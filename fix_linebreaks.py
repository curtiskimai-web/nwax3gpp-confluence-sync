"""
TR 38.855 등 하나의 <p>에 모든 텍스트가 몰려있는 페이지 줄바꿈 수정
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

DRY_RUN = "--execute" not in sys.argv

# 수정 대상 페이지 ID (TR 38.855)
TARGET_PAGE_ID = "144508"


def split_into_paragraphs(text: str) -> list[str]:
    """
    연속된 텍스트를 의미있는 단락으로 분리
    3GPP 스펙의 패턴을 활용:
    - 절 번호: "1 Scope", "2 References", "A.1 " 등
    - 키워드: "Keywords:", "Foreword", "Introduction"
    - 문장 끝 + 대문자 시작
    """
    # 공통 섹션 헤더 패턴
    SECTION_PATTERNS = [
        r'(?<=[a-z.;]) ((?:Fore[Ww]ord|Introduction|Scope|References?|Definitions?|Abbreviations?|General|Overview|Keywords|Copyright|Intellectual Property|Notice|Postal address|Internet|Tel|Fax|www\.))',
        r'(?<=[a-z.;,]) (\d+(?:\.\d+)*\s+[A-Z][a-z])',  # "1 Scope", "2.1 General" 등
        r'(?<=[a-z.;,]) ([A-Z]\.\d+\s+)',                 # "A.1 ", "B.2 " 등
        r'(?<=\.) (The present document)',
        r'(?<=\.) (This Technical)',
        r'(?<=\.) (This document)',
        r'(?<=\.) (3GPP)',
        r'(?<=\.) (Release \d+)',
    ]

    result = text
    for pat in SECTION_PATTERNS:
        result = re.sub(pat, r'\n\1', result)

    # 줄바꿈을 기준으로 단락 분리
    paras = [p.strip() for p in result.split('\n') if p.strip()]
    return paras


def fix_page_linebreaks(page_id: str):
    page = conf.get_page_by_id(page_id, expand="body.storage,version")
    title = page["title"]
    version = page["version"]["number"]
    body = page["body"]["storage"]["value"]

    print(f"페이지: [{page_id}] v{version} {title}")
    print(f"  Body 크기: {len(body.encode('utf-8'))//1024}KB")

    # <p>...</p> 블록 찾기
    p_blocks = re.findall(r'<p>(.*?)</p>', body, flags=re.DOTALL)
    print(f"  <p> 블록 수: {len(p_blocks)}")
    for i, p in enumerate(p_blocks):
        size = len(p.encode('utf-8'))
        print(f"    [{i}] {size//1024}KB: {p[:80].replace(chr(10),'⏎')!r}")

    # 가장 큰 <p> 블록 찾기
    if not p_blocks:
        print("  <p> 블록 없음, 스킵")
        return

    largest_idx = max(range(len(p_blocks)), key=lambda i: len(p_blocks[i]))
    largest_p = p_blocks[largest_idx]
    size_kb = len(largest_p.encode('utf-8')) // 1024

    if size_kb < 10:
        print(f"  가장 큰 <p>가 {size_kb}KB - 수정 불필요")
        return

    print(f"\n  가장 큰 <p>[{largest_idx}] {size_kb}KB → 단락 분리 시작")

    # 단락 분리
    paras = split_into_paragraphs(largest_p)
    print(f"  분리된 단락 수: {len(paras)}")
    for i, p in enumerate(paras[:5]):
        print(f"    [{i}] {p[:80]!r}")
    if len(paras) > 5:
        print(f"    ... ({len(paras)-5}개 더)")

    # 새 HTML 생성: 각 단락을 <p>로 감싸기
    new_p_html = '\n'.join(f'<p>{p}</p>' for p in paras)

    # 원래 거대 <p>를 새 HTML로 교체
    # 정확한 교체를 위해 원본 <p>...</p> 찾아서 교체
    old_p_tag = f'<p>{largest_p}</p>'
    if old_p_tag not in body:
        print("  ERROR: 원본 <p> 태그를 찾을 수 없음")
        return

    new_body = body.replace(old_p_tag, new_p_html, 1)
    print(f"\n  새 Body 크기: {len(new_body.encode('utf-8'))//1024}KB")
    print(f"  새 <p> 블록 수: {len(re.findall(r'<p>', new_body))}")

    if DRY_RUN:
        print("\n[DRY RUN] 실제 적용하려면: python fix_linebreaks.py --execute")
        return

    print("\n[EXECUTE] 페이지 업데이트 중...")
    conf.update_page(
        page_id=page_id,
        title=title,
        body=new_body,
        representation="storage",
    )
    print(f"  완료: {title}")


if __name__ == "__main__":
    fix_page_linebreaks(TARGET_PAGE_ID)
