# -*- coding: utf-8 -*-
"""
1회성 수정: riseReason/chartAnalysis가 한국어가 아니라 일본어 등 다른 언어로
작성된 행을 지정해서 다시 생성한다(회장님이 8/3 티웨이홀딩스를 예로 지적).

원인: Groq(llama-3.3-70b-versatile)가 "반드시 순수 한국어로만 작성하세요"
지시를 가끔 무시하고 일본어로 응답한 사례 발견(전체 250행 중 7건, 히라가나/
가타카나 문자 존재로 탐지). 이미 저장된 news/technicals를 재사용해 다시
생성만 한다(뉴스 재수집 안 함).

사용법:
  python scripts/fix_language_issues.py --provider gemini
"""
import argparse, os, sys, time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (  # noqa: E402
    load_env, build_analysis_prompt, parse_analysis_response, supabase_upsert,
)

TARGETS = [
    ("2026-08-06", 7),
]


def fetch_row(date_str: str, rank: int) -> dict:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.get(
        f"{url}/rest/v1/daily_gainers?trade_date=eq.{date_str}&rank=eq.{rank}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=20,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["gemini", "groq"], default="gemini")
    args = ap.parse_args()

    load_env()
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
    required.append("GEMINI_API_KEY" if args.provider == "gemini" else "GROQ_API_KEY")
    for key in required:
        if not os.environ.get(key):
            print(f"[오류] {key}가 없습니다.")
            sys.exit(1)

    from collect_gainers import has_language_issue

    if args.provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")

        def call(prompt):
            wait = 30
            for attempt in range(4):
                try:
                    text = model.generate_content(prompt).text
                    if has_language_issue(text):
                        print(f"    [언어 오염] 일본어 감지, 재시도 ({attempt+1}/4)...")
                        continue
                    return text
                except Exception as e:
                    if "429" not in str(e):
                        print(f"    [오류] {e}")
                        return ""
                    print(f"    [429] {wait}초 대기 ({attempt+1}/4)...")
                    time.sleep(wait)
                    wait = min(wait * 2, 120)
            return ""
    else:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)

        def call(prompt):
            wait = 60
            for attempt in range(4):
                try:
                    resp = client.chat.completions.create(
                        model="llama-3.3-70b-versatile", max_tokens=1024,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = resp.choices[0].message.content or ""
                    if has_language_issue(text):
                        print(f"    [언어 오염] 일본어 감지, 재시도 ({attempt+1}/4)...")
                        continue
                    return text
                except Exception as e:
                    if "429" not in str(e) and "rate_limit" not in str(e).lower():
                        print(f"    [오류] {e}")
                        return ""
                    print(f"    [429] {wait}초 대기 ({attempt+1}/4)...")
                    time.sleep(wait)
                    wait = min(wait * 2, 120)
            return ""

    for date_str, rank in TARGETS:
        row = fetch_row(date_str, rank)
        if not row:
            print(f"[skip] {date_str} #{rank} - 행 없음")
            continue
        print(f"\n{date_str} #{rank} {row['name']} ({row['ticker']}) 재생성 중...")
        prompt = build_analysis_prompt(
            row["name"], row["ticker"], date_str, float(row["change_pct"] or 0),
            row["news"], technicals=row.get("technicals"), is_weekly=(row["report_type"] == "weekly"),
        )
        # 언어 문제 재발 방지 - 프롬프트 끝에 한 번 더 강조
        prompt += "\n\n(다시 강조: 응답은 반드시 100% 한국어로만 작성하세요. 일본어·중국어·영어를 섞지 마세요.)"
        text = call(prompt)
        rise, chart = parse_analysis_response(text) if text else ("", "")
        ok = bool(rise and chart)
        print(f"  -> {'OK' if ok else '실패'}")
        if ok:
            supabase_upsert("daily_gainers", [{
                "trade_date": date_str, "rank": rank, "report_type": row["report_type"],
                "ticker": row["ticker"], "name": row["name"],
                "rise_reason": rise, "chart_analysis": chart,
            }], "trade_date,rank,report_type")
        time.sleep(1.5)

    print("\n완료!")


if __name__ == "__main__":
    main()
