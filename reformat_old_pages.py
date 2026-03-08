"""
구버전 포맷 Confluence 페이지를 신규 포맷으로 직접 변환
- .doc 재파싱 없이 기존 body를 신규 포맷으로 래핑
- info 매크로 추가, 본문 <p>를 투명 테이블로 감싸기
"""
import os, re, html, sys
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
DRY_RUN = "--execute" not in __import__("sys").argv


def reformat_body(title: str, old_body: str) -> str:
    """구버전 body → 신규 포맷 변환"""
    esc = html.escape

    # 기존 TOC 매크로 제거 (새로 추가)
    body = re.sub(r'<ac:structured-macro ac:name="toc"[^/]*/>', '', old_body)
    body = re.sub(r'<ac:structured-macro ac:name="toc".*?</ac:structured-macro>', '', body, flags=re.DOTALL)

    # <p> 단락들을 투명 테이블로 묶기
    def wrap_p_groups(text):
        # 연속된 <p> 블록을 table로 감쌈
        def replace_p_block(m):
            inner = m.group(0)
            return (
                f'<table style="width:100%;border-collapse:collapse;border:none;background-color:transparent"><tbody>'
                f'<tr><td style="padding:0;border:none;word-break:break-word;background-color:transparent">{inner}</td></tr>'
                f'</tbody></table>'
            )
        # 연속 <p>...</p> 블록 감싸기
        return re.sub(r'(<p>.*?</p>\s*)+', replace_p_block, text, flags=re.DOTALL)

    body = wrap_p_groups(body.strip())

    # 신규 포맷 조합
    new_body = f'<ac:structured-macro ac:name="toc" ac:schema-version="1"/>\n'
    new_body += f'''
<ac:structured-macro ac:name="info" ac:schema-version="1">
<ac:parameter ac:name="title">{esc(title)}</ac:parameter>
<ac:rich-text-body><p>Source: 3GPP 38 Series Specification</p></ac:rich-text-body>
</ac:structured-macro>\n'''
    new_body += body

    return new_body


def get_old_format_pages():
    """info 매크로 없는 구버전 페이지 목록"""
    results = conf.cql(f'ancestor = "{parent_id}"', limit=500)
    pages = results.get("results", [])
    old_pages = []
    for p in pages:
        pid = p["content"]["id"]
        title = p["content"]["title"]
        page = conf.get_page_by_id(pid, expand="body.storage,version")
        body = page["body"]["storage"]["value"]
        kb = len(body.encode("utf-8")) // 1024
        if kb < 1:
            continue  # 빈 페이지 스킵
        has_info = 'ac:name="info"' in body
        has_border_none = "border:none" in body
        if not has_info and not has_border_none:
            old_pages.append((pid, title, body, page["version"]["number"]))
    return old_pages


def main():
    print("구버전 포맷 페이지 스캔 중...")
    old_pages = get_old_format_pages()
    print(f"대상: {len(old_pages)}개\n")

    for pid, title, old_body, version in old_pages:
        print(f"  [{pid}] v{version} {title[:60]}")

    if DRY_RUN:
        print(f"\n[DRY RUN] 실제 변환하려면: python reformat_old_pages.py --execute")
        return

    print(f"\n[EXECUTE] 변환 중...")
    success, failed = [], []
    for pid, title, old_body, version in old_pages:
        try:
            new_body = reformat_body(title, old_body)
            conf.update_page(
                page_id=pid,
                title=title,
                body=new_body,
                representation="storage",
            )
            print(f"  OK: {title[:60]}")
            success.append(pid)
        except Exception as e:
            print(f"  FAILED [{pid}] {title[:40]}: {e}")
            failed.append((pid, str(e)))

    print(f"\n완료: {len(success)}/{len(old_pages)} 성공, {len(failed)} 실패")


if __name__ == "__main__":
    main()
