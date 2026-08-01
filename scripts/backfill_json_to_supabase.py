# -*- coding: utf-8 -*-
"""
1회성 백필: 사이트 저장소에 남아있던 stock-analysis-data.json/market-scope-data.json의
과거 기록을 Supabase(daily_gainers, volume_stocks, market_scope_reports)로 옮긴다.

2026-08-01 Supabase 전환 시점에 "앞으로 새로 쓰는 데이터"만 Supabase로 보내도록
파이프라인을 바꿨는데, 이미 커밋돼 있던 과거 날짜들은 옮기지 않아서 사이트에서
날짜 탭이 줄어드는 회귀가 발생했다 - 이 스크립트로 그 과거분을 채운다.
이미 Supabase에 있는 (trade_date, rank, report_type)/(trade_date, rank)/(report_date)는
upsert이므로 덮어써도 안전하다(트랜잭션 안전, 재실행 가능).

사용법:
  python scripts/backfill_json_to_supabase.py --site-repo "../만조그룹 2차"

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
import argparse, json, os, sys
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    for fname in (".env.local", ".env"):
        path = os.path.join(ROOT, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def supabase_upsert(table: str, rows: list[dict], on_conflict: str):
    if not rows:
        return
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.post(
        f"{url}/rest/v1/{table}?on_conflict={on_conflict}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=rows,
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase {table} upsert 실패 {r.status_code}: {r.text}")
    print(f"[supabase] {table} {len(rows)}행 upsert 완료")


def backfill_gainers(site_repo: str):
    path = os.path.join(site_repo, "stock-analysis-data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    now = datetime.now(timezone.utc).isoformat()
    gainer_rows, volume_rows = [], []
    for date_str, entry in data.get("dates", {}).items():
        report_type = entry.get("type") or "daily"
        week_range = entry.get("weekRange", "")
        week_start, week_end = (week_range.split(" ~ ") + [None, None])[:2] if week_range else (None, None)

        for g in entry.get("gainers", []):
            gainer_rows.append({
                "trade_date": date_str,
                "rank": g["rank"],
                "report_type": report_type,
                "week_start": week_start,
                "week_end": week_end,
                "ticker": g["ticker"],
                "name": g["name"],
                "close": g.get("close"),
                "change_pct": g.get("changePct"),
                "trade_amount": g.get("tradeAmount"),
                "ohlcv": g.get("ohlcv", []),
                "technicals": g.get("technicals"),
                "financials": g.get("financials", {}),
                "news": g.get("news", []),
                "rise_reason": g.get("riseReason", ""),
                "chart_analysis": g.get("chartAnalysis", ""),
                "updated_at": now,
            })
        for v in entry.get("volumeStocks", []):
            volume_rows.append({
                "trade_date": date_str,
                "rank": v["rank"],
                "ticker": v["ticker"],
                "name": v["name"],
                "close": v.get("close"),
                "change_pct": v.get("changePct"),
                "trade_amount": v.get("tradeAmount"),
                "naver_url": v.get("naverUrl"),
                "investors": v.get("investors"),
                "prev_rank": v.get("prevRank"),
                "price_change": v.get("priceChange"),
                "prev_trade_amount": v.get("prevTradeAmount"),
                "updated_at": now,
            })

    print(f"[gainers] {len(data.get('dates', {}))}개 날짜 -> gainers {len(gainer_rows)}행, volumeStocks {len(volume_rows)}행")
    supabase_upsert("daily_gainers", gainer_rows, "trade_date,rank,report_type")
    supabase_upsert("volume_stocks", volume_rows, "trade_date,rank")


def backfill_market_scope(site_repo: str):
    path = os.path.join(site_repo, "market-scope-data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    now = datetime.now(timezone.utc).isoformat()
    reports = list(data.get("history", []))
    if data.get("current", {}).get("report_date"):
        reports.append(data["current"])

    rows = [{
        "report_date": r["report_date"],
        "range_label": r.get("range_label"),
        "message_count": r.get("message_count"),
        "channel_count": r.get("channel_count"),
        "items": r.get("items", []),
        "updated_at": now,
    } for r in reports]

    print(f"[market-scope] {len(rows)}개 날짜")
    supabase_upsert("market_scope_reports", rows, "report_date")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-repo", default=os.path.join(os.path.dirname(ROOT), "만조그룹 2차"))
    args = ap.parse_args()

    load_env()
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        print("[오류] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY가 없습니다.")
        sys.exit(1)

    backfill_gainers(args.site_repo)
    backfill_market_scope(args.site_repo)
    print("\n백필 완료")


if __name__ == "__main__":
    main()
