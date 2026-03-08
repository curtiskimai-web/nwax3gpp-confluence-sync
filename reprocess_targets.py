"""
구버전 포맷 페이지 재처리 스크립트
info 매크로/투명 테이블이 없는 구버전 body를 신규 포맷으로 업데이트
"""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

Path("./logs").mkdir(exist_ok=True)

from main import (
    parse_docx, parse_doc, format_confluence_page,
    merge_parsed_docs, ConfluencePublisher,
    _extract_doc_type_and_title, _extract_spec_number,
    _sanitize_title, logger,
)

MAX_BODY_BYTES = 4_000_000

TARGET_STEMS = [
    "38885-g00", "38802-e20", "38811-f40", "38855-g00",
    "38821-g20", "38900-f00", "38824-g00", "38874-g00",
    "38801-e00", "38807-g10", "38804-e00", "38866-g10",
    "38873-g00", "38806-f00", "38826-g00", "38805-e00",
    "38819-g00", "38816-f00", "38173-001", "38475-030",
    "38201-j00",
]


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
    parent_page_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]
    publisher = ConfluencePublisher()
    total = len(TARGET_STEMS)
    success, failed, skipped = [], [], []

    for i, zip_stem in enumerate(TARGET_STEMS, 1):
        extract_dir = extracted_base / zip_stem
        if not extract_dir.exists():
            logger.warning(f"[{i}/{total}] Not found: {zip_stem}")
            skipped.append(zip_stem)
            continue

        spec_no = _extract_spec_number(zip_stem)
        zip_name = zip_stem + ".zip"
        logger.info(f"[{i}/{total}] {zip_stem} (Spec: {spec_no})")

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
            except Exception as e:
                logger.error(f"  Parse failed {doc_path.name}: {e}")

        if not parsed_list:
            logger.warning(f"  All parsing failed, skipping")
            skipped.append(zip_stem)
            continue

        doc_type, doc_desc = _extract_doc_type_and_title(parsed_list, zip_stem)
        page_title = f"{doc_type} {spec_no} - {doc_desc}" if doc_desc else f"{doc_type} {spec_no}"
        page_title = _sanitize_title(page_title, f"{doc_type} {spec_no}")

        merged = merge_parsed_docs(parsed_list, spec_no, doc_type)
        merged["title"] = page_title
        body = truncate_body(merged, zip_name)
        labels = ["3gpp", "specification", doc_type.lower(), f"spec-{spec_no.replace('.', '-')}"]

        try:
            publisher.upsert_page(
                title=page_title, body=body,
                parent_id=parent_page_id, labels=labels,
            )
            success.append(zip_stem)
        except Exception as e:
            logger.error(f"  Publish failed: {e}", exc_info=True)
            failed.append((zip_stem, str(e)[:100]))

    logger.info("=" * 60)
    logger.info(f"Done: {len(success)}/{total} succeeded, {len(skipped)} skipped, {len(failed)} failed")
    if failed:
        for name, err in failed:
            logger.warning(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
