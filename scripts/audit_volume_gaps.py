# -*- coding: utf-8 -*-
"""
거래대금(volume_stocks) 데이터 공백 감사 스크립트

무엇을 하나:
  Supabase의 daily_gainers 표에 있는 날짜 중, volume_stocks 표에는 없는
  날짜를 찾아 출력한다.
  (거래대금 상위 10위 표가 특정 날짜에 "데이터가 없습니다"로 나오는 원인 진단용)

2026-08-02: 사이트 저장소의 stock-analysis-data.json이 삭제되고 두 표 모두
Supabase가 유일한 원천이 되면서, 이 스크립트도 파일 대신 Supabase를 직접
조회하도록 다시 작성했다.

이 스크립트는 실제 데이터를 채우지 못한다 - 빠진 날짜를 다시 채우려면
scripts/collect_gainers.py를 그 날짜로 재실행해야 한다. 이 스크립트는 어떤
날짜를 백필해야 하는지 사람이 빠르게 파악하도록 목록만 뽑아준다.

필요 환경변수 (.env.local):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY

사용법:
  python scripts/audit_volume_gaps.py
"""
import os
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    for fname in (".env.local", ".env"):
        path = os.path.join(ROOT, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def fetch_distinct_dates(url: str, key: str, table: str, datecol: str) -> set[str]:
    r = requests.get(
        f"{url}/rest/v1/{table}?select={datecol}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase {table} 조회 실패 {r.status_code}: {r.text}")
    return {row[datecol] for row in r.json()}


def main():
    load_env()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("[오류] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY가 없습니다. .env.local에 추가해 주세요.")
        sys.exit(1)

    gainers_dates = fetch_distinct_dates(url, key, "daily_gainers", "trade_date")
    volume_dates = fetch_distinct_dates(url, key, "volume_stocks", "trade_date")

    gaps = sorted(gainers_dates - volume_dates)

    print(f"daily_gainers 내 날짜 수: {len(gainers_dates)}개")
    print(f"volume_stocks 내 날짜 수: {len(volume_dates)}개")
    print()

    if not gaps:
        print("거래대금(volume_stocks) 데이터가 비어있는 날짜: 없음 (daily_gainers의 모든 날짜가 거래대금 데이터도 보유)")
    else:
        print(f"거래대금 데이터가 없는 날짜 ({len(gaps)}개):")
        for d in gaps:
            print(f"  - {d}")

    return gaps


if __name__ == "__main__":
    main()
