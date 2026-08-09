# -*- coding: utf-8 -*-
"""
1회성 패치: 이미 저장된 과거 daily_gainers의 차트 분석(chartAnalysis)을
강화된 추세 판정(정배열/역배열 + 이격도 + ADX, 2026-08-08 collect_gainers.py
c221deb)으로 다시 생성한다.

배경: 회장님이 추세 판정 근거를 물어봐서 확인해보니 ma5 vs ma20 단순 비교
였던 걸 정배열/역배열 3단계 + 이격도 + ADX로 강화했다(calc_trend/
calc_disparity/calc_adx). 새로 생성되는 리포트는 자동으로 이 기준을
쓰지만, 이미 저장된 과거 chartAnalysis 문구는 옛 기준(ma5 vs ma20)으로
쓰인 그대로 남아 있어 다시 만들어달라는 요청.

이 스크립트는 지정한 날짜의 daily_gainers 행마다:
  1) 그 날짜 기준 OHLCV를 다시 가져와 calc_technicals()로 technicals를
     새 기준으로 재계산(disparity/adx 포함해 DB의 technicals도 갱신)
  2) build_chart_only_prompt()로 chartAnalysis만 다시 생성(riseReason은
     뉴스 기반이라 이번 요청과 무관 - 건드리지 않음)
  3) technicals + chart_analysis만 PATCH(다른 필드는 그대로 둠)

사용법:
  python scripts/patch_trend_technicals.py --dates 2026-07-23,2026-07-24
  python scripts/patch_trend_technicals.py --dates 2026-07-23 --provider groq

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  --provider gemini(기본): GEMINI_API_KEY / --provider groq: GROQ_API_KEY
"""
import argparse, os, sys, time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (  # noqa: E402
    load_env, fetch_ohlcv, calc_technicals, build_chart_only_prompt,
    parse_chart_only_response, KST,
)
from patch_gainer_fields import analyze_with_retry_gemini, analyze_with_retry_groq  # noqa: E402


def fetch_rows(dates: list[str]) -> list[dict]:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    date_list = ",".join(dates)
    r = requests.get(
        f"{url}/rest/v1/daily_gainers?select=trade_date,rank,report_type,ticker,name,"
        f"change_pct,technicals,chart_analysis&trade_date=in.({date_list})&order=trade_date.asc,rank.asc",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def patch_row(trade_date: str, rank: int, report_type: str, technicals: dict, chart_analysis: str):
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.patch(
        f"{url}/rest/v1/daily_gainers?trade_date=eq.{trade_date}&rank=eq.{rank}&report_type=eq.{report_type}",
        headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": "return=minimal",
        },
        json={"technicals": technicals, "chart_analysis": chart_analysis,
              "updated_at": datetime.now(KST).isoformat()},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"PATCH 실패 {r.status_code}: {r.text}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="쉼표로 구분된 날짜 목록")
    ap.add_argument("--provider", choices=["gemini", "groq"], default="gemini")
    args = ap.parse_args()

    load_env()
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
    required.append("GEMINI_API_KEY" if args.provider == "gemini" else "GROQ_API_KEY")
    for k in required:
        if not os.environ.get(k):
            print(f"[오류] {k}가 없습니다.")
            sys.exit(1)

    if args.provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")

        def call(prompt):
            return analyze_with_retry_gemini(model, prompt)
    else:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)

        def call(prompt):
            return analyze_with_retry_groq(client, prompt)

    dates = args.dates.split(",")
    rows = fetch_rows(dates)
    print(f"대상 {len(rows)}행")

    ok, fail = 0, 0
    for row in rows:
        ticker, name = row["ticker"], row["name"]
        trade_date = row["trade_date"]
        close = (row.get("technicals") or {}).get("current")
        ohlcv_all = fetch_ohlcv(ticker, count=120)
        ohlcv = [o for o in ohlcv_all if o["date"] <= trade_date]
        if not ohlcv:
            print(f"  [건너뜀] {trade_date} #{row['rank']} {name}({ticker}) - OHLCV 없음")
            fail += 1
            continue
        close = close or ohlcv[-1]["close"]
        volume = ohlcv[-1].get("volume", 0)
        new_technicals = calc_technicals(ohlcv, close, volume)

        prompt = build_chart_only_prompt(
            name, ticker, trade_date, float(row["change_pct"] or 0),
            technicals=new_technicals, is_weekly=(row["report_type"] == "weekly"),
        )
        text = call(prompt)
        chart = parse_chart_only_response(text) if text else ""
        if not chart:
            print(f"  [실패] {trade_date} #{row['rank']} {name}({ticker}) - LLM 응답 없음")
            fail += 1
            time.sleep(1.5)
            continue

        patch_row(trade_date, row["rank"], row["report_type"], new_technicals, chart)
        print(f"  [OK] {trade_date} #{row['rank']} {name}({ticker}) "
              f"추세={new_technicals['trend']} 이격도={new_technicals['disparity']}% ADX={new_technicals['adx']}")
        ok += 1
        time.sleep(1.5)

    print(f"\n완료: 성공 {ok}건 / 실패 {fail}건")


if __name__ == "__main__":
    main()
