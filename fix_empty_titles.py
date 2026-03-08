"""
빈 제목 Confluence 페이지를 3GPP 공식 제목으로 업데이트
"""
import os
import re
from dotenv import load_dotenv
load_dotenv()

from atlassian import Confluence

# 3GPP 공식 제목 매핑 (www.3gpp.org/DynaReport/38-series.htm 기준)
OFFICIAL_TITLES = {
    "38.173":     "TDD operating band in Band n48",
    "38.173-001": "TDD operating band in Band n48",
    "38.201":     "NR; Physical layer; General description",
    "38.475":     "NG-RAN; F1 interface user plane protocol",
    "38.475-030": "NG-RAN; F1 interface user plane protocol",
    "38.801":     "Study on new radio access technology: Radio access architecture and interfaces",
    "38.802":     "Study on new radio access technology Physical layer aspects",
    "38.804":     "Study on new radio access technology Radio interface protocol aspects",
    "38.805":     "Study on new radio access technology; 60 GHz unlicensed spectrum",
    "38.806":     "Study of separation of NR Control Plane (CP) and User Plane (UP) for split option 2",
    "38.807":     "Study on requirements for NR beyond 52.6 GHz",
    "38.811":     "Study on New Radio (NR) to support non-terrestrial networks",
    "38.816":     "Study on Central Unit (CU) - Distributed Unit (DU) lower layer split for NR",
    "38.819":     "LTE Band 65 for NR (n65)",
    "38.821":     "Solutions for NR to support Non-Terrestrial Networks (NTN)",
    "38.824":     "Study on physical layer enhancements for NR ultra-reliable and low latency case (URLLC)",
    "38.826":     "Study on evaluation for 2 receiver exception in Rel-15 vehicle mounted UE for NR",
    "38.855":     "Study on NR positioning support",
    "38.866":     "Study on remote interference management for NR",
    "38.873":     "Time Division Duplex (TDD) operating band in Band n48",
    "38.874":     "NR; Study on integrated access and backhaul",
    "38.885":     "Study on NR Vehicle-to-Everything (V2X)",
    "38.900":     "Study on channel model for frequency spectrum above 6 GHz",
    "38.918":     "Study on 5G NR User Equipment (UE) full stack testing for Network Slicing",
    "38.921":     "Study on International Mobile Telecommunications (IMT) parameters for 6.425-7.025GHz, 7.025-7.125GHz and 10.0-10.5 GHz",
    "38.922":     "Study on International Mobile Telecommunications (IMT) parameters for 4400-4800 MHz, 7125-8400 MHz and 14800-15350 MHz",
}

DRY_RUN = "--execute" not in __import__("sys").argv


def main():
    conf = Confluence(
        url=os.environ["CONFLUENCE_URL"],
        username=os.environ["CONFLUENCE_USER"],
        password=os.environ["CONFLUENCE_API_TOKEN"],
        cloud=True,
    )
    parent_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]
    space_key = os.environ["CONFLUENCE_SPACE_KEY"]

    # 모든 자식 페이지 조회
    results = conf.cql(f'ancestor = "{parent_id}"', limit=500)
    pages = results.get("results", [])

    to_update = []
    for p in pages:
        title = p["content"]["title"]
        pid = p["content"]["id"]

        m = re.match(r"^(TS|TR)\s+([\d\.\-]+)\s*$", title)
        if not m:
            continue  # 이미 제목이 있는 페이지

        doc_type = m.group(1)
        spec_no = m.group(2)

        if spec_no in OFFICIAL_TITLES:
            new_title = f"{doc_type} {spec_no} - {OFFICIAL_TITLES[spec_no]}"
            to_update.append((pid, title, new_title))

    print(f"업데이트 대상: {len(to_update)}개")
    for pid, old, new in to_update:
        print(f"  [{pid}] {old}")
        print(f"    → {new}")

    if DRY_RUN:
        print(f"\n[DRY RUN] 실제 적용하려면: python fix_empty_titles.py --execute")
        return

    print(f"\n[EXECUTE] 업데이트 중...")
    success, failed = [], []
    for pid, old_title, new_title in to_update:
        try:
            # 현재 버전 조회
            page = conf.get_page_by_id(pid, expand="body.storage,version")
            body = page["body"]["storage"]["value"]
            conf.update_page(
                page_id=pid,
                title=new_title,
                body=body,
                representation="storage",
            )
            print(f"  OK: {old_title} → {new_title}")
            success.append(pid)
        except Exception as e:
            print(f"  FAILED: {old_title}: {e}")
            failed.append((pid, str(e)))

    print(f"\n완료: {len(success)}/{len(to_update)} 성공, {len(failed)} 실패")


if __name__ == "__main__":
    main()
