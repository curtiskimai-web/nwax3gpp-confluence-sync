"""
인덱스 페이지(3GPP Specifications) 최신 정보로 업데이트
- 현재 Confluence 자식 페이지 목록 기반
- TS/TR 분류, spec 번호 정렬, 링크 포함
"""
import os
import re
import html
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
space_key = os.environ["CONFLUENCE_SPACE_KEY"]
base_url = os.environ["CONFLUENCE_URL"].rstrip("/")


def get_all_child_pages():
    results = conf.cql(f'ancestor = "{parent_id}"', limit=500)
    return results.get("results", [])


def spec_sort_key(spec_no: str):
    """38.101-1 → (38, 101, 1) 형태로 정렬"""
    parts = re.findall(r"\d+", spec_no)
    return tuple(int(p) for p in parts)


def build_index_body(ts_pages, tr_pages):
    esc = html.escape

    def make_table(pages, doc_type):
        rows = ""
        for spec_no, title, pid, desc in pages:
            page_url = f"{base_url}/wiki/spaces/{space_key}/pages/{pid}"
            desc_cell = esc(desc) if desc else '<em style="color:#999;">-</em>'
            rows += (
                f'<tr>'
                f'<td style="white-space:nowrap;padding:4px 8px;border:1px solid #ddd;text-align:center;">{esc(doc_type)}</td>'
                f'<td style="white-space:nowrap;padding:4px 8px;border:1px solid #ddd;">'
                f'<a href="{page_url}">{esc(spec_no)}</a></td>'
                f'<td style="padding:4px 8px;border:1px solid #ddd;word-break:break-word;">{desc_cell}</td>'
                f'</tr>'
            )
        return rows

    total = len(ts_pages) + len(tr_pages)
    ts_count = len(ts_pages)
    tr_count = len(tr_pages)

    body = f"""
<ac:structured-macro ac:name="info" ac:schema-version="1">
<ac:parameter ac:name="title">3GPP 38 Series Specifications Index</ac:parameter>
<ac:rich-text-body>
<p>Total: <strong>{total}</strong> specifications &nbsp;|&nbsp; TS: <strong>{ts_count}</strong> &nbsp;|&nbsp; TR: <strong>{tr_count}</strong></p>
<p>마지막 업데이트: 2026-03-08 &nbsp;|&nbsp; 인코딩 오류 수정 완료 &nbsp;|&nbsp; 본문/표 넓이 통일 적용</p>
</ac:rich-text-body>
</ac:structured-macro>

<h2>Technical Specifications (TS) — {ts_count}개</h2>
<table style="width:100%;border-collapse:collapse;table-layout:fixed">
<colgroup><col style="width:4%"/><col style="width:12%"/><col style="width:84%"/></colgroup>
<tbody>
<tr>
<th style="padding:6px 8px;border:1px solid #ddd;background:#f4f5f7;text-align:center;">Type</th>
<th style="padding:6px 8px;border:1px solid #ddd;background:#f4f5f7;">Spec No.</th>
<th style="padding:6px 8px;border:1px solid #ddd;background:#f4f5f7;">Title</th>
</tr>
{make_table(ts_pages, "TS")}
</tbody>
</table>

<h2>Technical Reports (TR) — {tr_count}개</h2>
<table style="width:100%;border-collapse:collapse;table-layout:fixed">
<colgroup><col style="width:4%"/><col style="width:12%"/><col style="width:84%"/></colgroup>
<tbody>
<tr>
<th style="padding:6px 8px;border:1px solid #ddd;background:#f4f5f7;text-align:center;">Type</th>
<th style="padding:6px 8px;border:1px solid #ddd;background:#f4f5f7;">Spec No.</th>
<th style="padding:6px 8px;border:1px solid #ddd;background:#f4f5f7;">Title</th>
</tr>
{make_table(tr_pages, "TR")}
</tbody>
</table>
"""
    return body


def main():
    print("Fetching child pages...")
    pages = get_all_child_pages()
    print(f"Total: {len(pages)} pages")

    ts_pages, tr_pages = [], []
    for p in pages:
        title = p["content"]["title"]
        pid = p["content"]["id"]

        m = re.match(r"^(TS|TR)\s+([\d\.\-]+)\s*[-–]?\s*(.*)", title)
        if m:
            doc_type, spec_no, desc = m.group(1), m.group(2), m.group(3).strip()
        else:
            doc_type = "TS" if title.startswith("TS") else "TR"
            spec_no = title
            desc = ""

        entry = (spec_no, title, pid, desc)
        if doc_type == "TS":
            ts_pages.append(entry)
        else:
            tr_pages.append(entry)

    ts_pages.sort(key=lambda x: spec_sort_key(x[0]))
    tr_pages.sort(key=lambda x: spec_sort_key(x[0]))

    print(f"TS: {len(ts_pages)}, TR: {len(tr_pages)}")

    body = build_index_body(ts_pages, tr_pages)

    # 인덱스 페이지 업데이트
    parent_page = conf.get_page_by_id(parent_id, expand="version")
    current_version = parent_page["version"]["number"]
    parent_title = parent_page["title"]

    print(f"Updating '{parent_title}' (version {current_version} → {current_version + 1})...")
    conf.update_page(
        page_id=parent_id,
        title=parent_title,
        body=body,
        representation="storage",
    )
    print("Done!")


if __name__ == "__main__":
    main()
