"""
구버전/고아 Confluence 페이지 정리 스크립트
대상:
1. stem-only 페이지 (38900-f00 형식) - retry_doc.py 생성 구버전
2. 3GPP prefix 페이지 (3GPP TR 38.900 V15.0.0... 형식) - 초기 pipeline 구버전
3. 잘못된 doc_type 중복 페이지 (TS X.Y 인데 TR X.Y가 올바른 경우)
"""
import os
import re
import sys
from dotenv import load_dotenv
load_dotenv()

from atlassian import Confluence

conf = Confluence(
    url=os.environ["CONFLUENCE_URL"],
    username=os.environ["CONFLUENCE_USER"],
    password=os.environ["CONFLUENCE_API_TOKEN"],
)

parent_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]

DRY_RUN = "--execute" not in sys.argv  # 기본은 dry-run


def get_all_pages():
    results = conf.cql(f'ancestor = "{parent_id}"', limit=300)
    return results.get("results", [])


def main():
    pages = get_all_pages()
    print(f"Total pages: {len(pages)}")

    # 페이지 맵 생성 (title → id)
    title_to_id = {p["content"]["title"]: p["content"]["id"] for p in pages}

    to_delete = []

    for p in pages:
        title = p["content"]["title"]
        pid = p["content"]["id"]

        # 1. stem-only 페이지 (38900-f00 형식)
        if re.match(r"^\d{5}-\w+$", title):
            to_delete.append((pid, title, "stem-only format"))

        # 2. 3GPP prefix 형식 (구버전)
        elif re.match(r"^3GPP ", title):
            to_delete.append((pid, title, "old 3GPP-prefix format"))

        # 3. TS/TR 중복 체크 (TS X.Y 가 있는데 TR X.Y 도 있는 경우)
        elif re.match(r"^TS \d{2}\.", title):
            spec_rest = title[3:]  # "38.900 - ..." 부분
            spec_no = spec_rest.split(" ")[0]  # "38.900"
            # TR 버전이 존재하는지 확인
            tr_title = f"TR {spec_no}"
            # 정확한 TR 페이지 찾기
            for other_title, other_id in title_to_id.items():
                if other_title.startswith(f"TR {spec_no}") and other_id != pid:
                    to_delete.append((pid, title, f"wrong TS (should be TR, TR page exists: '{other_title}')"))
                    break

    # 결과 출력
    print(f"\nPages to delete: {len(to_delete)}")
    for pid, title, reason in to_delete:
        print(f"  [{reason}]")
        print(f"  ID:{pid} - {title}")

    if DRY_RUN:
        print(f"\n[DRY RUN] {len(to_delete)} pages would be deleted.")
        print("실제 삭제하려면: python cleanup_old_pages.py --execute")
        return

    # 실제 삭제
    print(f"\n[EXECUTE] Deleting {len(to_delete)} pages...")
    success, failed = [], []
    for pid, title, reason in to_delete:
        try:
            conf.remove_page(pid)
            print(f"  DELETED: {title} (ID:{pid})")
            success.append(pid)
        except Exception as e:
            print(f"  FAILED: {title} (ID:{pid}): {e}")
            failed.append((pid, str(e)))

    print(f"\nDone: {len(success)} deleted, {len(failed)} failed")
    if failed:
        for pid, err in failed:
            print(f"  - ID:{pid}: {err}")


if __name__ == "__main__":
    main()
