"""
export_xml/*.xml → 다른 Confluence에 페이지 재생성

사용법:
  python import_pages_xml.py \
    --url https://target.atlassian.net \
    --user your@email.com \
    --token YOUR_API_TOKEN \
    --space SPACE_KEY \
    --parent PARENT_PAGE_ID

옵션:
  --dry-run   실제 생성 없이 목록만 출력 (기본값)
  --execute   실제 생성 실행
"""
import os, sys, re, xml.etree.ElementTree as ET, argparse
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from atlassian import Confluence

EXPORT_DIR = Path(__file__).parent / "export_xml"


def parse_xml_file(path: Path) -> dict | None:
    """XML 파일 파싱 → {title, body, original_id}"""
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        meta = root.find("metadata")
        title = meta.findtext("title", "").strip()
        original_id = meta.findtext("id", "").strip()
        # CDATA body 추출
        body_el = root.find("body")
        body = (body_el.text or "").strip()
        if not title or not body:
            return None
        return {"title": title, "body": body, "original_id": original_id}
    except Exception as e:
        print(f"  [PARSE ERROR] {path.name}: {e}")
        return None


def spec_sort_key(title: str) -> tuple:
    m = re.match(r'^(?:TS|TR)\s+([\d.\-]+)', title)
    if m:
        parts = re.findall(r'\d+', m.group(1))
        return tuple(int(p) for p in parts)
    return (9999,)


def main():
    parser = argparse.ArgumentParser(description="Confluence XML import")
    parser.add_argument("--url",    required=True, help="대상 Confluence URL (예: https://target.atlassian.net)")
    parser.add_argument("--user",   required=True, help="사용자 이메일")
    parser.add_argument("--token",  required=True, help="API Token")
    parser.add_argument("--space",  required=True, help="대상 Space Key")
    parser.add_argument("--parent", required=True, help="상위 페이지 ID")
    parser.add_argument("--execute", action="store_true", help="실제 생성 실행 (없으면 dry-run)")
    args = parser.parse_args()

    conf = Confluence(
        url=args.url,
        username=args.user,
        password=args.token,
        cloud=True,
    )

    # XML 파일 로드
    xml_files = sorted(EXPORT_DIR.glob("*.xml"))
    if not xml_files:
        print(f"[ERROR] {EXPORT_DIR} 에 XML 파일이 없습니다.")
        sys.exit(1)

    pages = []
    for f in xml_files:
        data = parse_xml_file(f)
        if data:
            pages.append(data)

    # 스펙 번호 오름차순 정렬
    pages.sort(key=lambda p: spec_sort_key(p["title"]))
    print(f"총 {len(pages)}개 페이지 로드 완료\n")

    if not args.execute:
        print("[DRY RUN] 생성될 페이지 목록 (앞 10개):")
        for p in pages[:10]:
            print(f"  {p['title'][:70]}")
        print(f"  ... ({len(pages)}개 총)")
        print(f"\n실제 생성: python import_pages_xml.py --url ... --execute")
        return

    # 대상 Confluence에 기존 페이지 확인
    print("대상 Confluence 기존 페이지 확인 중...")
    try:
        existing_results = conf.cql(f'ancestor = "{args.parent}"', limit=500)
        existing_titles = {
            p["content"]["title"] for p in existing_results.get("results", [])
        }
        print(f"  기존 페이지 {len(existing_titles)}개\n")
    except Exception as e:
        print(f"  [WARNING] 기존 페이지 조회 실패: {e}")
        existing_titles = set()

    print(f"[EXECUTE] 페이지 생성 중...")
    success, skipped, failed = [], [], []

    for i, page in enumerate(pages, 1):
        title = page["title"]
        body  = page["body"]

        if title in existing_titles:
            skipped.append(title)
            if i <= 5:
                print(f"  [{i}/{len(pages)}] SKIP (이미 존재): {title[:60]}")
            continue

        try:
            conf.create_page(
                space=args.space,
                title=title,
                body=body,
                parent_id=args.parent,
                representation="storage",
            )
            success.append(title)
            if i % 25 == 0 or i <= 3:
                print(f"  [{i}/{len(pages)}] OK: {title[:60]}")
        except Exception as e:
            print(f"  [{i}] FAILED: {title[:50]}: {e}")
            failed.append((title, str(e)))

    print(f"\n완료:")
    print(f"  생성 성공: {len(success)}개")
    print(f"  스킵(중복): {len(skipped)}개")
    print(f"  실패: {len(failed)}개")
    if failed:
        for title, err in failed:
            print(f"    - {title[:50]}: {err}")


if __name__ == "__main__":
    main()
