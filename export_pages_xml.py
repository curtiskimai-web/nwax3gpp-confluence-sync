"""
Confluence 하위 페이지 전체를 XML 형식으로 export → zip 압축
각 페이지: <page-id>.xml 파일
최종 결과: confluence_export_<date>.zip
"""
import os, sys, re, zipfile, html
from datetime import date, datetime
from pathlib import Path
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
space_key = os.environ["CONFLUENCE_SPACE_KEY"]
base_url = os.environ["CONFLUENCE_URL"].rstrip("/")

OUTPUT_DIR = Path(__file__).parent / "export_xml"
ZIP_NAME = f"confluence_export_{date.today().strftime('%Y%m%d')}.zip"


def safe_filename(title: str) -> str:
    """페이지 제목 → 안전한 파일명"""
    name = re.sub(r'[\\/:*?"<>|]', '_', title)
    return name[:120]


def page_to_xml(page_data: dict) -> str:
    """페이지 데이터 → XML 문자열"""
    pid = page_data["id"]
    title = page_data["title"]
    version = page_data["version"]["number"]
    created = page_data.get("history", {}).get("createdDate", "")
    last_updated = page_data["version"].get("when", "")
    author = (
        page_data.get("history", {})
        .get("createdBy", {})
        .get("displayName", "")
    )
    last_author = (
        page_data["version"]
        .get("by", {})
        .get("displayName", "")
    )
    body_storage = page_data["body"]["storage"]["value"]
    page_url = f"{base_url}/wiki/spaces/{space_key}/pages/{pid}"

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<confluence-page>
  <metadata>
    <id>{pid}</id>
    <title>{html.escape(title)}</title>
    <space-key>{html.escape(space_key)}</space-key>
    <version>{version}</version>
    <created>{html.escape(created)}</created>
    <last-updated>{html.escape(last_updated)}</last-updated>
    <author>{html.escape(author)}</author>
    <last-author>{html.escape(last_author)}</last-author>
    <url>{html.escape(page_url)}</url>
    <parent-id>{parent_id}</parent-id>
  </metadata>
  <body><![CDATA[
{body_storage}
  ]]></body>
</confluence-page>
"""
    return xml


def main():
    print("하위 페이지 목록 가져오는 중...")
    results = conf.cql(f'ancestor = "{parent_id}"', limit=500)
    pages = results.get("results", [])
    print(f"총 {len(pages)}개 페이지\n")

    OUTPUT_DIR.mkdir(exist_ok=True)

    # 기존 XML 파일 정리
    for f in OUTPUT_DIR.glob("*.xml"):
        f.unlink()

    failed = []
    for i, p in enumerate(pages, 1):
        pid = p["content"]["id"]
        title = p["content"]["title"]
        try:
            page_data = conf.get_page_by_id(
                pid,
                expand="body.storage,version,history,history.createdBy,version.by"
            )
            xml_str = page_to_xml(page_data)
            fname = f"{safe_filename(title)}__{pid}.xml"
            (OUTPUT_DIR / fname).write_text(xml_str, encoding="utf-8")

            if i % 25 == 0 or i <= 3:
                print(f"  [{i}/{len(pages)}] {title[:60]}")
        except Exception as e:
            print(f"  [{i}] FAILED [{pid}] {title[:40]}: {e}")
            failed.append((pid, title, str(e)))

    print(f"\nXML 파일 생성 완료: {len(pages) - len(failed)}개")

    # ZIP 압축
    zip_path = Path(__file__).parent / ZIP_NAME
    xml_files = list(OUTPUT_DIR.glob("*.xml"))
    print(f"ZIP 압축 중: {zip_path.name} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(xml_files):
            zf.write(f, f.name)

    zip_size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"완료: {zip_path} ({zip_size_mb:.1f} MB, {len(xml_files)}개 파일)")

    if failed:
        print(f"\n실패 {len(failed)}개:")
        for pid, title, err in failed:
            print(f"  [{pid}] {title[:50]}: {err}")


if __name__ == "__main__":
    main()
