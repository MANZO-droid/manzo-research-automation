# -*- coding: utf-8 -*-
"""
1회성 수정: volume_stocks.investors(기관·외국인·개인 순매수 금액)를 재계산해
저장한다.

배경: fetch_investor_netbuy()가 soup.select("table")[3] 고정 인덱스로
순매수 표를 찾다가, ETF/ETN처럼 앞쪽 "주요 시세" 표가 1개뿐인 종목에서
페이지네비게이션 표를 잘못 읽는 버그가 있었다(회장님 지적 - 거래대금
94억원짜리 종목의 기관 순매수가 582원으로 나옴). summary 텍스트 기반
검색 + 총거래량 대비 정합성 검사로 수정(collect_gainers.py 2026-08-08).

이 스크립트는 기존 volume_stocks 전체 행을 다시 조회해 investors만
재계산·upsert한다(다른 필드는 그대로 둠).

사용법:
  python scripts/fix_investor_netbuy.py            # 전체 재계산
  python scripts/fix_investor_netbuy.py --dates 2026-08-03,2026-08-04

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
import argparse, os, sys, time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env, fetch_investor_netbuy, KST  # noqa: E402


def patch_investors(trade_date: str, rank: int, investors: dict, updated_at: str):
    """POST upsert(on_conflict)가 이 프로젝트에서 기존 행을 못 찾고 새 행을
    만들려다 NOT NULL 위반으로 실패하는 문제가 있어(2026-08-08 발견, 원인
    미상 - unique(trade_date, rank) 제약은 존재하는데도 매칭 안 됨), 필터
    기반 PATCH로 우회한다. 행이 이미 존재함을 미리 조회로 확인했으므로
    PATCH만으로 충분하다."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.patch(
        f"{url}/rest/v1/volume_stocks?trade_date=eq.{trade_date}&rank=eq.{rank}",
        headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": "return=representation",
        },
        json={"investors": investors, "updated_at": updated_at},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"PATCH 실패 {r.status_code}: {r.text}")
    return r.json()


def fetch_all_rows(dates: list[str] | None) -> list[dict]:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    q = f"{url}/rest/v1/volume_stocks?select=trade_date,rank,ticker,name,close,trade_amount,investors&order=trade_date.asc,rank.asc"
    if dates:
        date_list = ",".join(dates)
        q += f"&trade_date=in.({date_list})"
    r = requests.get(q, headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", help="쉼표로 구분된 날짜 목록 (생략시 전체)")
    args = ap.parse_args()

    load_env()
    for k in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]:
        if not os.environ.get(k):
            print(f"[오류] {k}가 없습니다.")
            sys.exit(1)

    dates = args.dates.split(",") if args.dates else None
    rows = fetch_all_rows(dates)
    print(f"대상 {len(rows)}행")

    changed, unchanged, zeroed = [], 0, 0
    for row in rows:
        old_inv = row.get("investors") or {}
        new_inv = fetch_investor_netbuy(
            row["ticker"], row["close"], target_date=row["trade_date"],
            trade_amount=row.get("trade_amount"),
        )
        if new_inv == {"individual": 0, "institution": 0, "foreign": 0} and old_inv.get("institution") != 0:
            zeroed += 1
            print(f"  [이상치→0] {row['trade_date']} #{row['rank']} {row['name']}({row['ticker']}) "
                  f"기존={old_inv}")
        if new_inv != old_inv:
            changed.append({
                "trade_date": row["trade_date"], "rank": row["rank"],
                "investors": new_inv, "updated_at": datetime.now(KST).isoformat(),
            })
            print(f"  [변경] {row['trade_date']} #{row['rank']} {row['name']}({row['ticker']}) "
                  f"{old_inv} -> {new_inv}")
        else:
            unchanged += 1
        time.sleep(0.3)

    print(f"\n변경 {len(changed)}건 / 동일 {unchanged}건 / 이상치→0 {zeroed}건")
    if changed:
        ok, fail = 0, []
        for c in changed:
            try:
                patch_investors(c["trade_date"], c["rank"], c["investors"], c["updated_at"])
                ok += 1
            except Exception as e:
                fail.append((c["trade_date"], c["rank"], str(e)))
                print(f"  [PATCH 실패] {c['trade_date']} #{c['rank']}: {e}")
        print(f"PATCH 완료 {ok}건 / 실패 {len(fail)}건")


if __name__ == "__main__":
    main()
