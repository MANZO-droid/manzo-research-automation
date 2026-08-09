# -*- coding: utf-8 -*-
"""
1회성 패치: 이미 저장된 daily_gainers 행에 차트용 이동평균선 시계열
(technicals.maLines)을 채운다(2026-08-08, 회장님 요청 - "차트 그림에
5/20/60/120일선을 같이 넣어줄 수 있어?").

배경: collect_gainers.py/backfill_krx_historical.py의 calc_ma_lines()가
새로 생성되는 리포트부터는 자동으로 채우지만(커밋 참고), 이미 저장된
과거 리포트는 옛 방식(200일 대신 120일 OHLCV, maLines 없음)으로 만들어져
있어 다시 채워야 한다. 우선 2026-08-03~08-08 시범 적용 요청.

이 스크립트는 지정한 날짜의 daily_gainers 행마다 그 날짜 기준 OHLCV를
200일치 다시 가져와 technicals 전체(추세·이격도·ADX·maLines 포함)를
새로 계산해 technicals만 PATCH한다(ohlcv/chart_analysis/rise_reason은
건드리지 않음 - 이번 요청은 차트 선 추가가 목적이라 문구 재생성은
범위 밖).

사용법:
  python scripts/patch_ma_lines.py --dates 2026-08-03,2026-08-04,2026-08-05,2026-08-06,2026-08-07,2026-08-08

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
import argparse, os, sys, time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env, fetch_ohlcv, calc_technicals, calc_ma_lines, KST  # noqa: E402


def fetch_rows(dates: list[str]) -> list[dict]:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    date_list = ",".join(dates)
    r = requests.get(
        f"{url}/rest/v1/daily_gainers?select=trade_date,rank,report_type,ticker,name,close,change_pct"
        f"&trade_date=in.({date_list})&order=trade_date.asc,rank.asc",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def patch_technicals(trade_date: str, rank: int, report_type: str, technicals: dict):
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.patch(
        f"{url}/rest/v1/daily_gainers?trade_date=eq.{trade_date}&rank=eq.{rank}&report_type=eq.{report_type}",
        headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": "return=minimal",
        },
        json={"technicals": technicals, "updated_at": datetime.now(KST).isoformat()},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"PATCH 실패 {r.status_code}: {r.text}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True)
    args = ap.parse_args()

    load_env()
    for k in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]:
        if not os.environ.get(k):
            print(f"[오류] {k}가 없습니다.")
            sys.exit(1)

    dates = args.dates.split(",")
    rows = fetch_rows(dates)
    print(f"대상 {len(rows)}행")

    ok, fail = 0, 0
    for row in rows:
        ticker, name, trade_date = row["ticker"], row["name"], row["trade_date"]
        ohlcv_all = fetch_ohlcv(ticker, count=200)
        ohlcv = [o for o in ohlcv_all if o["date"] <= trade_date]
        if not ohlcv:
            print(f"  [건너뜀] {trade_date} #{row['rank']} {name}({ticker}) - OHLCV 없음")
            fail += 1
            continue
        close = row.get("close") or ohlcv[-1]["close"]
        volume = ohlcv[-1].get("volume", 0)
        technicals = calc_technicals(ohlcv, close, volume)
        technicals["maLines"] = calc_ma_lines(ohlcv, window=60)

        patch_technicals(trade_date, row["rank"], row["report_type"], technicals)
        ma120_filled = sum(1 for v in technicals["maLines"]["ma120"] if v is not None)
        print(f"  [OK] {trade_date} #{row['rank']} {name}({ticker}) "
              f"ma120 채움 {ma120_filled}/{len(technicals['maLines']['ma120'])}일")
        ok += 1
        time.sleep(0.3)

    print(f"\n완료: 성공 {ok}건 / 실패 {fail}건")


if __name__ == "__main__":
    main()
