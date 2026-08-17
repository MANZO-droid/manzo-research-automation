# -*- coding: utf-8 -*-
"""
1회성 패치: 확장된 has_language_issue()(2026-08-12, 일본어→태국어/베트남어/
중국어 한자로 확장, collect_gainers.py 70df67e)로 다시 검사해서 걸리는
daily_gainers 행의 riseReason/chartAnalysis를 재생성한다.

배경: 회장님이 Supabase 데이터를 옵시디언 볼트로 옮기다가 태국어(สำค)·
베트남어(tương)·중국어 한자(反应) 혼입을 발견(310행 중 205행). 뉴스·
기술적 지표는 이미 저장돼 있는 걸 재사용하고(재수집 안 함), LLM 응답만
다시 만든다.

뉴스가 실제로 있는 행은 build_analysis_prompt로 riseReason+chartAnalysis
둘 다, 뉴스가 없는(폴백 문구) 행은 build_chart_only_prompt로 chartAnalysis만
다시 만든다(riseReason의 "뉴스 기사를 수집하지 못했습니다" 폴백 문구는
항상 순수 한국어라 오염될 수 없음 - 안 건드림).

technicals.languageCleanVersion=1 마커로 멱등적 재실행 가능(patch_trend_technicals.py와
동일 패턴) - 재생성 후에도 여전히 오염 판정이면 마커를 심지 않고 기존 텍스트를
그대로 둔다(더 나쁜 텍스트로 덮어쓰지 않기 위함 - 다음 실행에서 다시 시도).
--minutes로 시간 예산을 줄 수 있다(GH Actions timeout-minutes: 60 방지).

사용법:
  python scripts/patch_language_contamination.py --provider gemini
  python scripts/patch_language_contamination.py --provider groq --minutes 45

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  --provider gemini(기본): GEMINI_API_KEY / --provider groq: GROQ_API_KEY
"""
import argparse, os, sys, time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (  # noqa: E402
    load_env, has_language_issue, build_analysis_prompt, parse_analysis_response,
    build_chart_only_prompt, parse_chart_only_response, KST,
)
from patch_gainer_fields import analyze_with_retry_gemini, analyze_with_retry_groq  # noqa: E402

LANG_CLEAN_VERSION = 1


def fetch_rows():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.get(
        f"{url}/rest/v1/daily_gainers?select=trade_date,rank,report_type,ticker,name,"
        f"change_pct,news,technicals,rise_reason,chart_analysis&report_type=eq.daily"
        f"&order=trade_date.asc,rank.asc&limit=1000",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def patch_row(trade_date: str, rank: int, report_type: str, fields: dict):
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    fields["updated_at"] = datetime.now(KST).isoformat()
    r = requests.patch(
        f"{url}/rest/v1/daily_gainers?trade_date=eq.{trade_date}&rank=eq.{rank}&report_type=eq.{report_type}",
        headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": "return=minimal",
        },
        json=fields, timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"PATCH 실패 {r.status_code}: {r.text}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["gemini", "groq"], default="gemini")
    ap.add_argument("--minutes", type=float, default=None)
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

    all_rows = fetch_rows()
    targets = []
    for row in all_rows:
        if (row.get("technicals") or {}).get("languageCleanVersion") == LANG_CLEAN_VERSION:
            continue
        text = (row.get("rise_reason") or "") + (row.get("chart_analysis") or "")
        if has_language_issue(text):
            targets.append(row)
    print(f"조회 {len(all_rows)}행 / 언어 오염 대상 {len(targets)}행")

    ok, fail, budget_stopped = 0, 0, 0
    for row in targets:
        if deadline and time.monotonic() >= deadline:
            budget_stopped = len(targets) - ok - fail
            print(f"\n[시간 예산 소진] {args.minutes}분 경과 - 남은 {budget_stopped}행은 다음 실행에서 이어감")
            break

        has_real_news = bool(row.get("news")) and "뉴스 기사를 수집하지 못했습니다" not in (row.get("rise_reason") or "")
        name, ticker, trade_date = row["name"], row["ticker"], row["trade_date"]

        if has_real_news:
            prompt = build_analysis_prompt(
                name, ticker, trade_date, float(row["change_pct"] or 0),
                row["news"], technicals=row.get("technicals"), is_weekly=(row["report_type"] == "weekly"),
            )
            text = call(prompt)
            rise, chart = parse_analysis_response(text) if text else ("", "")
            still_bad = has_language_issue(rise + chart)
            if not rise or not chart or still_bad:
                print(f"  [실패] {trade_date} #{row['rank']} {name}({ticker}) - "
                      f"{'재생성도 오염' if still_bad else 'LLM 응답 없음'}")
                fail += 1
                time.sleep(1.5)
                continue
            technicals = dict(row.get("technicals") or {})
            technicals["languageCleanVersion"] = LANG_CLEAN_VERSION
            patch_row(trade_date, row["rank"], row["report_type"],
                      {"rise_reason": rise, "chart_analysis": chart, "technicals": technicals})
        else:
            prompt = build_chart_only_prompt(
                name, ticker, trade_date, float(row["change_pct"] or 0),
                technicals=row.get("technicals"), is_weekly=(row["report_type"] == "weekly"),
            )
            text = call(prompt)
            chart = parse_chart_only_response(text) if text else ""
            still_bad = has_language_issue(chart)
            if not chart or still_bad:
                print(f"  [실패] {trade_date} #{row['rank']} {name}({ticker}) - "
                      f"{'재생성도 오염' if still_bad else 'LLM 응답 없음'}")
                fail += 1
                time.sleep(1.5)
                continue
            technicals = dict(row.get("technicals") or {})
            technicals["languageCleanVersion"] = LANG_CLEAN_VERSION
            patch_row(trade_date, row["rank"], row["report_type"],
                      {"chart_analysis": chart, "technicals": technicals})

        print(f"  [OK] {trade_date} #{row['rank']} {name}({ticker}) - {'전체 재생성' if has_real_news else '차트만'}")
        ok += 1
        time.sleep(1.5)

    print(f"\n완료: 성공 {ok}건 / 실패 {fail}건 / 시간 예산으로 이월 {budget_stopped}건")


if __name__ == "__main__":
    main()
