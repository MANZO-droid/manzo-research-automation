# -*- coding: utf-8 -*-
"""
1회성 수정: 최근 며칠(2026-08-03~08-07)의 volume_stocks가 실제로는
"거래대금 상위"가 아니라 "거래량(주식수) 상위"였던 버그를 바로잡는다.

배경: fetch_volume_stocks()가 네이버 sise_quant.naver 표에서 거래량(주)
컬럼을 거래대금으로 잘못 읽고 있었다(collect_gainers.py 2026-08-08 수정,
커밋 a3039e9). 이 페이지 자체가 거래량순 정렬이라, 저가 레버리지·인버스
ETF가 항상 1~10위를 독점하는 잘못된 순위가 저장돼 있었다.

이 스크립트는 영향받은 날짜만 KRX Open API(실제 거래대금 ACC_TRDVAL)로
다시 계산해 그 날짜의 volume_stocks 10행을 통째로 교체한다(순위 뿐
아니라 어떤 종목이 top10인지 자체가 바뀌므로 필드 patch가 아니라
전체 교체 - DELETE 후 INSERT).

사용법:
  python scripts/fix_volume_ranking.py --dates 2026-08-03,2026-08-04,2026-08-05,2026-08-06,2026-08-07

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, KRX_OPENAPI_KEY
"""
import argparse, os, sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env, volume_to_row  # noqa: E402
from backfill_krx_historical import fetch_krx_day, build_volume_top10  # noqa: E402


def delete_existing(date_str: str):
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.delete(
        f"{url}/rest/v1/volume_stocks?trade_date=eq.{date_str}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"삭제 실패 {r.status_code}: {r.text}")


def insert_rows(rows: list[dict]):
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.post(
        f"{url}/rest/v1/volume_stocks",
        headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": "return=minimal",
        },
        json=rows, timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"삽입 실패 {r.status_code}: {r.text}")


def fix_date(date_str: str):
    print(f"\n{'='*50}\n[교정] {date_str}\n{'='*50}")
    base_dd = date_str.replace("-", "")
    kospi = fetch_krx_day(base_dd, "stk")
    kosdaq = fetch_krx_day(base_dd, "ksq")
    all_stocks = kospi + kosdaq
    if not all_stocks:
        print("  [경고] KRX 데이터 없음 - 건너뜀")
        return
    top10 = build_volume_top10(all_stocks, date_str)
    for s in top10:
        print(f"  #{s['rank']} {s['name']}({s['ticker']}) 거래대금={s['tradeAmount']:,} "
              f"순매수={s['investors']}")
    rows = [volume_to_row(date_str, v) for v in top10]
    delete_existing(date_str)
    insert_rows(rows)
    print(f"  {date_str} 교체 완료 ({len(rows)}행)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True)
    args = ap.parse_args()

    load_env()
    for k in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "KRX_OPENAPI_KEY"]:
        if not os.environ.get(k):
            print(f"[오류] {k}가 없습니다.")
            sys.exit(1)

    for date_str in args.dates.split(","):
        fix_date(date_str)

    print("\n전체 완료!")


if __name__ == "__main__":
    main()
