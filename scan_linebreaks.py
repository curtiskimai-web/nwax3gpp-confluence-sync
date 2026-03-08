"""
모든 자식 페이지에서 거대 단일 <p> 블록이 있는 페이지 스캔 후 자동 수정
"""
import os, re, sys
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
LARGE_P_THRESHOLD_KB = 50  # 50KB 이상인 <p> 블록을 문제로 간주


def split_into_paragraphs(text: str) -> list[str]:
    SECTION_PATTERNS = [
        r'(?<=[a-z.;]) ((?:Fore[Ww]ord|Introduction|Scope|References?|Definitions?|Abbreviations?|General|Overview|Keywords|Copyright|Intellectual Property|Notice|Postal address|Internet|Tel|Fax|www\.))',
        r'(?<=[a-z.;,]) (\d+(?:\.\d+)*\s+[A-Z][a-z])',
        r'(?<=[a-z.;,]) ([A-Z]\.\d+\s+)',
        r'(?<=\.) (The present document)',
        r'(?<=\.) (This Technical)',
        r'(?<=\.) (This document)',
        r'(?<=\.) (3GPP)',
        r'(?<=\.) (Release \d+)',
    ]
    result = text
    for pat in SECTION_PATTERNS:
        result = re.sub(pat, r'\n\1', result)
    paras = [p.strip() for p in result.split('\n') if p.strip()]
    return paras


def fix_page(page_id, title, body, version):
    p_blocks = re.findall(r'<p>(.*?)</p>', body, flags=re.DOTALL)
    if not p_blocks:
        return False

    largest_idx = max(range(len(p_blocks)), key=lambda i: len(p_blocks[i]))
    largest_p = p_blocks[largest_idx]
    size_kb = len(largest_p.encode('utf-8')) // 1024

    if size_kb < LARGE_P_THRESHOLD_KB:
        return False

    paras = split_into_paragraphs(largest_p)
    new_p_html = '\n'.join(f'<p>{p}</p>' for p in paras)

    old_p_tag = f'<p>{largest_p}</p>'
    if old_p_tag not in body:
        print(f"    ERROR: 원본 <p> 태그 찾기 실패")
        return False

    new_body = body.replace(old_p_tag, new_p_html, 1)

    if not DRY_RUN:
        conf.update_page(
            page_id=page_id,
            title=title,
            body=new_body,
            representation="storage",
        )
    return True


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

        p_blocks = re.findall(r'<p>(.*?)</p>', body, flags=re.DOTALL)
        if not p_blocks:
            continue

        largest_p = max(p_blocks, key=len)
        size_kb = len(largest_p.encode('utf-8')) // 1024

        if size_kb >= LARGE_P_THRESHOLD_KB:
            targets.append((pid, title, body, version, size_kb))
            print(f"  [{pid}] {size_kb}KB 단일<p> - {title[:60]}")

    print(f"\n대상: {len(targets)}개")

    if DRY_RUN:
        print(f"[DRY RUN] 실제 수정하려면: python scan_linebreaks.py --execute")
        return

    print(f"\n[EXECUTE] 수정 중...")
    success, failed = [], []
    for pid, title, body, version, size_kb in targets:
        try:
            fixed = fix_page(pid, title, body, version)
            if fixed:
                print(f"  OK [{pid}] {size_kb}KB→분리 완료: {title[:50]}")
                success.append(pid)
        except Exception as e:
            print(f"  FAILED [{pid}] {title[:40]}: {e}")
            failed.append((pid, str(e)))

    print(f"\n완료: {len(success)}/{len(targets)} 성공, {len(failed)} 실패")


if __name__ == "__main__":
    main()
