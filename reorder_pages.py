"""
Confluence 하위 페이지를 스펙 번호 오름차순으로 재정렬
"""
import os, sys, re, requests
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv()
from atlassian import Confluence

conf = Confluence(
    url=os.environ["CONFLUENCE_URL"],
    username=os.environ["CONFLUENCE_USER"],
    password=os.environ["CONFLUENCE_API_TOKEN"],
    cloud=True,
)
parent_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]
base_url = os.environ["CONFLUENCE_URL"].rstrip("/")
auth = (os.environ["CONFLUENCE_USER"], os.environ["CONFLUENCE_API_TOKEN"])


def spec_sort_key(title: str):
    """제목에서 스펙 번호 추출 후 정렬키 반환"""
    m = re.match(r'^(?:TS|TR)\s+([\d\.\-]+)', title)
    if m:
        parts = re.findall(r'\d+', m.group(1))
        return tuple(int(p) for p in parts)
    return (9999,)


def move_page_after(page_id: str, target_id: str):
    """page_id를 target_id 다음으로 이동"""
    url = f"{base_url}/wiki/rest/api/content/{page_id}/move/after/{target_id}"
    resp = requests.put(url, auth=auth)
    resp.raise_for_status()


def move_page_to_first(page_id: str, parent_id: str):
    """page_id를 부모의 첫 번째 자식으로 이동"""
    # Confluence Cloud: prepend (첫 번째 자식으로)
    url = f"{base_url}/wiki/rest/api/content/{page_id}/move/prepend/{parent_id}"
    resp = requests.put(url, auth=auth)
    resp.raise_for_status()


def main():
    print("하위 페이지 목록 가져오는 중...")
    results = conf.cql(f'ancestor = "{parent_id}"', limit=500)
    pages = results.get("results", [])
    print(f"총 {len(pages)}개 페이지")

    # 스펙 번호로 정렬
    sorted_pages = sorted(pages, key=lambda p: spec_sort_key(p["content"]["title"]))

    print("\n정렬 순서 (앞 10개):")
    for p in sorted_pages[:10]:
        print(f"  {p['content']['title'][:60]}")
    print(f"  ... ({len(sorted_pages)-10}개 더)")

    print("\n페이지 순서 재정렬 중...")

    # 첫 번째 페이지를 prepend
    first_id = sorted_pages[0]["content"]["id"]
    first_title = sorted_pages[0]["content"]["title"]
    try:
        move_page_to_first(first_id, parent_id)
        print(f"  [1/{len(sorted_pages)}] prepend: {first_title[:50]}")
    except Exception as e:
        print(f"  [1] FAILED prepend {first_title[:40]}: {e}")

    # 나머지는 이전 페이지 다음으로
    for i, p in enumerate(sorted_pages[1:], 2):
        pid = p["content"]["id"]
        title = p["content"]["title"]
        prev_id = sorted_pages[i-2]["content"]["id"]
        try:
            move_page_after(pid, prev_id)
            if i % 20 == 0 or i <= 5:
                print(f"  [{i}/{len(sorted_pages)}] after: {title[:50]}")
        except Exception as e:
            print(f"  [{i}] FAILED {title[:40]}: {e}")

    print("\n완료!")


if __name__ == "__main__":
    main()
