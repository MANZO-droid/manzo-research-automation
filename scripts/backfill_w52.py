# -*- coding: utf-8 -*-
"""일회성: daily_gainers 전 행의 technicals.w52High/w52Low(+pctFromHigh/pctFromLow)를
calc_w52(직전 365일, 0값은 종가 대체)로 재계산해 덮어쓴다. 다른 지표(ADX 등)는 건드리지 않는다.
2026-09-26 회장님 지적("52주 고가/저가 조회가 안 된다") 전수조사 후 작성."""
import os, sys, time, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env, fetch_ohlcv, calc_w52

load_env()
url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

rows, off = [], 0
while True:
    b = requests.get(f"{url}/rest/v1/daily_gainers", params={"select": "id,trade_date,ticker,technicals", "order": "trade_date.asc,rank.asc", "limit": 1000, "offset": off}, headers=h).json()
    rows += b
    if len(b) < 1000:
        break
    off += 1000
print(f"{len(rows)}행")

cache, changed, same, nodata = {}, 0, 0, []
for i, r in enumerate(rows):
    t = r.get("technicals") or {}
    if r["ticker"] not in cache:
        cache[r["ticker"]] = fetch_ohlcv(r["ticker"], count=400)
        time.sleep(0.15)
    w = calc_w52(cache[r["ticker"]], r["trade_date"])
    if not w:
        nodata.append((r["trade_date"], r["ticker"]))
        continue
    hi, lo = w
    cur = t.get("current")
    new = dict(t)
    new["w52High"], new["w52Low"] = hi, lo
    if cur:
        new["pctFromHigh"] = round((cur - hi) / hi * 100, 1)
        new["pctFromLow"] = round((cur - lo) / lo * 100, 1) if lo else 0
    if new == t:
        same += 1
        continue
    p = requests.patch(f"{url}/rest/v1/daily_gainers", params={"id": f"eq.{r['id']}"}, headers=h, json={"technicals": new})
    if p.status_code != 204:
        print("PATCH FAIL", r["trade_date"], r["ticker"], p.status_code)
    changed += 1
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(rows)} changed={changed}")
print(f"완료: changed={changed} same={same} nodata={len(nodata)}")
print("nodata:", nodata)
