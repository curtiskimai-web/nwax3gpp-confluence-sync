"""
페이지 ID 직접 지정으로 재파싱 후 업데이트
- title 매칭 없이 page_id로 바로 업데이트
- 21개 단일<p> 문제 페이지 대상
"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

Path("./logs").mkdir(exist_ok=True)

from main import (
    parse_docx, parse_doc, format_confluence_page,
    merge_parsed_docs, _extract_doc_type_and_title,
    _extract_spec_number, _sanitize_title, logger,
)
from atlassian import Confluence

conf = Confluence(
    url=os.environ["CONFLUENCE_URL"],
    username=os.environ["CONFLUENCE_USER"],
    password=os.environ["CONFLUENCE_API_TOKEN"],
    cloud=True,
)

MAX_BODY_BYTES = 4_000_000

# page_id → zip_stem 매핑
TARGETS = {
    # .docx - 재파싱 가능
    "177266": "38212-j20",
    "113261": "38213-j20",
    "143479": "38214-j20",
    "81159":  "38321-j10",
    "177188": "38331-j10",
    "80990":  "38455-j10",
    "80877":  "38473-j10",
    # .doc
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


def truncate_body(merged: dict, zip_name: str) -> str:
    body = format_confluence_page(merged, zip_name)
    if len(body.encode("utf-8")) <= MAX_BODY_BYTES:
        return body
    logger.warning(f"  Body {len(body.encode('utf-8'))//1024}KB > limit, truncating...")
    sections = list(merged.get("sections", []))
    while sections and len(body.encode("utf-8")) > MAX_BODY_BYTES:
        sections = sections[:len(sections) * 3 // 4]
        merged["sections"] = sections
        body = format_confluence_page(merged, zip_name)
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        full_text = merged.get("full_text", "")
        limit = 5000
        while len(body.encode("utf-8")) > MAX_BODY_BYTES and limit >= 0:
            merged["full_text"] = full_text[:limit]
            body = format_confluence_page(merged, zip_name)
            limit = limit // 2
    logger.info(f"  Final: {len(body.encode('utf-8'))//1024}KB ({len(merged.get('sections', []))} sections)")
    return body


def main():
    extracted_base = Path("./extracted")
    total = len(TARGETS)
    success, failed, skipped = [], [], []

    for i, (page_id, zip_stem) in enumerate(TARGETS.items(), 1):
        extract_dir = extracted_base / zip_stem
        if not extract_dir.exists():
            logger.warning(f"[{i}/{total}] Not found: {zip_stem}")
            skipped.append(zip_stem)
            continue

        spec_no = _extract_spec_number(zip_stem)
        zip_name = zip_stem + ".zip"
        logger.info(f"[{i}/{total}] {zip_stem} (Spec: {spec_no}, PageID: {page_id})")

        doc_files = sorted(
            f for f in extract_dir.iterdir()
            if f.suffix.lower() in (".doc", ".docx")
            and "presentation" not in f.name.lower()
        )
        if not doc_files:
            logger.warning(f"  No doc files, skipping")
            skipped.append(zip_stem)
            continue

        parsed_list = []
        for doc_path in doc_files:
            try:
                if doc_path.suffix.lower() == ".docx":
                    parsed = parse_docx(doc_path)
                else:
                    parsed = parse_doc(doc_path)
                parsed_list.append(parsed)
                logger.info(f"  Parsed: {doc_path.name}")
            except Exception as e:
                logger.error(f"  Parse failed {doc_path.name}: {e}")

        if not parsed_list:
            logger.warning(f"  All parsing failed, skipping")
            skipped.append(zip_stem)
            continue

        # 기존 페이지 제목 가져오기 (제목은 변경하지 않음)
        try:
            existing = conf.get_page_by_id(page_id, expand="version,title")
            existing_title = existing["title"]
            existing_version = existing["version"]["number"]
        except Exception as e:
            logger.error(f"  Cannot get page {page_id}: {e}")
            failed.append((zip_stem, str(e)))
            continue

        doc_type, doc_desc = _extract_doc_type_and_title(parsed_list, zip_stem)
        merged = merge_parsed_docs(parsed_list, spec_no, doc_type)
        merged["title"] = existing_title  # 기존 제목 유지
        body = truncate_body(merged, zip_name)

        logger.info(f"  Updating [{page_id}] v{existing_version}: {existing_title[:60]}")
        try:
            conf.update_page(
                page_id=page_id,
                title=existing_title,
                body=body,
                representation="storage",
            )
            logger.info(f"  OK: {existing_title[:60]}")
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
