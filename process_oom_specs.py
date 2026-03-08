"""
OOM 위험 대형 스펙 처리 스크립트
38133-j30 (22파일/36MB), 38523-1-j30 (12파일/30MB)
파일 크기 누적 한도를 두어 OOM 방지
"""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

Path("./logs").mkdir(exist_ok=True)

from main import (
    parse_docx,
    parse_doc,
    format_confluence_page,
    merge_parsed_docs,
    ConfluencePublisher,
    _extract_doc_type_and_title,
    _extract_spec_number,
    _sanitize_title,
    logger,
)

# 누적 파일 크기 한도 (bytes) — 이 이하의 파일들만 파싱
MAX_PARSE_BYTES = 10 * 1024 * 1024  # 10MB

MAX_BODY_BYTES = 4_000_000

OOM_STEMS = ["38133-j30", "38523-1-j30"]


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
    success, failed = [], []

    for stem in OOM_STEMS:
        extract_dir = extracted_base / stem
        spec_no = _extract_spec_number(stem)
        zip_name = stem + ".zip"
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing OOM spec: {stem} (Spec: {spec_no})")

        all_files = sorted(
            f for f in extract_dir.iterdir()
            if f.suffix.lower() in (".doc", ".docx")
            and "presentation" not in f.name.lower()
        )

        # 누적 크기 한도로 파일 선택
        selected, cumulative = [], 0
        for f in all_files:
            size = f.stat().st_size
            if cumulative + size > MAX_PARSE_BYTES and selected:
                logger.info(f"  Size limit reached at {cumulative//1024//1024}MB, stopping at {f.name}")
                break
            selected.append(f)
            cumulative += size

        logger.info(f"  Parsing {len(selected)}/{len(all_files)} files ({cumulative//1024//1024}MB)")

        parsed_list = []
        for doc_path in selected:
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
            failed.append((stem, "All parsing failed"))
            continue

        doc_type, doc_desc = _extract_doc_type_and_title(parsed_list, stem)
        page_title = f"{doc_type} {spec_no} - {doc_desc}" if doc_desc else f"{doc_type} {spec_no}"
        page_title = _sanitize_title(page_title, f"{doc_type} {spec_no}")

        merged = merge_parsed_docs(parsed_list, spec_no, doc_type)
        merged["title"] = page_title

        # 파일 일부만 파싱했음을 명시
        skipped_count = len(all_files) - len(selected)
        if skipped_count > 0:
            merged["sections"] = merged.get("sections", [])
            note = f"[참고: 메모리 제한으로 {len(all_files)}개 파일 중 {len(selected)}개만 파싱됨. {skipped_count}개 파일(주로 Annex) 생략]"
            merged["sections"].insert(0, {
                "level": 1, "heading": "처리 안내",
                "content": note, "tables": []
            })

        body = truncate_body(merged, zip_name)
        labels = ["3gpp", "specification", doc_type.lower(), f"spec-{spec_no.replace('.', '-')}"]

        logger.info(f"  Publishing: {page_title}")
        try:
            publisher.upsert_page(
                title=page_title, body=body,
                parent_id=parent_page_id, labels=labels,
            )
            logger.info(f"  OK: {page_title}")
            success.append(stem)
        except Exception as e:
            logger.error(f"  Publish failed: {e}", exc_info=True)
            failed.append((stem, str(e)[:100]))

    logger.info(f"\n{'='*60}")
    logger.info(f"Done: {len(success)}/{len(OOM_STEMS)} succeeded")
    if failed:
        logger.warning("Failed:")
        for name, err in failed:
            logger.warning(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
