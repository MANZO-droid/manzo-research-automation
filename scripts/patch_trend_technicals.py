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

2026-08-09 추가: technicals.trendCriteriaVersion 마커 + 시간 예산.
배경 - Gemini/Groq 무료 할당량이 하루 동안 여러 번 소진돼 270행짜리
배치를 몇 차례 나눠 돌렸는데, technicals.disparity/adx 키만으로는
"문구까지 새로 생성됐는지"를 구분할 수 없었다(calc_technicals가 이제
항상 이 값을 채우므로, maLines만 갱신한 행도 있는 것처럼 보임) - 실행
로그를 일일이 파싱해서 진행 상황을 추적해야 했다. 이제 문구를 실제로
새로 생성한 행에만 technicals.trendCriteriaVersion=2를 심어서, 이미
끝난 행은 --dates에 다시 넣어도 자동으로 건너뛴다(멱등적 재실행 가능 -
GitHub Actions 예약 실행이 할당량 회복 후 스스로 이어받을 수 있도록).
--minutes로 시간 예산을 주면 그 안에 처리 가능한 만큼만 하고 멈춘다
(GH Actions timeout-minutes: 60을 넘기지 않기 위함 - 270행짜리 배치가
60분에 다 못 끝나고 강제 취소된 적이 있어 추가함).

사용법:
  python scripts/patch_trend_technicals.py --dates 2026-07-23,2026-07-24
  python scripts/patch_trend_technicals.py --dates 2026-07-23 --provider groq
  python scripts/patch_trend_technicals.py --dates <19개 날짜> --provider groq --minutes 45

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  --provider gemini(기본): GEMINI_API_KEY / --provider groq: GROQ_API_KEY
"""
import argparse, os, sys, time
from datetime import datetime

TREND_CRITERIA_VERSION = 2  # calc_trend 정배열/역배열+이격도+ADX 기준

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (  # noqa: E402
    load_env, fetch_ohlcv, calc_technicals, calc_ma_lines, build_chart_only_prompt,
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
    ap.add_argument("--minutes", type=float, default=None,
                     help="이 시간(분) 예산 안에서 처리 가능한 만큼만 하고 멈춤(GH Actions 타임아웃 방지)")
    args = ap.parse_args()
    deadline = time.monotonic() + args.minutes * 60 if args.minutes else None

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
    all_rows = fetch_rows(dates)
    rows = [r for r in all_rows
            if (r.get("technicals") or {}).get("trendCriteriaVersion") != TREND_CRITERIA_VERSION]
    skipped_done = len(all_rows) - len(rows)
    print(f"조회 {len(all_rows)}행 / 이미 완료돼 건너뜀 {skipped_done}행 / 처리 대상 {len(rows)}행")

    ok, fail, budget_stopped = 0, 0, 0
    for row in rows:
        if deadline and time.monotonic() >= deadline:
            budget_stopped = len(rows) - ok - fail
            print(f"\n[시간 예산 소진] {args.minutes}분 경과 - 남은 {budget_stopped}행은 다음 실행에서 이어감")
            break
        ticker, name = row["ticker"], row["name"]
        trade_date = row["trade_date"]
        close = (row.get("technicals") or {}).get("current")
        ohlcv_all = fetch_ohlcv(ticker, count=200)
        ohlcv = [o for o in ohlcv_all if o["date"] <= trade_date]
        if not ohlcv:
            print(f"  [건너뜀] {trade_date} #{row['rank']} {name}({ticker}) - OHLCV 없음")
            fail += 1
            continue
        close = close or ohlcv[-1]["close"]
        volume = ohlcv[-1].get("volume", 0)
        new_technicals = calc_technicals(ohlcv, close, volume)
        new_technicals["maLines"] = calc_ma_lines(ohlcv, window=60)
        new_technicals["trendCriteriaVersion"] = TREND_CRITERIA_VERSION

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

    print(f"\n완료: 성공 {ok}건 / 실패 {fail}건 / 시간 예산으로 이월 {budget_stopped}건")


if __name__ == "__main__":
    main()
