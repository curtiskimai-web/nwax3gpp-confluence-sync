"""
info 매크로의 Subject 필드가 없는 페이지에 Subject 추가
- <p>Source: 3GPP 38 Series Specification</p> 형식 → Subject + Source 테이블로 교체
- 이미 테이블이 있는 페이지는 Subject 행이 비어있으면 채움
"""
import os, sys, re, html
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

ESC = html.escape

# info 매크로 전체 추출
INFO_RE = re.compile(
    r'<ac:structured-macro ac:name="info".*?</ac:structured-macro>',
    re.DOTALL
)
# rich-text-body 추출
RICH_RE = re.compile(
    r'<ac:rich-text-body>(.*?)</ac:rich-text-body>',
    re.DOTALL
)


def extract_subject_from_title(page_title: str) -> str:
    """'TS 38.101-1 - User Equipment ...' → 'User Equipment ...'"""
    m = re.match(r'^(?:TS|TR)\s+[\d.\-]+\s*[-–]\s*(.*)', page_title)
    if m:
        return m.group(1).strip()
    return page_title


def build_new_rich_body(subject: str) -> str:
    return (
        f'<table style="width:100%;border-collapse:collapse"><tbody>'
        f'<tr><th style="white-space:nowrap;padding:4px 8px;">Subject</th>'
        f'<td style="padding:4px 8px;">{ESC(subject)}</td></tr>'
        f'<tr><th style="white-space:nowrap;padding:4px 8px;">Source</th>'
        f'<td style="padding:4px 8px;">3GPP 38 Series Specification</td></tr>'
        f'</tbody></table>'
    )


def fix_page_info(page_title: str, body: str) -> str | None:
    """info 매크로 rich-text-body에 Subject가 없으면 추가. 변경 없으면 None."""
    m = INFO_RE.search(body)
    if not m:
        return None

    info_block = m.group(0)
    rm = RICH_RE.search(info_block)
    if not rm:
        return None

    rich_body = rm.group(1)

    # 이미 Subject 있으면 스킵
    if re.search(r'Subject', rich_body, re.IGNORECASE):
        # Subject 행이 비어있는지 확인
        empty_subject = re.search(
            r'<th[^>]*>Subject</th>\s*<td[^>]*>\s*</td>', rich_body, re.IGNORECASE
        )
        if not empty_subject:
            return None  # 이미 Subject 있고 비어있지 않음

    subject = extract_subject_from_title(page_title)
    new_rich = build_new_rich_body(subject)

    new_info = info_block[:rm.start(1)] + new_rich + info_block[rm.end(1):]
    new_body = body[:m.start()] + new_info + body[m.end():]
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
        new_body = fix_page_info(title, body)
        if new_body is not None:
            targets.append((pid, title, new_body, version))

    print(f"Subject 없거나 비어있는 페이지: {len(targets)}개")

    if DRY_RUN:
        print(f"\n[DRY RUN] 실제 적용: python fix_info_subject.py --execute")
        print("샘플 (첫 5개):")
        for pid, title, _, _ in targets[:5]:
            print(f"  [{pid}] {title[:70]}")
        return

    print(f"\n[EXECUTE] Subject 추가 중...")
    success, failed = [], []

    for i, (pid, title, new_body, version) in enumerate(targets, 1):
        try:
            conf.update_page(
                page_id=pid,
                title=title,
                body=new_body,
                representation="storage",
            )
            success.append(pid)
            if i % 20 == 0 or i <= 3:
                print(f"  [{i}/{len(targets)}] OK: {title[:60]}")
        except Exception as e:
            print(f"  [{i}] FAILED [{pid}] {title[:40]}: {e}")
            failed.append((pid, str(e)))

    print(f"\n완료: {len(success)}/{len(targets)} 성공, {len(failed)} 실패")


if __name__ == "__main__":
    main()
