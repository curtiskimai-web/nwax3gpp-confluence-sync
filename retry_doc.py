"""
실패한 .doc 파일 재처리 스크립트
이미 extracted/ 폴더에 있는 파일만 파싱 → Confluence 퍼블리싱
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

Path("./logs").mkdir(exist_ok=True)

# main.py의 모든 함수/클래스를 재사용
from main import (
    parse_doc,
    parse_docx,
    format_confluence_page,
    ConfluencePublisher,
    logger,
)

FAILED_DOCS = [
    "38900-f00/38900-f00.doc",
    "38885-g00/38885-g00.doc",
    "38873-g00/38873-g00.doc",
    "38874-g00/38874-g00.doc",
    "38866-g10/38866-g10.doc",
    "38855-g00/38855-g00.doc",
    "38826-g00/38826-g00.doc",
    "38824-g00/38824-g00.doc",
    "38821-g20/38821-g20.doc",
    "38819-g00/38819-g00.doc",
    "38816-f00/38816-f00.doc",
]

def main():
    publisher = ConfluencePublisher()
    parent_page_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]
    extracted_base = Path("./extracted")

    total = len(FAILED_DOCS)
    success = 0
    failed = []

    for i, rel_path in enumerate(FAILED_DOCS):
        doc_path = extracted_base / rel_path
        stem = doc_path.stem  # e.g., 38900-f00

        logger.info(f"[{i+1}/{total}] Reprocessing: {doc_path.name}")

        if not doc_path.exists():
            logger.warning(f"  File not found: {doc_path}")
            failed.append((doc_path.name, "File not found"))
            continue

        try:
            # 파싱
            if doc_path.suffix.lower() == ".docx":
                parsed = parse_docx(doc_path)
            else:
                parsed = parse_doc(doc_path)

            title = parsed["title"] or stem
            body = format_confluence_page(parsed, doc_path.name)
            labels = ["3gpp", "specification", stem.lower()]

            # 그룹 페이지(zip명) 가져오거나 생성
            group_page_id = publisher.get_or_create_page_id(
                title=stem,
                parent_id=parent_page_id,
                body=f"<p>Source: {stem}.zip</p>",
            )

            # 문서 페이지 생성/업데이트 (title이 stem과 다른 깨진 페이지 정리)
            # 기존에 깨진 제목으로 생성된 페이지가 있으면 stem 제목으로 업데이트
            publisher.upsert_page(
                title=title,
                body=body,
                parent_id=group_page_id,
                labels=labels,
            )
            logger.info(f"  Page title: {title}")
            success += 1
            logger.info(f"  OK: {title}")

        except Exception as e:
            logger.error(f"  Failed {doc_path.name}: {e}", exc_info=True)
            failed.append((doc_path.name, str(e)))

    logger.info("=" * 60)
    logger.info(f"Retry complete: {success}/{total} succeeded")
    if failed:
        logger.warning(f"Still failing ({len(failed)}):")
        for name, err in failed:
            logger.warning(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
