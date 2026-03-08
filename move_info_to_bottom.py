"""
모든 자식 페이지의 info 매크로를 상단에서 본문 끝으로 이동
"""
import os, sys, re
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
DRY_RUN = "--execute" not in sys.argv

# info 매크로 전체 블록 추출 패턴
INFO_PATTERN = re.compile(
    r'<ac:structured-macro ac:name="info".*?</ac:structured-macro>',
    re.DOTALL
)


def move_info_to_bottom(pid, title, body):
    m = INFO_PATTERN.search(body)
    if not m:
        return None  # info 매크로 없음

    info_block = m.group(0)
    # info 블록 제거 (앞뒤 공백 포함)
    new_body = body[:m.start()].rstrip() + "\n" + body[m.end():].lstrip()
    # 본문 끝에 추가
    new_body = new_body.rstrip() + "\n\n" + info_block
    return new_body


def main():
    print("전체 페이지 스캔 중...")
    results = conf.cql(f'ancestor = "{parent_id}"', limit=500)
    pages = results.get("results", [])
    print(f"총 {len(pages)}개 페이지\n")

    targets = []
    for p in pages:
        pid = p["content"]["id"]
        title = p["content"]["title"]
        page = conf.get_page_by_id(pid, expand="body.storage,version")
        body = page["body"]["storage"]["value"]
        version = page["version"]["number"]
        if INFO_PATTERN.search(body):
            targets.append((pid, title, body, version))

    print(f"info 매크로 있는 페이지: {len(targets)}개")

    if DRY_RUN:
        print(f"\n[DRY RUN] 실제 적용: python move_info_to_bottom.py --execute")
        print("샘플 (첫 3개):")
        for pid, title, body, version in targets[:3]:
            print(f"  [{pid}] {title[:60]}")
        return

    print(f"\n[EXECUTE] 이동 중...")
    success, failed, skipped = [], [], []

    for i, (pid, title, body, version) in enumerate(targets, 1):
        new_body = move_info_to_bottom(pid, title, body)
        if new_body is None:
            skipped.append(pid)
            continue
        try:
            conf.update_page(
                page_id=pid,
                title=title,
                body=new_body,
                representation="storage",
            )
            success.append(pid)
            if i % 20 == 0 or i <= 3:
                print(f"  [{i}/{len(targets)}] OK: {title[:50]}")
        except Exception as e:
            print(f"  [{i}] FAILED [{pid}] {title[:40]}: {e}")
            failed.append((pid, str(e)))

    print(f"\n완료: {len(success)}/{len(targets)} 성공, {len(failed)} 실패")


if __name__ == "__main__":
    main()
