# -*- coding: utf-8 -*-
"""52주 고/저가 교정(backfill_w52.py) 이후, 옛 52주 수치를 인용한 차트분석을 재생성한다.
대상: 옛 로직(일봉 200개 극값)으로 재현한 52주 값이 교정값과 달라진 행(=backfill_w52.py로 바뀐 행).
technicals.w52ChartVersion 마커로 재실행 시 이어서 처리(멱등). --dry-run은 대상 수만 출력."""
import argparse, os, re, sys, time, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (load_env, build_chart_only_prompt, parse_chart_only_response,
                             call_groq_with_retry, GroqQuotaExhausted)

VER = 1
ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--minutes", type=float, default=0)
args = ap.parse_args()
load_env()
url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

rows, off = [], 0
while True:
    b = requests.get(f"{url}/rest/v1/daily_gainers", params={"select": "id,trade_date,rank,report_type,ticker,name,change_pct,technicals,chart_analysis", "order": "trade_date.asc,rank.asc", "limit": 1000, "offset": off}, headers=h).json()
    rows += b
    if len(b) < 1000:
        break
    off += 1000

from collect_gainers import fetch_ohlcv
_cache = {}
def old_w52(r):
    """backfill_w52.py 이전 로직(수집 당시 일봉 200개의 high/low 극값)을 재현한다."""
    if r["ticker"] not in _cache:
        _cache[r["ticker"]] = fetch_ohlcv(r["ticker"], count=400); time.sleep(0.15)
    o = [x for x in _cache[r["ticker"]] if x["date"] <= r["trade_date"]][-200:]
    return (max(x["high"] for x in o), min(x["low"] for x in o)) if o else None

def changed(r):
    t = r["technicals"] or {}
    if not t.get("w52Low"):
        return False
    old = old_w52(r)
    return bool(old) and old != (t.get("w52High"), t.get("w52Low"))

todo = [r for r in rows if (r["technicals"] or {}).get("w52ChartVersion") != VER and changed(r)]
print(f"전체 {len(rows)} / 재생성 대상 {len(todo)}")
if args.dry_run:
    sys.exit(0)

from groq import Groq
client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
deadline = time.monotonic() + args.minutes * 60 if args.minutes else None
ok = fail = 0
for r in todo:
    if deadline and time.monotonic() >= deadline:
        print("[시간 예산 소진] 남은 대상은 다음 실행에서 이어감"); break
    t = dict(r["technicals"])
    try:
        text = call_groq_with_retry(client, build_chart_only_prompt(
            r["name"], r["ticker"], r["trade_date"], float(r["change_pct"] or 0), technicals=t,
            is_weekly=(r["report_type"] == "weekly")))
    except GroqQuotaExhausted as e:
        print(f"[Groq 할당량 소진] {e} - 여기서 중단(재실행 시 이어감)"); break
    chart = parse_chart_only_response(text) if text else ""
    if not chart:
        fail += 1; print("  [실패]", r["trade_date"], r["ticker"]); continue
    t["w52ChartVersion"] = VER
    p = requests.patch(f"{url}/rest/v1/daily_gainers", params={"id": f"eq.{r['id']}"}, headers=h, json={"chart_analysis": chart, "technicals": t})
    ok += p.status_code == 204
    if ok % 25 == 0:
        print(f"  진행 ok={ok} fail={fail}")
    time.sleep(1.5)
print(f"완료: ok={ok} fail={fail} 남은={len(todo)-ok-fail}")
