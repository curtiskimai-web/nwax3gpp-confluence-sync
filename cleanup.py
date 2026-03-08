"""
Confluence 기존 페이지 정리 스크립트
parent_page_id (65715) 하위의 모든 페이지 삭제
"""
import os
import time
from dotenv import load_dotenv
load_dotenv()

from atlassian import Confluence

client = Confluence(
    url=os.environ["CONFLUENCE_URL"],
    username=os.environ["CONFLUENCE_USER"],
    password=os.environ["CONFLUENCE_API_TOKEN"],
    cloud=True,
)
space_key = os.environ["CONFLUENCE_SPACE_KEY"]
parent_page_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]


def delete_page_recursive(page_id: str, title: str, depth: int = 0):
    indent = "  " * depth
    children = list(client.get_child_pages(page_id) or [])
    for child in children:
        delete_page_recursive(child["id"], child["title"], depth + 1)
    try:
        client.remove_page(page_id)
        safe_title = title.encode("cp949", errors="replace").decode("cp949")
        print(f"{indent}Deleted: {safe_title} (id={page_id})")
        time.sleep(0.3)
    except Exception as e:
        safe_title = title.encode("cp949", errors="replace").decode("cp949")
        print(f"{indent}Failed to delete {safe_title}: {e}")


def main():
    print(f"Fetching children of parent page {parent_page_id}...")
    children = list(client.get_child_pages(parent_page_id) or [])
    print(f"Found {len(children)} child pages to delete.\n")

    for child in children:
        delete_page_recursive(child["id"], child["title"])

    print("\nCleanup complete.")


if __name__ == "__main__":
    import sys
    if "--yes" in sys.argv:
        main()
    else:
        confirm = input(f"Delete ALL child pages under page {parent_page_id}? (yes/no): ")
        if confirm.strip().lower() == "yes":
            main()
        else:
            print("Aborted.")
