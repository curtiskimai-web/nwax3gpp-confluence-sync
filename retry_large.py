"""
5MB 초과 실패 스펙 + 손상 zip 재처리 스크립트
대상: 38533, 38523-1, 38521-4, 38521-3
"""
import os
import zipfile
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

Path("./logs").mkdir(exist_ok=True)

from main import (
    get_gdrive_service,
    list_files_in_folder,
    download_file,
    extract_zip,
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

# 재처리 대상 zip 이름 (우선순위 순)
TARGET_ZIPS = [
    "38533-j10.zip",
    "38523-1-j30.zip",
    "38521-4-i90.zip",
    "38521-3-j30.zip",
]

MAX_BODY_BYTES = 4_000_000


def truncate_body(merged: dict, zip_name: str) -> str:
    """5MB 이하로 body 축소 (섹션 → full_text 단계적 제거)"""
    body = format_confluence_page(merged, zip_name)
    if len(body.encode("utf-8")) <= MAX_BODY_BYTES:
        return body

    logger.warning(f"  Body {len(body.encode('utf-8'))//1024}KB > limit, truncating sections...")
    sections = list(merged.get("sections", []))
    while sections and len(body.encode("utf-8")) > MAX_BODY_BYTES:
        sections = sections[:len(sections) * 3 // 4]
        merged["sections"] = sections
        body = format_confluence_page(merged, zip_name)

    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        logger.warning("  Still too large, truncating full_text...")
        full_text = merged.get("full_text", "")
        limit = 5000
        while len(body.encode("utf-8")) > MAX_BODY_BYTES and limit >= 0:
            merged["full_text"] = full_text[:limit]
            body = format_confluence_page(merged, zip_name)
            limit = limit // 2

    logger.info(f"  Final body: {len(body.encode('utf-8'))//1024}KB "
                f"({len(merged.get('sections', []))} sections)")
    return body


def main():
    folder_id = os.environ["GDRIVE_FOLDER_ID"]
    parent_page_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]

    logger.info("=" * 60)
    logger.info("Retry large/corrupt zips")
    logger.info(f"Targets: {TARGET_ZIPS}")

    service = get_gdrive_service()
    all_files = list_files_in_folder(service, folder_id)
    zip_map = {f["name"]: f for f in all_files if f["name"].lower().endswith(".zip")}

    publisher = ConfluencePublisher()
    success, failed = [], []

    for zip_name in TARGET_ZIPS:
        zip_stem = Path(zip_name).stem
        spec_no = _extract_spec_number(zip_stem)
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing: {zip_name} (Spec: {spec_no})")

        zip_path = Path("./downloads") / zip_name

        # 다운로드 (0byte 또는 없으면 재다운로드)
        if not zip_path.exists() or zip_path.stat().st_size == 0:
            if zip_name not in zip_map:
                logger.error(f"  Not found in Google Drive: {zip_name}")
                failed.append((zip_name, "Not in Drive"))
                continue
            logger.info(f"  Downloading {zip_name}...")
            try:
                download_file(service, zip_map[zip_name]["id"], zip_path)
            except Exception as e:
                logger.error(f"  Download failed: {e}")
                failed.append((zip_name, f"Download failed: {e}"))
                continue
        else:
            logger.info(f"  Already downloaded ({zip_path.stat().st_size//1024}KB)")

        # 압축 해제
        extract_dir = Path("./extracted") / zip_stem
        try:
            doc_files = extract_zip(zip_path, extract_dir)
        except zipfile.BadZipFile as e:
            logger.error(f"  Bad zip: {e}. Deleting.")
            zip_path.unlink(missing_ok=True)
            failed.append((zip_name, f"BadZipFile: {e}"))
            continue

        if not doc_files:
            logger.warning("  No doc/docx files extracted")
            failed.append((zip_name, "No doc files"))
            continue

        logger.info(f"  Extracted {len(doc_files)} file(s)")

        # 파싱
        parsed_list = []
        for doc_path in sorted(doc_files):
            try:
                parsed = parse_docx(doc_path) if doc_path.suffix.lower() == ".docx" else parse_doc(doc_path)
                parsed_list.append(parsed)
            except Exception as e:
                logger.error(f"  Parse failed {doc_path.name}: {e}")

        if not parsed_list:
            failed.append((zip_name, "All parsing failed"))
            continue

        # 제목 & body 생성
        doc_type, doc_desc = _extract_doc_type_and_title(parsed_list, zip_stem)
        page_title = f"{doc_type} {spec_no} - {doc_desc}" if doc_desc else f"{doc_type} {spec_no}"
        page_title = _sanitize_title(page_title, f"{doc_type} {spec_no}")

        merged = merge_parsed_docs(parsed_list, spec_no, doc_type)
        merged["title"] = page_title
        body = truncate_body(merged, zip_name)

        labels = ["3gpp", "specification", doc_type.lower(), f"spec-{spec_no.replace('.', '-')}"]

        logger.info(f"  Publishing: {page_title}")
        try:
            publisher.upsert_page(
                title=page_title,
                body=body,
                parent_id=parent_page_id,
                labels=labels,
            )
            logger.info(f"  OK: {page_title}")
            success.append(zip_name)
        except Exception as e:
            logger.error(f"  Publish failed: {e}", exc_info=True)
            failed.append((zip_name, str(e)))

    logger.info("\n" + "=" * 60)
    logger.info(f"Done: {len(success)}/{len(TARGET_ZIPS)} succeeded")
    for s in success:
        logger.info(f"  OK: {s}")
    if failed:
        logger.warning(f"Failed ({len(failed)}):")
        for name, err in failed:
            logger.warning(f"  - {name}: {err[:120]}")


if __name__ == "__main__":
    main()
