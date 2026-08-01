# -*- coding: utf-8 -*-
"""
거래대금(volumeStocks) 데이터 공백 감사 스크립트

무엇을 하나:
  stock-analysis-data.json의 dates 맵을 훑어, "리포트 항목은 존재하지만
  volumeStocks가 비어있거나 없는 날짜"를 찾아 출력한다.
  (거래대금 상위 10위 표가 특정 날짜에 "데이터가 없습니다"로 나오는 원인 진단용)

이 스크립트는 실제 데이터를 채우지 못한다 - Kiwoom/네이버 실시간 수집에는
네트워크 접근과 (필요 시) API 키가 있는 환경에서 실행해야 하는
scripts/collect_gainers.py / scripts/enrich_gainers.py의 재실행이 필요하다.
이 스크립트는 어떤 날짜를 백필해야 하는지 사람이 빠르게 파악하도록 목록만
뽑아준다.

사용법:
  python scripts/audit_volume_gaps.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

# scripts/ 에서 한 단계 위가 이 저장소(리서치자동화)의 루트다.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_ROOT = os.environ.get("SITE_REPO_PATH") or os.path.join(os.path.dirname(ROOT), "만조그룹 2차")
JSON_PATH = os.path.join(SITE_ROOT, "stock-analysis-data.json")


def find_volume_gaps(data: dict) -> list[str]:
    """volumeStocks가 없거나 빈 배열인 날짜 목록(오름차순)을 반환."""
    dates = data.get("dates", {})
    gaps = []
    for d in sorted(dates.keys()):
        entry = dates[d] or {}
        vol = entry.get("volumeStocks")
        if not vol:  # None, [] 모두 포함
            gaps.append(d)
    return gaps


def main():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    dates = data.get("dates", {})
    gaps = find_volume_gaps(data)

    print(f"stock-analysis-data.json 내 날짜 수: {len(dates)}개")
    print(f"latestDate(파일 기준): {data.get('latestDate')}")
    print()

    if not gaps:
        print("volumeStocks가 비어있는 날짜: 없음 (현재 파일에 있는 모든 날짜는 거래대금 데이터 보유)")
    else:
        print(f"volumeStocks가 비어있거나 없는 날짜 ({len(gaps)}개):")
        for d in gaps:
            print(f"  - {d}")

    return gaps


if __name__ == "__main__":
    main()
