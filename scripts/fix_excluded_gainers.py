# -*- coding: utf-8 -*-
"""
1회성 수정: classify_excluded()에 관리종목·정리매매 필터를 추가하기 전에
저장된 daily_gainers에 섞여 들어간 관리종목(예: 2026-07-30 케이엠제약)을
제외하고, 그 자리를 실제 다음 순위 종목으로 교체한다.

배경: KRX Open API로 관리종목 여부(SECT_TP_NM)를 조회하는 기능이 없던
동안 KRX 백필(backfill_krx_historical.py)이 관리종목도 그냥 Top10에
넣어버렸다(회장님이 케이엠제약을 지적해 발견, 감사 결과 7개 날짜 11건).

각 영향받은 날짜에 대해 KRX 데이터를 다시 가져와 올바른 Top10을 재계산하고,
기존에 이미 있던 종목(순위만 바뀜)은 그대로 재사용하되, 새로 들어오는
종목만 OHLCV·재무정보·뉴스·분석을 새로 채운다(불필요한 재작업 최소화 -
Gemini 무료 할당량이 넉넉하지 않음).

사용법:
  python scripts/fix_excluded_gainers.py --dates 2026-07-20,2026-07-21,...
  python scripts/fix_excluded_gainers.py --dates 2026-07-22 --provider groq

--provider 기본값은 gemini. Gemini 무료 분당 한도(20회/분)에 자주 걸려서
groq로도 돌릴 수 있게 함(정규 자동화가 쓰는 것과 같은 GROQ_API_KEY - 둘 다
같은 날 많이 쓰면 서로 할당량을 나눠 쓰게 되니 주의).

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, KRX_OPENAPI_KEY
  --provider gemini(기본): GEMINI_API_KEY
  --provider groq: GROQ_API_KEY
"""
import argparse, os, sys, time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (  # noqa: E402
    load_env, classify_excluded, fetch_ohlcv, calc_technicals,
    fetch_stock_news_staged, news_to_dicts,
    fetch_financials, supabase_upsert, KST, analyze_stock, GroqQuotaExhausted,
)
from backfill_krx_historical import (  # noqa: E402
    fetch_krx_day, analyze_stock_gemini, GeminiQuotaExhausted,
)


def fetch_current_rows(date_str: str) -> dict:
    """Supabase에 이미 있는 그 날짜의 daily_gainers 행을 ticker 기준으로 반환."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.get(
        f"{url}/rest/v1/daily_gainers?trade_date=eq.{date_str}&order=rank.asc",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=20,
    )
    r.raise_for_status()
    return {row["ticker"]: row for row in r.json()}


def fix_date(client, date_str: str, analyze_fn):
    print(f"\n{'='*50}\n[교정] {date_str}\n{'='*50}")
    base_dd = date_str.replace("-", "")
    current = fetch_current_rows(date_str)
    if not current:
        print("  [skip] 이 날짜의 기존 daily_gainers 행이 없음")
        return
    report_type = next(iter(current.values()))["report_type"]

    kospi = fetch_krx_day(base_dd, "stk")
    kosdaq = fetch_krx_day(base_dd, "ksq")
    all_stocks = kospi + kosdaq

    seen, top10 = set(), []
    for s in sorted(all_stocks, key=lambda x: x["changePct"], reverse=True):
        if s["ticker"] in seen:
            continue
        seen.add(s["ticker"])
        reason = classify_excluded(s["ticker"], s["name"], base_dd=base_dd)
        if reason:
            continue
        top10.append(dict(s))
        if len(top10) >= 10:
            break
    for i, s in enumerate(top10, 1):
        s["rank"] = i

    new_tickers = {s["ticker"] for s in top10} - set(current.keys())
    removed_tickers = set(current.keys()) - {s["ticker"] for s in top10}
    if removed_tickers:
        print(f"  제외됨: {[current[t]['name'] for t in removed_tickers]}")
    print(f"  신규 진입(재분석 필요): {[s['name'] for s in top10 if s['ticker'] in new_tickers]}")

    rows_to_save = []
    for s in top10:
        ticker = s["ticker"]
        if ticker in current and ticker not in new_tickers:
            # 기존 데이터 재사용, 순위만 갱신
            old = current[ticker]
            rows_to_save.append({
                "trade_date": date_str, "rank": s["rank"], "report_type": report_type,
                "ticker": ticker, "name": s["name"], "close": s["close"], "change_pct": s["changePct"],
                "trade_amount": s.get("tradeAmount"), "ohlcv": old.get("ohlcv"),
                "technicals": old.get("technicals"), "financials": old.get("financials"),
                "news": old.get("news"), "rise_reason": old.get("rise_reason"),
                "chart_analysis": old.get("chart_analysis"),
                "updated_at": datetime.now(KST).isoformat(),
            })
        else:
            # 신규 진입 - 전부 새로 채운다
            print(f"\n  [신규] #{s['rank']} {s['name']} ({ticker}) +{s['changePct']:.2f}%")
            ohlcv_all = fetch_ohlcv(ticker, count=120)
            ohlcv = [o for o in ohlcv_all if o["date"] <= date_str]
            technicals = calc_technicals(ohlcv, s["close"], s.get("volume", 0))
            financials = fetch_financials(ticker)
            articles, stage = fetch_stock_news_staged(ticker, s["name"], date_str, max_articles=15)
            print(f"     기사 {len(articles)}개 ({stage})")
            news = news_to_dicts(articles, date_str, stage=stage)
            rise, chart = analyze_fn(client, s["name"], ticker, date_str, s["changePct"], articles,
                                     technicals=technicals)
            rows_to_save.append({
                "trade_date": date_str, "rank": s["rank"], "report_type": report_type,
                "ticker": ticker, "name": s["name"], "close": s["close"], "change_pct": s["changePct"],
                "trade_amount": s.get("tradeAmount"), "ohlcv": ohlcv[-60:] if len(ohlcv) > 60 else ohlcv,
                "technicals": technicals, "financials": financials, "news": news,
                "rise_reason": rise, "chart_analysis": chart,
                "updated_at": datetime.now(KST).isoformat(),
            })
            time.sleep(1)

    supabase_upsert("daily_gainers", rows_to_save, "trade_date,rank,report_type")
    print(f"  {date_str} 교정 완료 ({len(rows_to_save)}행 upsert)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="쉼표로 구분된 날짜 목록")
    ap.add_argument("--provider", choices=["gemini", "groq"], default="gemini")
    args = ap.parse_args()

    load_env()
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "KRX_OPENAPI_KEY"]
    required.append("GEMINI_API_KEY" if args.provider == "gemini" else "GROQ_API_KEY")
    for key in required:
        if not os.environ.get(key):
            print(f"[오류] {key}가 없습니다.")
            sys.exit(1)

    if args.provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        client = genai.GenerativeModel("gemini-2.5-flash")
        analyze_fn = analyze_stock_gemini
        quota_exc = GeminiQuotaExhausted
    else:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
        analyze_fn = analyze_stock
        quota_exc = GroqQuotaExhausted

    for date_str in args.dates.split(","):
        try:
            fix_date(client, date_str, analyze_fn)
        except quota_exc as e:
            print(f"\n[중단] {e}")
            sys.exit(1)

    print("\n전체 완료!")


if __name__ == "__main__":
    main()
