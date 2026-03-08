"""
이미 extracted된 모든 파일 재파싱 → Confluence 업데이트
(다운로드/Drive 접근 없이 로컬 파일만 사용)
"""
import os
import sys
import time
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

MAX_BODY_BYTES = 4_000_000

# 메모리 문제 등으로 별도 처리가 필요한 스펙 (스킵 후 수동 처리)
SKIP_STEMS = {"38133-j30", "38523-1-j30"}  # OOM 위험 — process_oom_specs.py로 별도 처리

# 특정 스펙부터 재시작 (빈 문자열이면 처음부터)
START_FROM = ""


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

    logger.info(f"  Final: {len(body.encode('utf-8'))//1024}KB ({len(merged.get('sections',[]))} sections)")
    return body


def main():
    extracted_base = Path("./extracted")
    parent_page_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]

    all_dirs = sorted(extracted_base.iterdir())
    # START_FROM 이후 디렉토리만 처리
    if START_FROM:
        all_dirs = [d for d in all_dirs if d.name >= START_FROM]
    # SKIP_STEMS 제외
    dirs = [d for d in all_dirs if d.name not in SKIP_STEMS]
    total = len(dirs)
    logger.info(f"Reprocessing {total} directories (start={START_FROM or 'beginning'}, skip={SKIP_STEMS})")

    publisher = ConfluencePublisher()
    success, failed, skipped = [], [], []

    for i, extract_dir in enumerate(dirs, 1):
        if not extract_dir.is_dir():
            continue

        zip_stem = extract_dir.name
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

        # 파싱
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

        # 제목 & body 생성
        doc_type, doc_desc = _extract_doc_type_and_title(parsed_list, zip_stem)
        page_title = f"{doc_type} {spec_no} - {doc_desc}" if doc_desc else f"{doc_type} {spec_no}"
        page_title = _sanitize_title(page_title, f"{doc_type} {spec_no}")

        merged = merge_parsed_docs(parsed_list, spec_no, doc_type)
        merged["title"] = page_title
        body = truncate_body(merged, zip_name)

        labels = ["3gpp", "specification", doc_type.lower(),
                  f"spec-{spec_no.replace('.', '-')}"]

        try:
            publisher.upsert_page(
                title=page_title,
                body=body,
                parent_id=parent_page_id,
                labels=labels,
            )
            success.append(zip_stem)
        except Exception as e:
            logger.error(f"  Publish failed: {e}", exc_info=True)
            failed.append((zip_stem, str(e)[:100]))

    logger.info("=" * 60)
    logger.info(f"Done: {len(success)}/{total} succeeded, {len(skipped)} skipped, {len(failed)} failed")
    if failed:
        logger.warning("Failed:")
        for name, err in failed:
            logger.warning(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
