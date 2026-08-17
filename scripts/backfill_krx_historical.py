# -*- coding: utf-8 -*-
"""
1회성 백필: KRX 정보데이터시스템 Open API(KRX_OPENAPI_KEY, 회장님이 이미
발급·연동해 두심)로 2026-07-17~07-31 공백 기간의 실제 전종목 시세를 받아
그날그날의 진짜 상승률 Top10·거래대금 Top10을 복원한다.

네이버의 실시간 전용 페이지와 달리 KRX Open API는 과거 특정 기준일(basDd)의
전종목 시세를 정식으로 제공하므로, 이 스크립트로만 과거 날짜를 정확히
복원할 수 있다(2026-08-01 AUTOMATION_NOTES §8-6/8-7 참고).

사용법:
  python scripts/backfill_krx_historical.py --dates 2026-07-20,2026-07-21,...
  python scripts/backfill_krx_historical.py --from 2026-07-20 --to 2026-07-31

완료 후 raw_top_candidates가 채워지므로, 8/1 주간 리포트를
--recompute-weekly 로 다시 계산할 수 있다:
  python scripts/backfill_krx_historical.py --recompute-weekly 2026-08-01 --week-start 2026-07-27 --week-end 2026-07-31

이 백필의 상승이유·차트분석은 Groq가 아니라 Gemini로 생성한다 - 오늘 이미
일일/주간 정규 실행에서 Groq 무료 할당량을 상당히 썼고, 100건 안팎을 한 번에
분석하는 이 백필까지 같은 할당량을 쓰면 정규 자동 실행에 지장을 줄 수 있어서
분리했다(market-scope 파이프라인이 쓰는 것과 동일한 Gemini API, 별도 할당량).

필요 환경변수 (.env.local): GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, KRX_OPENAPI_KEY
"""
import argparse, os, sys, time
from datetime import datetime, timedelta

import requests
import google.generativeai as genai

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (  # noqa: E402
    load_env, classify_excluded, fetch_ohlcv, calc_technicals, calc_ma_lines, fetch_stock_news,
    fetch_stock_news_staged, news_to_dicts,
    fetch_financials, build_analysis_prompt, parse_analysis_response, fetch_investor_netbuy,
    save_raw_candidates, save_to_supabase, get_weekly_top10, KST, has_language_issue,
)
from krx_calendar import is_trading_day  # noqa: E402


def fetch_krx_day(base_dd: str, market: str) -> list[dict]:
    """market: 'stk'(KOSPI) 또는 'ksq'(KOSDAQ). 그날 전종목 시세를 반환한다."""
    key = os.environ["KRX_OPENAPI_KEY"]
    url = f"https://data-dbg.krx.co.kr/svc/apis/sto/{market}_bydd_trd"
    r = requests.get(url, headers={"AUTH_KEY": key}, params={"basDd": base_dd}, timeout=30)
    r.raise_for_status()
    rows = r.json().get("OutBlock_1", [])
    out = []
    for row in rows:
        try:
            ticker = row["ISU_CD"]
            if not (len(ticker) == 6 and ticker.isdigit()):
                continue
            out.append({
                "ticker": ticker,
                "name": row["ISU_NM"],
                "close": int(row["TDD_CLSPRC"]),
                "changePct": float(row["FLUC_RT"]),
                "tradeAmount": int(row["ACC_TRDVAL"]),
                "volume": int(row["ACC_TRDVOL"]),
            })
        except (KeyError, ValueError):
            continue
    return out


def build_daily_top10(all_stocks: list[dict], base_dd: str) -> list[dict]:
    seen, top10 = set(), []
    for s in sorted(all_stocks, key=lambda x: x["changePct"], reverse=True):
        if s["ticker"] in seen:
            continue
        seen.add(s["ticker"])
        reason = classify_excluded(s["ticker"], s["name"], base_dd=base_dd)
        if reason:
            print(f"  [제외] {s['name']} ({s['ticker']}) - {reason}")
            continue
        top10.append(dict(s))
        if len(top10) >= 10:
            break
    if len(top10) < 10:
        print(f"  [경고] 제외 처리 후 {len(top10)}개만 확보됨 (10개 미달)")
    for i, s in enumerate(top10, 1):
        s["rank"] = i
    return top10


def build_volume_top10(all_stocks: list[dict], date_str: str) -> list[dict]:
    top10 = sorted(all_stocks, key=lambda x: x["tradeAmount"], reverse=True)[:10]
    for i, s in enumerate(top10, 1):
        s = dict(s)
        s["rank"] = i
        s["naverUrl"] = f"https://finance.naver.com/item/main.naver?code={s['ticker']}"
        s["investors"] = fetch_investor_netbuy(s["ticker"], s["close"], target_date=date_str,
                                                trade_amount=s.get("tradeAmount"))
        top10[i - 1] = s
        time.sleep(0.3)
    return top10


class GeminiQuotaExhausted(Exception):
    """Gemini 429가 재시도 후에도 풀리지 않을 때 발생시켜 실행을 중단시킨다.
    2026-08-02 수정: 예전엔 여기서 그냥 빈 값("","")을 반환하고 계속 진행해서
    할당량 소진 이후의 모든 종목이 리포트 없이 저장되는 걸 눈치채지 못했다
    (76행이 조용히 비어버린 뒤 회장님이 발견). Groq 경로(collect_gainers.py의
    GroqQuotaExhausted)와 동일하게 실패를 눈에 띄게 만든다."""


def analyze_stock_gemini(model, name: str, ticker: str, date_str: str,
                         change_pct: float, articles: list[dict],
                         technicals: dict | None = None, is_weekly: bool = False,
                         max_retries: int = 4) -> tuple[str, str]:
    if not articles:
        return f"{name}에 대한 뉴스 기사를 수집하지 못했습니다.", ""
    prompt = build_analysis_prompt(name, ticker, date_str, change_pct, articles, technicals, is_weekly)
    wait = 30
    last_text = ""
    for attempt in range(max_retries):
        try:
            resp = model.generate_content(prompt)
            text = resp.text
            if has_language_issue(text):
                last_text = text
                print(f"    [Gemini 언어 오염] 일본어 감지, 재시도 ({attempt + 1}/{max_retries})...")
                continue
            return parse_analysis_response(text)
        except Exception as e:
            if "429" not in str(e):
                print(f"    [Gemini 오류(재시도 안 함)] {e}")
                return "", ""
            print(f"    [Gemini 429] {wait}초 대기 후 재시도 ({attempt + 1}/{max_retries})...")
            time.sleep(wait)
            wait = min(wait * 2, 120)
    if last_text:
        print("    [Gemini 언어 오염] 재시도 소진 - 마지막 응답을 그대로 사용")
        return parse_analysis_response(last_text)
    raise GeminiQuotaExhausted(
        f"Gemini 429가 {max_retries}회 재시도 후에도 풀리지 않았습니다. 무료 할당량이 "
        "소진된 것으로 보여 자동 실행을 중단합니다. 할당량 회복 후 사람이 직접 다시 실행해야 합니다."
    )


def backfill_date(client, date_str: str, analyze_fn=None):
    analyze_fn = analyze_fn or analyze_stock_gemini
    print(f"\n{'='*50}\n[백필] {date_str}\n{'='*50}")
    base_dd = date_str.replace("-", "")

    kospi = fetch_krx_day(base_dd, "stk")
    kosdaq = fetch_krx_day(base_dd, "ksq")
    print(f"  KRX 전종목: KOSPI {len(kospi)}개, KOSDAQ {len(kosdaq)}개")
    if not kospi and not kosdaq:
        print("  [skip] KRX 데이터 없음(휴장일이거나 API 오류)")
        return

    # 주간 리포트 재계산에도 쓰이도록 원본 후보 저장
    save_raw_candidates(date_str, kospi, kosdaq)

    all_stocks = kospi + kosdaq
    gainers = build_daily_top10(all_stocks, base_dd)
    volume_stocks = build_volume_top10(all_stocks, date_str)
    print(f"  gainers {len(gainers)}개, volumeStocks {len(volume_stocks)}개")

    for g in gainers:
        name, ticker = g["name"], g["ticker"]
        print(f"\n  [{g['rank']}] {name} ({ticker}) +{g['changePct']:.2f}%")

        ohlcv_all = fetch_ohlcv(ticker, count=200)
        ohlcv = [o for o in ohlcv_all if o["date"] <= date_str]  # 그날짜 이후 데이터는 제외
        g["ohlcv"] = ohlcv[-60:] if len(ohlcv) > 60 else ohlcv
        g["technicals"] = calc_technicals(ohlcv, g["close"], g.get("volume", 0))
        g["technicals"]["maLines"] = calc_ma_lines(ohlcv, window=60)
        g["financials"] = fetch_financials(ticker)
        g["naverUrl"] = f"https://finance.naver.com/item/main.naver?code={ticker}"
        time.sleep(0.3)

        articles, stage = fetch_stock_news_staged(ticker, name, date_str, max_articles=15)
        print(f"     기사 {len(articles)}개 ({stage})")
        g["news"] = news_to_dicts(articles, date_str, stage=stage)

        rise, chart = analyze_fn(client, name, ticker, date_str, g["changePct"], articles,
                                    technicals=g["technicals"])
        g["riseReason"] = rise
        g["chartAnalysis"] = chart
        time.sleep(1)

    entry = {"date": date_str, "updatedAt": datetime.now(KST).isoformat(),
              "gainers": gainers, "volumeStocks": volume_stocks}
    save_to_supabase(date_str, entry, "daily", None, None)


def recompute_weekly(client, date_str: str, week_start: str, week_end: str, analyze_fn=None):
    analyze_fn = analyze_fn or analyze_stock_gemini
    print(f"\n{'='*50}\n[주간 재계산] {date_str} ({week_start} ~ {week_end})\n{'='*50}")
    gainers = get_weekly_top10(week_start, week_end)
    print(f"  주간 gainers {len(gainers)}개")

    base_dd = week_end.replace("-", "")
    kospi = fetch_krx_day(base_dd, "stk")
    kosdaq = fetch_krx_day(base_dd, "ksq")
    volume_stocks = build_volume_top10(kospi + kosdaq, week_end)

    for g in gainers:
        name, ticker = g["name"], g["ticker"]
        print(f"\n  [{g['rank']}] {name} ({ticker}) 주간 +{g['changePct']:.2f}%")
        ohlcv_all = fetch_ohlcv(ticker, count=200)
        ohlcv = [o for o in ohlcv_all if o["date"] <= week_end]
        g["ohlcv"] = ohlcv[-60:] if len(ohlcv) > 60 else ohlcv
        g["technicals"] = calc_technicals(ohlcv, g["close"], 0)
        g["technicals"]["maLines"] = calc_ma_lines(ohlcv, window=60)
        g["financials"] = fetch_financials(ticker)
        g["naverUrl"] = f"https://finance.naver.com/item/main.naver?code={ticker}"
        time.sleep(0.3)

        articles, stage = fetch_stock_news_staged(ticker, name, week_end, max_articles=15)
        print(f"     기사 {len(articles)}개 ({stage})")
        g["news"] = news_to_dicts(articles, week_end, stage=stage)

        rise, chart = analyze_fn(client, name, ticker, date_str, g["changePct"], articles,
                                    technicals=g["technicals"], is_weekly=True)
        g["riseReason"] = rise
        g["chartAnalysis"] = chart
        time.sleep(1)

    entry = {"date": date_str, "type": "weekly", "weekRange": f"{week_start} ~ {week_end}",
              "updatedAt": datetime.now(KST).isoformat(), "gainers": gainers, "volumeStocks": volume_stocks}
    save_to_supabase(date_str, entry, "weekly", week_start, week_end)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", help="쉼표로 구분된 날짜 목록 (YYYY-MM-DD,...)")
    ap.add_argument("--from", dest="date_from", help="시작 날짜")
    ap.add_argument("--to", dest="date_to", help="종료 날짜")
    ap.add_argument("--recompute-weekly", dest="weekly_date", help="주간 리포트를 다시 계산할 발행일(YYYY-MM-DD)")
    ap.add_argument("--week-start")
    ap.add_argument("--week-end")
    ap.add_argument("--provider", choices=["gemini", "groq"], default="gemini",
                     help="2026-08-17 추가: Gemini 할당량 소진 시 Groq로 전환 가능")
    args = ap.parse_args()

    load_env()
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "KRX_OPENAPI_KEY"]
    required.append("GEMINI_API_KEY" if args.provider == "gemini" else "GROQ_API_KEY")
    for key in required:
        if not os.environ.get(key):
            print(f"[오류] {key}가 없습니다. .env.local에 추가해 주세요.")
            sys.exit(1)

    if args.provider == "gemini":
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        client = genai.GenerativeModel("gemini-2.5-flash")
        analyze_fn = analyze_stock_gemini
        quota_exc = GeminiQuotaExhausted
    else:
        from groq import Groq
        from collect_gainers import analyze_stock, GroqQuotaExhausted
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
        analyze_fn = analyze_stock
        quota_exc = GroqQuotaExhausted

    if args.weekly_date:
        try:
            recompute_weekly(client, args.weekly_date, args.week_start, args.week_end, analyze_fn=analyze_fn)
        except quota_exc as e:
            print(f"\n[중단] {e}")
            sys.exit(1)
        print("\n완료!")
        return

    if args.dates:
        dates = args.dates.split(",")
    elif args.date_from and args.date_to:
        d0 = datetime.strptime(args.date_from, "%Y-%m-%d")
        d1 = datetime.strptime(args.date_to, "%Y-%m-%d")
        dates = []
        d = d0
        while d <= d1:
            ds = d.strftime("%Y-%m-%d")
            if is_trading_day(ds):
                dates.append(ds)
            d += timedelta(days=1)
    else:
        print("[오류] --dates 또는 --from/--to 중 하나가 필요합니다.")
        sys.exit(1)

    print(f"백필 대상 날짜({len(dates)}개): {dates}")
    for date_str in dates:
        try:
            backfill_date(client, date_str, analyze_fn=analyze_fn)
        except quota_exc as e:
            print(f"\n[중단] {e} (이미 처리된 날짜까지는 저장됨)")
            sys.exit(1)

    print("\n전체 완료!")


if __name__ == "__main__":
    main()
