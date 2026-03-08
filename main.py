"""
3GPP FTP Doc → Confluence Auto-Sync

파이프라인:
1. Google Drive에서 zip 파일 다운로드
2. zip 압축 해제 → doc/docx 추출
3. doc/docx 내용 AI 검색 최적화 파싱
4. Confluence 페이지 계층 구조로 퍼블리싱
"""

import os
import io
import re
import json
import zipfile
import logging
import html
import time
import hashlib
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

Path("./logs").mkdir(exist_ok=True)

# ── Google Drive ──
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ── Doc 파싱 ──
import docx
import mammoth

# ── Confluence ──
from atlassian import Confluence
from tenacity import retry, stop_after_attempt, wait_exponential

# ── 로깅 설정 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("./logs/sync.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("3gpp-ftp")

GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# ─────────────────────────────────────────────
# Google Drive 인증 & 파일 다운로드
# ─────────────────────────────────────────────

def get_gdrive_service():
    """Service Account 인증으로 Google Drive 서비스 반환"""
    key_path = os.environ["GDRIVE_SERVICE_ACCOUNT_JSON"]
    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=GDRIVE_SCOPES
    )
    logger.info(f"Authenticated as service account: {creds.service_account_email}")
    return build("drive", "v3", credentials=creds)


def list_files_in_folder(service, folder_id: str) -> list[dict]:
    """폴더 내 모든 파일 목록 반환 (재귀, 공유 드라이브 포함)"""
    try:
        folder_info = service.files().get(
            fileId=folder_id,
            fields="id, name, mimeType",
            supportsAllDrives=True,
        ).execute()
        logger.info(f"Scanning folder: {folder_info.get('name')}")
    except Exception as e:
        logger.warning(f"Cannot access folder info: {e}")

    return _list_recursive(service, folder_id, "")


def _list_recursive(service, folder_id: str, prefix: str) -> list[dict]:
    """재귀적으로 모든 파일 탐색, 폴더 경로 정보 포함"""
    items = []
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None

    while True:
        resp = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, size)",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        batch = resp.get("files", [])
        items.extend(batch)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    files = []
    for item in items:
        item["folder_path"] = f"{prefix}/{item['name']}" if prefix else item["name"]
        if item["mimeType"] == "application/vnd.google-apps.folder":
            logger.info(f"  Subfolder: {item['folder_path']}")
            files.extend(_list_recursive(service, item["id"], item["folder_path"]))
        else:
            logger.info(f"  File: {item['folder_path']}")
            files.append(item)

    return files


def download_file(service, file_id: str, dest_path: Path):
    """Google Drive 파일 다운로드 (대용량 파일 abuse 확인 포함)"""
    request = service.files().get_media(fileId=file_id)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with io.FileIO(str(dest_path), "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
    except Exception as e:
        # 403 cannotDownloadAbusiveFile: 대용량 파일 바이러스 스캔 확인 필요
        if "cannotDownloadAbusiveFile" in str(e) or "403" in str(e):
            logger.warning(f"Retrying with acknowledgeAbuse=True: {dest_path.name}")
            request2 = service.files().get_media(fileId=file_id, acknowledgeAbuse=True)
            with io.FileIO(str(dest_path), "wb") as f:
                downloader = MediaIoBaseDownload(f, request2)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
        else:
            raise
    logger.info(f"Downloaded: {dest_path.name}")


# ─────────────────────────────────────────────
# zip 압축 해제
# ─────────────────────────────────────────────

def extract_zip(zip_path: Path, extract_dir: Path) -> list[Path]:
    """zip 파일에서 doc/docx 추출, 경로 목록 반환"""
    doc_files = []
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            lower = name.lower()
            if lower.endswith(".docx") or lower.endswith(".doc"):
                # 경로의 한글/특수문자 처리
                try:
                    decoded_name = name.encode("cp437").decode("euc-kr")
                except Exception:
                    decoded_name = name

                dest = extract_dir / Path(decoded_name).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                doc_files.append(dest)
                logger.info(f"  Extracted: {dest.name}")

    return doc_files


# ─────────────────────────────────────────────
# doc/docx 파싱 (AI 검색 최적화)
# ─────────────────────────────────────────────

def parse_docx(file_path: Path) -> dict:
    """
    .docx 파일 파싱 → AI 검색 최적화 구조 반환

    전략:
    - 제목(Heading) 계층 보존 → Confluence 섹션 분리
    - 표(Table) → 구조화된 텍스트
    - 단락 → 섹션별 묶음
    - 메타데이터 추출
    """
    doc = docx.Document(str(file_path))

    result = {
        "title": "",
        "metadata": {},
        "sections": [],   # [{"level": int, "heading": str, "content": str, "tables": []}]
        "full_text": "",
    }

    # 메타데이터
    core = doc.core_properties

    def _clean_meta(val: str) -> str:
        """Word 템플릿 플레이스홀더('<...>') 및 빈 값 제거"""
        if not val:
            return ""
        # <...> 패턴만 있는 경우 (미작성 템플릿)
        if re.fullmatch(r"[\s<>a-zA-Z0-9;,|\[\]\s\.]+", val) and "<" in val and ">" in val:
            return ""
        return val.strip()

    result["metadata"] = {
        "author": _clean_meta(core.author or ""),
        "created": str(core.created or ""),
        "modified": str(core.modified or ""),
        "subject": _clean_meta(core.subject or ""),
        "keywords": _clean_meta(core.keywords or ""),
    }

    # 첫 번째 제목을 문서 타이틀로
    for para in doc.paragraphs:
        if para.text.strip():
            result["title"] = para.text.strip()
            break

    # 섹션 구조 파싱
    current_section = {"level": 0, "heading": result["title"], "content": [], "tables": []}
    sections = []
    full_text_parts = []

    para_index = 0
    for block in _iter_block_items(doc):
        if isinstance(block, docx.text.paragraph.Paragraph):
            para = block
            text = para.text.strip()
            if not text:
                continue

            style_name = para.style.name.lower() if para.style else ""

            # ToC 스타일 단락 스킵 (목차 항목 - 페이지 번호 포함, Confluence에서 불필요)
            if re.match(r"toc\s*\d*|table of contents|목차", style_name):
                continue

            heading_level = _get_heading_level(style_name)

            if heading_level:
                # 이전 섹션 저장
                if current_section["content"] or current_section["tables"]:
                    sections.append({
                        "level": current_section["level"],
                        "heading": current_section["heading"],
                        "content": "\n".join(current_section["content"]),
                        "tables": current_section["tables"],
                    })
                current_section = {"level": heading_level, "heading": text, "content": [], "tables": []}
            else:
                current_section["content"].append(text)

            full_text_parts.append(text)

        elif isinstance(block, docx.table.Table):
            table_data = _parse_table(block)
            current_section["tables"].append(table_data)
            # 테이블 텍스트도 full_text에 포함 (text만 추출)
            for row in table_data:
                full_text_parts.append(" | ".join(text for text, _ in row))

    # 마지막 섹션 저장
    if current_section["content"] or current_section["tables"]:
        sections.append({
            "level": current_section["level"],
            "heading": current_section["heading"],
            "content": "\n".join(current_section["content"]),
            "tables": current_section["tables"],
        })

    result["sections"] = sections
    result["full_text"] = "\n".join(full_text_parts)
    return result


def parse_doc(file_path: Path) -> dict:
    """.doc 파일 파싱 (mammoth → olefile 순서로 폴백)"""

    # 1차: mammoth 시도 (.doc가 실제로 docx 포맷인 경우)
    try:
        with open(file_path, "rb") as f:
            result_mammoth = mammoth.convert_to_html(f)
        html_content = result_mammoth.value
        if html_content.strip():
            return _parse_html_to_struct(html_content, file_path.stem)
    except Exception:
        pass

    # 2차: olefile로 바이너리 .doc 텍스트 추출
    try:
        import olefile
        text = _extract_text_from_ole(file_path)
        if text:
            return _plain_text_to_struct(text, file_path.stem)
    except Exception as e:
        logger.warning(f"olefile failed for {file_path.name}: {e}")

    # 3차: 바이너리에서 출력 가능한 텍스트만 추출
    logger.warning(f"Falling back to raw text extraction: {file_path.name}")
    text = _extract_printable_text(file_path)
    return _plain_text_to_struct(text, file_path.stem)


def _sanitize_text(text: str) -> str:
    """Confluence XML에 안전한 텍스트로 정제"""
    # XML에서 허용되지 않는 제어문자 제거 (탭, 개행 제외)
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    # 연속 공백/개행 정리
    text = re.sub(r" {3,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sanitize_title(title: str, fallback: str) -> str:
    """Confluence 페이지 제목으로 안전한 문자열 반환"""
    title = re.sub(r"[\x00-\x1F\x7F]", " ", title).strip()
    title = title[:200]
    if not title or len(title) < 3:
        return fallback
    # 깨진 유니코드 감지: 비ASCII 문자 비율이 40% 초과면 fallback
    non_ascii = sum(1 for c in title if ord(c) > 127)
    if non_ascii / max(len(title), 1) > 0.4:
        return fallback
    return title


def _filter_word_markup_lines(text: str) -> str:
    """Word 내부 마크업/필드 코드 라인 제거"""
    # Word 필드 코드 패턴
    FIELD_CODE = re.compile(r'\b(?:STYLEREF|MERGEFORMAT|HYPERLINK|EMBED|REF\s+\w+)\b')
    # Word 포맷 약어 패턴 (mH, nH, tH, CJ, OJ, QJ, KH, PJ, cH, dh, Zg, sH 등)
    MARKUP_TOKEN = re.compile(r'\b(?:mH|nH|tH|sH|CJ|OJ|QJ|KH|PJ|cH|dh|Zg|aJ)\b')
    # Word SPRM 레코드 패턴: "y  h[x]  mH  sH  h " 구조
    SPRM_PATTERN = re.compile(r'\bh\S*\s+(?:mH|nH|sH)\b')

    filtered = []
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped:
            filtered.append(line)
            continue
        # Word 필드 코드가 있는 줄 제거
        if FIELD_CODE.search(stripped):
            continue
        # Word 마크업 약어가 2개 이상이면 내부 마크업으로 판단, 제거
        if len(MARKUP_TOKEN.findall(stripped)) >= 2:
            continue
        # Word SPRM 레코드 패턴 (h[ref] mH sH 구조)
        if SPRM_PATTERN.search(stripped):
            continue
        # 짧은 줄(30자 미만)에 마크업 토큰이 있으면 제거
        if len(stripped) < 30 and MARKUP_TOKEN.search(stripped):
            continue
        # 공백 구분 토큰의 60% 이상이 1-2자이면 내부 마크업으로 판단
        tokens = stripped.split()
        if len(tokens) >= 6:
            short = sum(1 for t in tokens if len(t.strip('()[]!*.,;')) <= 2)
            if short / len(tokens) > 0.6:
                continue
        filtered.append(line)
    return '\n'.join(filtered)


def _extract_text_from_ole(file_path: Path) -> str:
    """OLE2 바이너리 .doc에서 텍스트 추출"""
    import olefile

    # ASCII 가독성 점수: 출력 가능한 ASCII 비율
    def _ascii_ratio(text: str) -> float:
        if not text:
            return 0.0
        printable = sum(1 for c in text if '\x20' <= c <= '\x7E' or c in '\t\n\r')
        return printable / len(text)

    with olefile.OleFileIO(str(file_path)) as ole:
        for stream in ["WordDocument", "1Table", "0Table"]:
            if not ole.exists(stream):
                continue
            try:
                data = ole.openstream(stream).read()

                # CP1252(단일바이트)와 UTF-16LE 모두 시도 → 더 읽기 좋은 것 선택
                candidates = []
                for enc in ("cp1252", "utf-16-le"):
                    try:
                        decoded = data.decode(enc, errors="ignore")
                        # CJK 제외: ASCII + 한글만 허용
                        cleaned = re.sub(r"[^\x09\x0A\x0D\x20-\x7E\uAC00-\uD7A3]", " ", decoded)
                        cleaned = _sanitize_text(cleaned)
                        # Word 내부 마크업 라인 제거
                        cleaned = _filter_word_markup_lines(cleaned)
                        cleaned = _sanitize_text(cleaned)
                        if len(cleaned) > 100:
                            candidates.append((cleaned, _ascii_ratio(cleaned)))
                    except Exception:
                        continue

                if candidates:
                    # ASCII 비율이 높은 결과 선택
                    best = max(candidates, key=lambda x: x[1])
                    text = best[0]
                    # OLE 바이너리 헤더 제거: 첫 번째 3GPP 마커 이전 내용 스킵
                    m = re.search(r"3GPP\s+(?:TS|TR)\b", text)
                    if m:
                        text = text[m.start():]
                    return text

            except Exception:
                continue

    return ""


def _extract_printable_text(file_path: Path) -> str:
    """바이너리 파일에서 출력 가능한 텍스트 추출 (최후 수단)"""
    with open(file_path, "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-16-le", errors="ignore")
        cleaned = re.sub(r"[^\x09\x0A\x0D\x20-\x7E\uAC00-\uD7A3]", " ", text)
        cleaned = _sanitize_text(cleaned)
        if len(cleaned) > 50:
            return cleaned
    except Exception:
        pass
    return _sanitize_text(re.sub(r"[^\x20-\x7E\n]", " ", data.decode("ascii", errors="ignore")))


def _parse_html_to_struct(html_content: str, stem: str) -> dict:
    """HTML → 구조화 dict 변환"""
    title = stem
    sections = []
    full_text_parts = []
    current_section = {"level": 0, "heading": title, "content": [], "tables": []}

    # XML 누출 감지 패턴 (drawingml, OpenXML 내부 스키마가 텍스트로 포함된 경우)
    XML_LEAK = re.compile(r'<\?xml\b|xmlns:|schemas\.openxmlformats\.org|schemas\.microsoft\.com')

    def strip_tags(s):
        return re.sub(r"<[^>]+>", "", s).strip()

    for m in re.finditer(r"<(h[1-6]|p|table)[\s>].*?</\1>", html_content, re.IGNORECASE | re.DOTALL):
        tag = m.group(1).lower()
        content = strip_tags(m.group(0))
        if not content:
            continue
        # XML 내부 스키마 누출 콘텐츠 제거
        if XML_LEAK.search(content):
            continue
        if tag.startswith("h"):
            level = int(tag[1])
            if current_section["content"]:
                sections.append({
                    "level": current_section["level"],
                    "heading": current_section["heading"],
                    "content": "\n".join(current_section["content"]),
                    "tables": current_section["tables"],
                })
            current_section = {"level": level, "heading": content, "content": [], "tables": []}
            if not title or title == stem:
                title = content
        elif tag == "p":
            current_section["content"].append(content)
        elif tag == "table":
            current_section["tables"].append(_parse_html_table(m.group(0)))
        full_text_parts.append(content)

    if current_section["content"] or current_section["tables"]:
        sections.append({
            "level": current_section["level"],
            "heading": current_section["heading"],
            "content": "\n".join(current_section["content"]),
            "tables": current_section["tables"],
        })

    return {"title": title, "metadata": {}, "sections": sections, "full_text": "\n".join(full_text_parts)}


def _plain_text_to_struct(text: str, stem: str) -> dict:
    """평문 텍스트 → 구조화 dict 변환"""
    text = _sanitize_text(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    raw_title = lines[0] if lines else stem
    title = _sanitize_title(raw_title, stem)
    return {
        "title": title,
        "metadata": {},
        "sections": [{"level": 1, "heading": title, "content": "\n".join(lines[1:]), "tables": []}],
        "full_text": text,
    }


def _iter_block_items(doc):
    """문서의 단락과 표를 순서대로 yield"""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    parent = doc.element.body
    for child in parent.iterchildren():
        if child.tag == qn("w:p"):
            yield docx.text.paragraph.Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield docx.table.Table(child, doc)


def _get_heading_level(style_name: str) -> Optional[int]:
    """스타일명에서 헤딩 레벨 추출"""
    m = re.match(r"heading\s*(\d)", style_name)
    if m:
        return int(m.group(1))
    # 한글 워드 스타일명 처리
    korean_map = {"제목 1": 1, "제목 2": 2, "제목 3": 3, "제목 4": 4}
    for k, v in korean_map.items():
        if k in style_name:
            return v
    return None


def _parse_table(table) -> list[list[tuple]]:
    """docx 표 → 2D 리스트: [(text, colspan), ...]
    python-docx는 병합 셀을 동일한 _tc 객체로 반환 → colspan 계산
    """
    rows = []
    for row in table.rows:
        result: list[tuple[str, int]] = []
        prev_tc = None
        for cell in row.cells:
            if cell._tc is prev_tc:
                # 이전 셀의 colspan 증가
                result[-1] = (result[-1][0], result[-1][1] + 1)
            else:
                result.append((cell.text.strip(), 1))
                prev_tc = cell._tc
        rows.append(result)
    return rows


def _parse_html_table(html_str: str) -> list[list[tuple]]:
    """HTML 표 → 2D 리스트: [(text, colspan), ...] (colspan=1 고정)"""
    rows = []
    for row_m in re.finditer(r"<tr>(.*?)</tr>", html_str, re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_m.group(1), re.IGNORECASE | re.DOTALL)
        rows.append([(re.sub(r"<[^>]+>", "", c).strip(), 1) for c in cells])
    return rows


# ─────────────────────────────────────────────
# Confluence Storage Format 변환
# ─────────────────────────────────────────────

def format_confluence_page(parsed: dict, file_name: str) -> str:
    """파싱된 문서 → Confluence Storage Format HTML"""
    esc = html.escape
    content = []

    # TOC 매크로
    content.append('<ac:structured-macro ac:name="toc" ac:schema-version="1"/>')

    # 메타데이터 패널
    meta = parsed.get("metadata", {})
    meta_rows = "".join(
        f"<tr><th>{esc(k.title())}</th><td>{esc(str(v))}</td></tr>"
        for k, v in meta.items() if v
    )
    if meta_rows:
        content.append(f"""
<ac:structured-macro ac:name="info" ac:schema-version="1">
<ac:parameter ac:name="title">{esc(parsed.get('title', file_name))}</ac:parameter>
<ac:rich-text-body>
<table style="width:100%;border-collapse:collapse"><tbody>{meta_rows}</tbody></table>
</ac:rich-text-body>
</ac:structured-macro>""")

    # 섹션별 내용
    for section in parsed.get("sections", []):
        level = min(max(section["level"], 1), 4)
        heading = section["heading"]
        body_text = section["content"]
        tables = section["tables"]

        if heading:
            content.append(f"<h{level}>{esc(heading)}</h{level}>")

        if body_text:
            # 이중 줄바꿈 → <p> 단락 구분, 단일 줄바꿈 → <br/> 줄바꿈 유지
            # 본문도 테이블과 동일하게 table 구조로 감싸
            # → Confluence가 table-wrap CSS를 동일하게 적용, 넓이 통일
            import re as _re
            text_blocks = []
            for block in _re.split(r'\n{2,}', body_text):
                block = block.strip()
                if block:
                    lines = [esc(line.strip()) for line in block.splitlines() if line.strip()]
                    if lines:
                        text_blocks.append(f"<p>{'<br/>'.join(lines)}</p>")
            if text_blocks:
                inner = "".join(text_blocks)
                content.append(
                    f'<table style="width:100%;border-collapse:collapse;border:none;background-color:transparent"><tbody>'
                    f'<tr><td style="padding:0;border:none;word-break:break-word;background-color:transparent">{inner}</td></tr>'
                    f'</tbody></table>'
                )

        for table_data in tables:
            if not table_data:
                continue
            table_html = ['<table style="width:100%;border-collapse:collapse;table-layout:auto"><tbody>']
            for i, row in enumerate(table_data):
                tag = "th" if i == 0 else "td"
                cell_style = 'style="padding:6px 10px;border:1px solid #ddd;vertical-align:top;word-break:break-word"'
                cells = ""
                for text, colspan in row:
                    cs = f' colspan="{colspan}"' if colspan > 1 else ""
                    cell_html = "<br/>".join(esc(line) for line in text.split("\n"))
                    cells += f"<{tag}{cs} {cell_style}>{cell_html}</{tag}>"
                table_html.append(f"<tr>{cells}</tr>")
            table_html.append("</tbody></table>")
            content.append("\n".join(table_html))

    # 전체 텍스트 (검색 인덱싱용, 접기)
    full_text = parsed.get("full_text", "")
    if full_text:
        truncated = full_text[:15000] if len(full_text) > 15000 else full_text
        content.append(f"""
<ac:structured-macro ac:name="expand" ac:schema-version="1">
<ac:parameter ac:name="title">Full Text (for search indexing)</ac:parameter>
<ac:rich-text-body>{"".join(f"<p>{esc(l)}</p>" for l in truncated.split(chr(10)) if l.strip())}</ac:rich-text-body>
</ac:structured-macro>""")

    return "\n".join(content)


# ─────────────────────────────────────────────
# Confluence 퍼블리셔
# ─────────────────────────────────────────────

class ConfluencePublisher:
    def __init__(self):
        self.client = Confluence(
            url=os.environ["CONFLUENCE_URL"],
            username=os.environ["CONFLUENCE_USER"],
            password=os.environ["CONFLUENCE_API_TOKEN"],
            cloud=True,
        )
        self.space_key = os.environ["CONFLUENCE_SPACE_KEY"]
        self.parent_page_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]
        self._cache = {}

    def get_or_create_page_id(self, title: str, parent_id: str, body: str = "<p></p>") -> str:
        """페이지 ID 조회, 없으면 생성"""
        if title in self._cache:
            return self._cache[title]
        page_id = self.client.get_page_id(space=self.space_key, title=title)
        if not page_id:
            result = self.client.create_page(
                space=self.space_key,
                title=title,
                body=body,
                parent_id=parent_id,
                representation="storage",
            )
            page_id = result["id"]
            logger.info(f"Created group page: {title} (id={page_id})")
        self._cache[title] = page_id
        return page_id

    def _get_child_page_id(self, parent_id: str, title: str) -> Optional[str]:
        """부모 페이지 하위에서만 제목으로 페이지 ID 검색"""
        try:
            children = self.client.get_child_pages(parent_id)
            for child in (children or []):
                if child.get("title") == title:
                    return child["id"]
        except Exception:
            pass
        return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def upsert_page(self, title: str, body: str, parent_id: str, labels: list[str] = None) -> str:
        """페이지 생성 또는 업데이트 (부모 하위에서만 검색)"""
        existing_id = self._get_child_page_id(parent_id, title)
        if existing_id:
            self.client.update_page(
                page_id=existing_id,
                title=title,
                body=body,
                representation="storage",
            )
            logger.info(f"Updated: {title}")
            page_id = existing_id
        else:
            result = self.client.create_page(
                space=self.space_key,
                title=title,
                body=body,
                parent_id=parent_id,
                representation="storage",
            )
            page_id = result["id"]
            logger.info(f"Created: {title} (id={page_id})")

        if labels and page_id:
            time.sleep(0.5)
            for label in labels:
                try:
                    self.client.set_page_label(page_id, label.lower().replace(" ", "-"))
                except Exception as e:
                    logger.warning(f"Label failed '{label}': {e}")

        time.sleep(0.5)  # rate limiting
        return page_id


# ─────────────────────────────────────────────
# 메인 파이프라인
# ─────────────────────────────────────────────

def _extract_spec_number(zip_stem: str) -> str:
    """zip 파일명에서 표준 spec 번호 추출 (38903-j10 → 38.903, 38523-1-j30 → 38.523-1)"""
    m = re.match(r"^(\d{2})(\d{3})(-\d+)?", zip_stem)
    if m:
        return f"{m.group(1)}.{m.group(2)}{m.group(3) or ''}"
    return zip_stem


def _extract_doc_type_and_title(parsed_list: list[dict], zip_stem: str = "") -> tuple[str, str]:
    """
    파싱된 문서 목록에서 문서 유형(TS/TR)과 제목 추출
    """
    doc_type = "TS"
    best_title = ""

    combined_text = ""
    for parsed in parsed_list:
        combined_text += parsed.get("full_text", "") + " " + parsed.get("title", "") + " "

    # 테이블 행의 " | <duplicate>" 제거: parse_docx가 테이블 셀을 " | "로 조인하므로
    # "NR; | NR;" → "NR;" 으로 정규화하여 패턴 매칭 안정화
    combined_text = re.sub(r" \| [^\n]*", "", combined_text)

    # TS vs TR 판별
    if re.search(r"Technical Report|3GPP TR\b", combined_text[:2000]):
        doc_type = "TR"
    elif re.search(r"Technical Specification|3GPP TS\b", combined_text[:2000]):
        doc_type = "TS"

    # 패턴 1: TSG 라인 이후 실제 문서 제목 추출
    # 구조: "Technical Specification Group Radio Access Network;\nNR;\n[제목 라인들]\n(Release XX)"
    # 제목이 여러 줄일 수도 있고, 마지막 줄에 세미콜론이 없을 수도 있음
    m = re.search(
        r"(?:Radio Access Network|Radio access network|RAN)[^\n]*\n+"
        r"(?:(?:NR|LTE|E-UTRA|5G)[^\n]*\n+)?"
        r"(.+?)\n\s*\(Release",
        combined_text,
        re.DOTALL
    )
    if m:
        candidate = m.group(1).strip()
        # 여러 줄 제목 정리: ";\\n" → "; ", 나머지 줄바꿈 → 공백
        candidate = re.sub(r";\s*\n\s*", "; ", candidate)
        candidate = re.sub(r"\n\s*", " ", candidate)
        candidate = candidate.rstrip(";").strip()
        # 끝에 붙은 "Technical Report/Specification" 제거 (별도 줄이 병합된 경우)
        candidate = re.sub(r"\s+Technical (?:Report|Specification)\s*$", "", candidate, flags=re.IGNORECASE).strip()
        # 잘못된 매칭 필터: "(Release"로 시작하거나 URL 포함 또는 "Radio Access Network" 포함
        if (len(candidate) > 10
                and not candidate.startswith("(Release")
                and not re.search(r"https?://|Radio Access Network", candidate, re.IGNORECASE)):
            best_title = candidate

    # 패턴 2: NR; 또는 LTE; 바로 다음 줄 제목
    if not best_title:
        m = re.search(r"(?:^|\n)(?:NR|E-UTRA|LTE|5G);\s*\n(.+?)(?:;|\n\n|\(Release)",
                      combined_text, re.DOTALL)
        if m:
            candidate = m.group(1).strip().split("\n")[0]
            if len(candidate) > 10:
                best_title = candidate

    # 패턴 3: Scope 섹션 첫 문장
    if not best_title:
        m = re.search(r"[Ss]cope\s*\n+(.{20,200}?)(?:\.|;|\n\n)", combined_text)
        if m:
            candidate = m.group(1).strip()
            # URL이나 "present document"로 시작하는 문장은 제목으로 부적합
            if not re.search(r"https?://|^The present document", candidate, re.IGNORECASE):
                best_title = candidate

    # 패턴 4: 의미있는 섹션 제목
    if not best_title:
        for parsed in parsed_list:
            for section in parsed.get("sections", []):
                h = section.get("heading", "").strip()
                if (h and len(h) > 15
                        and not re.match(r"^[\d\|\s]", h)
                        and not re.match(r"3GPP\s+(TS|TR)", h)
                        and not any(x in h for x in ["Contents", "Foreword",
                                    "Technical Report", "Technical Specification"])):
                    best_title = h
                    break
            if best_title:
                break

    # 정제
    best_title = re.sub(r"\s*\|.*", "", best_title)   # 파이프 이후 제거
    best_title = re.sub(r"[\t|]", " ", best_title)
    best_title = re.sub(r"\s{2,}", " ", best_title).strip()
    if len(best_title) > 120:
        best_title = best_title[:120].rsplit(" ", 1)[0] + "..."

    return doc_type, best_title


def merge_parsed_docs(parsed_list: list[dict], spec_no: str, doc_type: str) -> dict:
    """여러 docx 파싱 결과를 하나의 구조로 병합"""
    merged_sections = []
    merged_full_text = []
    metadata = {}

    for parsed in parsed_list:
        if parsed.get("metadata"):
            metadata.update({k: v for k, v in parsed["metadata"].items() if v})
        merged_sections.extend(parsed.get("sections", []))
        if parsed.get("full_text"):
            merged_full_text.append(parsed["full_text"])

    return {
        "title": "",  # main()에서 설정
        "metadata": metadata,
        "sections": merged_sections,
        "full_text": "\n\n".join(merged_full_text),
    }


def main():
    Path("./downloads").mkdir(exist_ok=True)
    Path("./extracted").mkdir(exist_ok=True)

    folder_id = os.environ["GDRIVE_FOLDER_ID"]
    parent_page_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]

    # ── Step 1: Google Drive 파일 목록 조회 ──
    logger.info("=" * 60)
    logger.info("[1/4] Connecting to Google Drive...")
    service = get_gdrive_service()
    files = list_files_in_folder(service, folder_id)
    zip_files = [f for f in files if f["name"].lower().endswith(".zip")]
    logger.info(f"Found {len(zip_files)} zip file(s)")

    publisher = ConfluencePublisher()

    for zip_info in zip_files:
        zip_name = zip_info["name"]
        zip_stem = Path(zip_name).stem
        spec_no = _extract_spec_number(zip_stem)

        logger.info(f"\n{'='*60}")
        logger.info(f"Processing: {zip_name} (Spec: {spec_no})")

        # ── Step 2: 다운로드 & 압축 해제 ──
        logger.info("[2/4] Downloading & extracting...")
        zip_path = Path("./downloads") / zip_name
        if not zip_path.exists() or zip_path.stat().st_size == 0:
            try:
                download_file(service, zip_info["id"], zip_path)
            except Exception as e:
                logger.error(f"  Download failed {zip_name}: {e}")
                continue
        else:
            logger.info(f"  Already downloaded: {zip_name}")

        extract_dir = Path("./extracted") / zip_stem
        try:
            doc_files = extract_zip(zip_path, extract_dir)
        except zipfile.BadZipFile as e:
            logger.error(f"  Bad zip file {zip_name}: {e}. Deleting corrupt file, skipping.")
            zip_path.unlink(missing_ok=True)
            continue
        logger.info(f"  Extracted {len(doc_files)} doc/docx file(s)")

        if not doc_files:
            logger.warning(f"  No doc/docx files found in {zip_name}")
            continue

        # ── Step 3: 파싱 (모든 docx 병합) ──
        logger.info(f"[3/4] Parsing {len(doc_files)} file(s)...")
        parsed_list = []
        for doc_path in sorted(doc_files):
            try:
                if doc_path.suffix.lower() == ".docx":
                    parsed = parse_docx(doc_path)
                else:
                    parsed = parse_doc(doc_path)
                parsed_list.append(parsed)
            except Exception as e:
                logger.error(f"  Parse failed {doc_path.name}: {e}")
                continue

        if not parsed_list:
            logger.warning(f"  All parsing failed for {zip_name}")
            continue

        # ── Step 4: 제목 결정 & Confluence 퍼블리싱 ──
        doc_type, doc_desc = _extract_doc_type_and_title(parsed_list)

        # 제목: "TS 38.523-1 - NR; UE conformance specification"
        if doc_desc:
            page_title = f"{doc_type} {spec_no} - {doc_desc}"
        else:
            page_title = f"{doc_type} {spec_no}"
        page_title = _sanitize_title(page_title, f"{doc_type} {spec_no}")

        merged = merge_parsed_docs(parsed_list, spec_no, doc_type)
        merged["title"] = page_title
        body = format_confluence_page(merged, zip_name)

        labels = ["3gpp", "specification", doc_type.lower(),
                  f"spec-{spec_no.replace('.', '-')}"]

        # Confluence 페이지 크기 제한: ~5MB (HTML 기준)
        MAX_BODY_BYTES = 4_000_000
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            logger.warning(f"  Body too large ({len(body.encode('utf-8'))//1024}KB), truncating sections...")
            # 1단계: 섹션을 줄여서 크기 제한 준수
            sections = merged.get("sections", [])
            while sections and len(body.encode("utf-8")) > MAX_BODY_BYTES:
                sections = sections[:len(sections) * 3 // 4]
                merged["sections"] = sections
                body = format_confluence_page(merged, zip_name)
            # 2단계: 섹션 제거 후에도 크면 full_text 단계적으로 축소
            if len(body.encode("utf-8")) > MAX_BODY_BYTES:
                logger.warning(f"  Still too large after sections removed, truncating full_text...")
                full_text = merged.get("full_text", "")
                limit = 5000
                while len(body.encode("utf-8")) > MAX_BODY_BYTES and limit >= 0:
                    merged["full_text"] = full_text[:limit]
                    body = format_confluence_page(merged, zip_name)
                    limit = limit // 2
            logger.info(f"  Truncated to {len(body.encode('utf-8'))//1024}KB ({len(merged.get('sections',[]))} sections)")

        logger.info(f"[4/4] Publishing: {page_title}")
        try:
            publisher.upsert_page(
                title=page_title,
                body=body,
                parent_id=parent_page_id,
                labels=labels,
            )
        except Exception as e:
            logger.error(f"  Publish failed {zip_name}: {e}", exc_info=True)

    logger.info("\n" + "=" * 60)
    logger.info("Pipeline finished successfully.")


if __name__ == "__main__":
    main()
