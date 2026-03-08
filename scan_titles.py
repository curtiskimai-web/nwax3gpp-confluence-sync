"""
전체 페이지 제목 이상 여부 스캔
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
results = conf.cql(f'ancestor = "{parent_id}"', limit=500)
pages = results.get("results", [])

SUSPICIOUS = [
    (r"- \.\d",                   "섹션번호(.6 형식)"),
    (r"- \d+\.\d+\.\d+",          "섹션번호(x.y.z)"),
    (r"- [A-Z]\.\d+[\. ]",        "부록번호(A.1 형식)"),
    (r"- \(Release",              "릴리즈 번호"),
    (r"- The present",            "본문 첫줄"),
    (r"- Annex [A-Z]",            "부록 섹션"),
    (r"PAGEREF",                   "Word 필드코드"),
    (r"https?://",                 "URL 포함"),
    (r"- [a-z]",                   "소문자로 시작"),
    (r"- \d+ [A-Z][a-z]",          "절번호+제목(1 Scope)"),
    (r"- Figure \d",               "Figure 캡션"),
    (r"- Table \d",                "Table 캡션"),
    (r"- NOTE",                    "NOTE 텍스트"),
    (r"- [A-Z][a-z]+ [a-z].{20,}", "본문 문장 패턴"),
]

suspicious = []
for p in pages:
    pid = p["content"]["id"]
    title = p["content"]["title"]
    for pattern, reason in SUSPICIOUS:
        if re.search(pattern, title):
            suspicious.append((pid, title, reason))
            break

print(f"의심 페이지: {len(suspicious)}개\n")
for pid, title, reason in sorted(suspicious, key=lambda x: x[1]):
    print(f"[{pid}] [{reason}]")
    print(f"  {title[:100]}")
