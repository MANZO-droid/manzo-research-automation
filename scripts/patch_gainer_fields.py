# -*- coding: utf-8 -*-
"""
1회성 패치: daily_gainers에서 financials(재무정보)나 news(최근뉴스)가 비어있는
행만 골라 채운다. LLM 분석(riseReason/chartAnalysis)은 건드리지 않는다 -
회장님이 요청한 건 재무정보·뉴스 표시뿐이라 그 범위만 패치한다.

배경: 2026-08-01 KRX 백필(backfill_krx_historical.py)이 처음엔 financials를
항상 빈 값으로 저장했고(재무정보 자동화가 그때까진 없었음), 반대로 그
이전(7/2~7/16)의 기존 데이터는 news가 비어 있었다(회장님이 스크린샷으로
지적). fetch_financials()가 새로 추가된 뒤 이 스크립트로 양쪽을 한 번에
메운다.

2026-08-02 추가: --mode analysis - 7/20~8/1 KRX 백필 도중 Gemini 무료
할당량이 소진되면서(429) riseReason/chartAnalysis가 조용히 빈 값으로
남은 76행을 발견(회장님 지적). analyze_stock_gemini()가 Groq 경로와 달리
실패해도 멈추지 않고 계속 진행하는 구조였던 게 원인 - 이미 저장된 news/
technicals를 재사용해 Gemini 분석만 다시 돌린다(뉴스·재무정보 재수집 안 함).

사용법:
  python scripts/patch_gainer_fields.py --mode financials
  python scripts/patch_gainer_fields.py --mode news
  python scripts/patch_gainer_fields.py --mode analysis
  python scripts/patch_gainer_fields.py --mode both   # financials + news만(기존 동작 유지)

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  --mode analysis를 쓸 때만 GEMINI_API_KEY 추가로 필요.
"""
import argparse, os, sys, time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (  # noqa: E402
    load_env, fetch_financials, fetch_stock_news_staged, news_to_dicts, supabase_upsert,
    build_analysis_prompt, parse_analysis_response,
    build_chart_only_prompt, parse_chart_only_response,
)


def fetch_all_gainer_rows() -> list[dict]:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.get(
        f"{url}/rest/v1/daily_gainers"
        f"?select=trade_date,rank,report_type,ticker,name,news,financials,"
        f"change_pct,technicals,rise_reason,chart_analysis"
        f"&order=trade_date.asc,rank.asc&limit=500",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def patch_financials(rows: list[dict]):
    targets = [r for r in rows if not r.get("financials")]
    print(f"[재무정보] 대상 {len(targets)}행")
    for row in targets:
        fin = fetch_financials(row["ticker"])
        print(f"  {row['trade_date']} #{row['rank']} {row['name']} ({row['ticker']}) -> "
              f"{'OK' if fin else '실패/데이터없음'}")
        if fin:
            supabase_upsert("daily_gainers", [{
                "trade_date": row["trade_date"], "rank": row["rank"], "report_type": row["report_type"],
                "ticker": row["ticker"], "name": row["name"], "financials": fin,
            }], "trade_date,rank,report_type")
        time.sleep(0.3)


def patch_news(rows: list[dict]):
    targets = [r for r in rows if not r.get("news")]
    print(f"[뉴스] 대상 {len(targets)}행")
    for row in targets:
        articles, stage = fetch_stock_news_staged(row["ticker"], row["name"], row["trade_date"], max_articles=15)
        news = news_to_dicts(articles, row["trade_date"], stage=stage)
        print(f"  {row['trade_date']} #{row['rank']} {row['name']} ({row['ticker']}) -> 기사 {len(articles)}개 ({stage})")
        if news:
            supabase_upsert("daily_gainers", [{
                "trade_date": row["trade_date"], "rank": row["rank"], "report_type": row["report_type"],
                "ticker": row["ticker"], "name": row["name"], "news": news,
            }], "trade_date,rank,report_type")
        time.sleep(0.5)


def analyze_with_retry_gemini(model, prompt: str, max_retries: int = 3) -> str:
    from collect_gainers import has_language_issue
    wait = 30
    last_text = ""
    for attempt in range(max_retries):
        try:
            text = model.generate_content(prompt).text
            if has_language_issue(text):
                last_text = text
                print(f"    [Gemini 언어 오염] 일본어 감지, 재시도 ({attempt + 1}/{max_retries})...")
                continue
            return text
        except Exception as e:
            msg = str(e)
            if "429" not in msg:
                print(f"    [Gemini 오류(재시도 안 함)] {e}")
                return ""
            print(f"    [Gemini 429] {wait}초 대기 후 재시도 ({attempt + 1}/{max_retries})...")
            time.sleep(wait)
            wait = min(wait * 2, 120)
    if last_text:
        print("    [Gemini 언어 오염] 재시도 소진 - 이 종목은 건너뜀(다음에 다시 실행)")
        return ""
    print("    [Gemini 429] 재시도 소진 - 이 종목은 건너뜀(다음에 다시 실행)")
    return ""


def analyze_with_retry_groq(client, prompt: str, max_retries: int = 4) -> str:
    from groq import RateLimitError
    from collect_gainers import has_language_issue
    wait = 60
    last_text = ""
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model="openai/gpt-oss-120b", max_tokens=3000,  # 2026-08-17: llama-3.3-70b-versatile 단종으로 교체
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content or ""
            if has_language_issue(text):
                last_text = text
                print(f"    [Groq 언어 오염] 일본어 감지, 재시도 ({attempt + 1}/{max_retries})...")
                continue
            return text
        except RateLimitError:
            print(f"    [Groq 429] {wait}초 대기 후 재시도 ({attempt + 1}/{max_retries})...")
            time.sleep(wait)
            wait = min(wait * 2, 120)
        except Exception as e:
            print(f"    [Groq 오류(재시도 안 함)] {e}")
            return ""
    if last_text:
        print("    [Groq 언어 오염] 재시도 소진 - 이 종목은 건너뜀(다음에 다시 실행)")
        return ""
    print("    [Groq 429] 재시도 소진 - 이 종목은 건너뜀(다음에 다시 실행)")
    return ""


def patch_analysis(rows: list[dict], provider: str):
    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")
        call = lambda prompt: analyze_with_retry_gemini(model, prompt)  # noqa: E731
    else:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
        call = lambda prompt: analyze_with_retry_groq(client, prompt)  # noqa: E731

    # 뉴스는 있는데 chartAnalysis가 비어있는 행만 대상(뉴스가 아예 없는 행은
    # "기사를 수집하지 못했습니다"가 정상 - 건드리지 않는다).
    targets = [r for r in rows if r.get("news") and not r.get("chart_analysis")]
    print(f"[분석 재생성/{provider}] 대상 {len(targets)}행")
    for row in targets:
        prompt = build_analysis_prompt(
            row["name"], row["ticker"], row["trade_date"], float(row["change_pct"] or 0),
            row["news"], technicals=row.get("technicals"), is_weekly=(row["report_type"] == "weekly"),
        )
        text = call(prompt)
        rise, chart = parse_analysis_response(text) if text else ("", "")
        print(f"  {row['trade_date']} #{row['rank']} {row['name']} ({row['ticker']}) -> "
              f"{'OK' if chart else '실패'}")
        if rise and chart:
            supabase_upsert("daily_gainers", [{
                "trade_date": row["trade_date"], "rank": row["rank"], "report_type": row["report_type"],
                "ticker": row["ticker"], "name": row["name"],
                "rise_reason": rise, "chart_analysis": chart,
            }], "trade_date,rank,report_type")
        time.sleep(1.5)


def patch_short(rows: list[dict], provider: str):
    """뉴스는 있는데 riseReason<200자 또는 chartAnalysis<150자인 행을 다시 생성한다
    (2026-08-04 - 초기 개발 단계 데이터 다수가 프롬프트의 최소 분량 기준에
    못 미쳤던 걸 회장님이 지적). "뉴스를 수집하지 못했습니다" 폴백 행은
    제외(정상 - 다시 생성해도 내용이 늘지 않음)."""
    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")
        call = lambda prompt: analyze_with_retry_gemini(model, prompt)  # noqa: E731
    else:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
        call = lambda prompt: analyze_with_retry_groq(client, prompt)  # noqa: E731

    targets = []
    for r in rows:
        if not r.get("news"):
            continue
        rr = r.get("rise_reason") or ""
        ca = r.get("chart_analysis") or ""
        if "뉴스 기사를 수집하지 못했습니다" in rr:
            continue
        if len(rr) < 200 or len(ca) < 150:
            targets.append(r)
    print(f"[분량 미달 재생성/{provider}] 대상 {len(targets)}행")
    for row in targets:
        prompt = build_analysis_prompt(
            row["name"], row["ticker"], row["trade_date"], float(row["change_pct"] or 0),
            row["news"], technicals=row.get("technicals"), is_weekly=(row["report_type"] == "weekly"),
        )
        text = call(prompt)
        rise, chart = parse_analysis_response(text) if text else ("", "")
        print(f"  {row['trade_date']} #{row['rank']} {row['name']} ({row['ticker']}) -> "
              f"{'OK(' + str(len(rise)) + '/' + str(len(chart)) + '자)' if rise and chart else '실패'}")
        if rise and chart:
            supabase_upsert("daily_gainers", [{
                "trade_date": row["trade_date"], "rank": row["rank"], "report_type": row["report_type"],
                "ticker": row["ticker"], "name": row["name"],
                "rise_reason": rise, "chart_analysis": chart,
            }], "trade_date,rank,report_type")
        time.sleep(1.5)


def patch_widenews(rows: list[dict], provider: str):
    """"뉴스를 수집하지 못했습니다" 상태인 행을 대상으로 당일→1주일→2주일→1개월
    순으로 넓혀가며 재검색한다(2026-08-08, 회장님 요청으로 1개월까지 확장).
    기사를 찾으면 news(날짜·상대 라벨 포함) 갱신 + riseReason/chartAnalysis도
    그 기사를 근거로 재생성. 그래도 못 찾으면 손대지 않는다(지어내지 않음)."""
    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")
        call = lambda prompt: analyze_with_retry_gemini(model, prompt)  # noqa: E731
    else:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
        call = lambda prompt: analyze_with_retry_groq(client, prompt)  # noqa: E731

    targets = [r for r in rows if "뉴스 기사를 수집하지 못했습니다" in (r.get("rise_reason") or "")]
    print(f"[뉴스 없음 - 단계적 확장 재검색(1개월까지)/{provider}] 대상 {len(targets)}행")
    for row in targets:
        articles, stage = fetch_stock_news_staged(row["ticker"], row["name"], row["trade_date"], max_articles=15)
        print(f"  {row['trade_date']} #{row['rank']} {row['name']} ({row['ticker']}) -> 기사 {len(articles)}개 ({stage})")
        if not articles:
            continue
        news = news_to_dicts(articles, row["trade_date"], stage=stage)
        prompt = build_analysis_prompt(
            row["name"], row["ticker"], row["trade_date"], float(row["change_pct"] or 0),
            articles, technicals=row.get("technicals"), is_weekly=(row["report_type"] == "weekly"),
        )
        text = call(prompt)
        rise, chart = parse_analysis_response(text) if text else ("", "")
        print(f"    -> {'OK(' + str(len(rise)) + '/' + str(len(chart)) + '자)' if rise and chart else '분석 실패'}")
        if rise and chart:
            supabase_upsert("daily_gainers", [{
                "trade_date": row["trade_date"], "rank": row["rank"], "report_type": row["report_type"],
                "ticker": row["ticker"], "name": row["name"],
                "news": news, "rise_reason": rise, "chart_analysis": chart,
            }], "trade_date,rank,report_type")
        time.sleep(1.5)


def patch_chartonly(rows: list[dict], provider: str):
    """chartAnalysis가 비어있는 모든 행을 대상으로, 기술적 지표만 근거로
    차트 해설을 채운다(2026-08-07, 회장님 요청 - "Top10 종목 하나하나 확인해서
    차트 해설 없는 것 채워달라"). 뉴스 없는 종목도 차트 분석은 기술적 지표만
    있으면 가능하므로 news 유무와 무관하게 대상에 포함한다. riseReason은
    건드리지 않는다(뉴스 없으면 여전히 "수집 못함" 문구 유지 - 정직함 유지)."""
    if provider == "gemini":
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

    targets = [r for r in rows if not (r.get("chart_analysis") or "").strip() and r.get("technicals")]
    print(f"[차트 해설 채우기/{provider}] 대상 {len(targets)}행")
    for row in targets:
        prompt = build_chart_only_prompt(
            row["name"], row["ticker"], row["trade_date"], float(row["change_pct"] or 0),
            technicals=row.get("technicals"), is_weekly=(row["report_type"] == "weekly"),
        )
        text = call(prompt)
        chart = parse_chart_only_response(text) if text else ""
        print(f"  {row['trade_date']} #{row['rank']} {row['name']} ({row['ticker']}) -> "
              f"{'OK(' + str(len(chart)) + '자)' if chart else '실패'}")
        if chart:
            supabase_upsert("daily_gainers", [{
                "trade_date": row["trade_date"], "rank": row["rank"], "report_type": row["report_type"],
                "ticker": row["ticker"], "name": row["name"],
                "chart_analysis": chart,
            }], "trade_date,rank,report_type")
        time.sleep(1.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["financials", "news", "analysis", "short", "widenews", "chartonly", "both"], required=True)
    ap.add_argument("--provider", choices=["gemini", "groq"], default="gemini")
    args = ap.parse_args()

    load_env()
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
    if args.mode in ("analysis", "short", "widenews", "chartonly"):
        required.append("GEMINI_API_KEY" if args.provider == "gemini" else "GROQ_API_KEY")
    for key in required:
        if not os.environ.get(key):
            print(f"[오류] {key}가 없습니다.")
            sys.exit(1)

    rows = fetch_all_gainer_rows()
    print(f"전체 {len(rows)}행 조회됨")

    if args.mode in ("financials", "both"):
        patch_financials(rows)
    if args.mode in ("news", "both"):
        patch_news(rows)
    if args.mode == "analysis":
        patch_analysis(rows, args.provider)
    if args.mode == "short":
        patch_short(rows, args.provider)
    if args.mode == "widenews":
        patch_widenews(rows, args.provider)
    if args.mode == "chartonly":
        patch_chartonly(rows, args.provider)

    print("\n완료!")


if __name__ == "__main__":
    main()
