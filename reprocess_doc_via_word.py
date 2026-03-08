"""
Microsoft Word COM 자동화로 .doc 파일을 구조적으로 파싱 후 Confluence 업데이트
- 제목1/2/3/4 → h2/h3/h4/h5
- 표준 → <p>
- 각주/참조/NOTE 등 보조 스타일 처리
- TOC 항목 스킵
"""
import os, sys, re, html
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

Path("./logs").mkdir(exist_ok=True)

from main import (
    format_confluence_page, merge_parsed_docs,
    _extract_spec_number, logger,
)
from atlassian import Confluence
import win32com.client

conf = Confluence(
    url=os.environ["CONFLUENCE_URL"],
    username=os.environ["CONFLUENCE_USER"],
    password=os.environ["CONFLUENCE_API_TOKEN"],
    cloud=True,
)

MAX_BODY_BYTES = 4_000_000

# .doc 페이지 ID → zip_stem 매핑 (재파싱 대상)
TARGETS = {
    "178372": "38801-e00",
    "178393": "38802-e20",
    "178423": "38804-e00",
    "82190":  "38806-f00",
    "82210":  "38807-g10",
    "178445": "38811-f40",
    "178486": "38821-g20",
    "114415": "38824-g00",
    "144431": "38826-g00",
    "144508": "38855-g00",
    "114565": "38866-g10",
    "178599": "38873-g00",
    "114596": "38874-g00",
    "178695": "38900-f00",
}

# 스타일 이름 → heading 레벨 (0 = 본문)
def get_heading_level(style_name: str) -> int:
    s = style_name.lower()
    # 제목 1, 제목 2 ... or Heading 1 ...
    for kw, lv in [("제목 1", 1), ("제목 2", 2), ("제목 3", 3), ("제목 4", 4),
                    ("제목 5", 5), ("제목 6", 6), ("제목 7", 7), ("제목 8", 8), ("제목 9", 9)]:
        if style_name.startswith(kw):
            return lv
    for kw, lv in [("heading 1", 1), ("heading 2", 2), ("heading 3", 3), ("heading 4", 4)]:
        if kw in s:
            return lv
    return 0


def is_toc_style(style_name: str) -> bool:
    return style_name.startswith("목차") or "toc" in style_name.lower()


def is_skip_style(style_name: str) -> bool:
    skip = {"ZA", "ZB", "ZT", "ZU", "ZV", "TT"}  # 표지, 목차제목
    return style_name.split(",")[0].strip() in skip


def parse_doc_word_com(doc_path: Path) -> dict:
    """Word COM으로 .doc 파싱 → sections 구조 반환"""
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = False

    sections = []
    current_section = None
    full_text_parts = []

    try:
        doc = word.Documents.Open(str(doc_path.resolve()), ReadOnly=True, ConfirmConversions=False)
        total_paras = doc.Paragraphs.Count
        logger.info(f"    Word COM: {total_paras} paragraphs in {doc_path.name}")

        for para in doc.Paragraphs:
            raw = para.Range.Text
            text = raw.rstrip("\r\n\x07\x0b").strip()
            style = para.Style.NameLocal

            if not text:
                continue
            if is_toc_style(style):
                continue
            if is_skip_style(style):
                continue
            # TOC 필드 코드 스킵
            if re.match(r'^TOC\\|^PAGEREF\s+_Toc|^\s*\\[a-z]', text):
                continue
            # 특수문자만으로 구성된 줄 스킵
            if re.match(r'^[\s\-_=]{3,}$', text):
                continue

            level = get_heading_level(style)
            esc_text = html.escape(text)

            if level > 0:
                # heading 레벨을 h2~h5로 매핑 (h1은 페이지 제목용)
                h = min(level + 1, 5)
                if current_section:
                    sections.append(current_section)
                current_section = {
                    "heading": text,
                    "level": level,
                    "content": f"<h{h}>{esc_text}</h{h}>\n",
                }
                full_text_parts.append(text)
            else:
                content_line = f"<p>{esc_text}</p>\n"
                if current_section is None:
                    current_section = {"heading": "", "level": 0, "content": ""}
                current_section["content"] += content_line
                full_text_parts.append(text)

        if current_section:
            sections.append(current_section)

        doc.Close(False)
    finally:
        word.Quit()

    return {
        "title": doc_path.stem,
        "sections": sections,
        "full_text": "\n".join(full_text_parts),
    }


def build_confluence_body(parsed: dict, page_title: str, zip_name: str) -> str:
    esc = html.escape
    body = f'<ac:structured-macro ac:name="toc" ac:schema-version="1"/>\n'
    body += f'''
<ac:structured-macro ac:name="info" ac:schema-version="1">
<ac:parameter ac:name="title">{esc(page_title)}</ac:parameter>
<ac:rich-text-body><p>Source: 3GPP 38 Series Specification</p></ac:rich-text-body>
</ac:structured-macro>\n'''

    for sec in parsed.get("sections", []):
        content = sec.get("content", "").strip()
        if not content:
            continue
        body += (
            f'<table style="width:100%;border-collapse:collapse;border:none">'
            f'<tbody><tr><td style="padding:4px 0;border:none;word-break:break-word">'
            f'{content}'
            f'</td></tr></tbody></table>\n'
        )

    return body


def truncate_body(body: str) -> str:
    if len(body.encode("utf-8")) <= MAX_BODY_BYTES:
        return body
    logger.warning(f"  Body {len(body.encode('utf-8'))//1024}KB > limit, truncating...")
    # 섹션 단위로 자르기 어려우므로 뒤에서 자르기
    while len(body.encode("utf-8")) > MAX_BODY_BYTES:
        body = body[:int(len(body) * 0.75)]
    return body


def main():
    extracted_base = Path("./extracted")
    total = len(TARGETS)
    success, failed, skipped = [], [], []

    for i, (page_id, zip_stem) in enumerate(TARGETS.items(), 1):
        extract_dir = extracted_base / zip_stem
        doc_files = sorted(
            f for f in extract_dir.iterdir()
            if f.suffix.lower() == ".doc"
            and "presentation" not in f.name.lower()
        ) if extract_dir.exists() else []

        if not doc_files:
            logger.warning(f"[{i}/{total}] No .doc: {zip_stem}")
            skipped.append(zip_stem)
            continue

        # 기존 페이지 제목 가져오기
        try:
            cur = conf.get_page_by_id(page_id, expand="version,title")
            page_title = cur["title"]
        except Exception as e:
            logger.error(f"  Cannot get page {page_id}: {e}")
            failed.append((zip_stem, str(e)))
            continue

        spec_no = _extract_spec_number(zip_stem)
        zip_name = zip_stem + ".zip"
        logger.info(f"[{i}/{total}] {zip_stem} (PageID: {page_id}) - {page_title[:50]}")

        doc_path = doc_files[0]
        try:
            parsed = parse_doc_word_com(doc_path)
            n_sections = len(parsed.get("sections", []))
            logger.info(f"  Parsed: {n_sections} sections")
        except Exception as e:
            logger.error(f"  Parse failed: {e}", exc_info=True)
            failed.append((zip_stem, str(e)[:100]))
            continue

        body = build_confluence_body(parsed, page_title, zip_name)
        body = truncate_body(body)
        logger.info(f"  Body: {len(body.encode('utf-8'))//1024}KB")

        try:
            conf.update_page(
                page_id=page_id,
                title=page_title,
                body=body,
                representation="storage",
            )
            logger.info(f"  OK: {page_title[:60]}")
            success.append(zip_stem)
        except Exception as e:
            logger.error(f"  Update failed [{page_id}]: {e}", exc_info=True)
            failed.append((zip_stem, str(e)[:100]))

    logger.info("=" * 60)
    logger.info(f"Done: {len(success)}/{total} succeeded, {len(skipped)} skipped, {len(failed)} failed")
    if failed:
        for name, err in failed:
            logger.warning(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
