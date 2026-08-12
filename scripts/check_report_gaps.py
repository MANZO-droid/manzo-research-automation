# -*- coding: utf-8 -*-
"""
날짜 공백 자동 감지: daily_gainers(report_type='daily')에 있어야 할
거래일 리포트가 빠져 있는지 매일 점검한다.

배경: 2026-08-10(월요일, 정상 거래일)에 정규 자동화(gainers-daily.yml)가
Groq 429를 4회 재시도해도 못 풀어서 그날 리포트 전체를 조용히 건너뛴
사고가 있었다(회장님이 화면 보고 직접 발견 - AUTOMATION_NOTES.md
2026-08-12 참고). 이런 공백을 사람이 매번 눈으로 찾지 않도록 자동화.

범위: "당일"(daily) 리포트만 검사한다. 주간(weekly) 리포트는 발행일
계산 규칙(krx_calendar.py 상단 주석 참고)이 더 복잡해 이번엔 다루지
않았다 - 필요하면 별도로 확장.

판정 기준: START_DATE부터 "어제"(한국시간)까지의 모든 거래일
(krx_calendar.is_trading_day)에 daily_gainers(report_type='daily') 행이
있는지 확인한다. 오늘은 아직 정규 자동화가 안 돌았을 수 있어 제외한다.

공백이 있으면 비정상 종료(exit 1)한다 - GitHub Actions에서 이 스크립트를
돌리는 워크플로가 실패로 표시되고, 저장소 소유자에게 GitHub 기본 알림
메일이 간다(별도 Slack/이메일 연동 없이 기존 인프라만으로 알림).

사용법:
  python scripts/check_report_gaps.py
  python scripts/check_report_gaps.py --start 2026-07-02

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
import argparse, os, sys
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env, KST  # noqa: E402
from krx_calendar import is_trading_day  # noqa: E402

DEFAULT_START = "2026-07-02"  # 이 파이프라인이 실제로 데이터를 쌓기 시작한 첫 날짜


def fetch_existing_daily_dates() -> set:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.get(
        f"{url}/rest/v1/daily_gainers?select=trade_date&report_type=eq.daily&order=trade_date.asc",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30,
    )
    r.raise_for_status()
    return {row["trade_date"] for row in r.json()}


def find_gaps(start_date: str, end_date: str, existing: set) -> list[str]:
    gaps = []
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    while d <= end:
        ds = d.strftime("%Y-%m-%d")
        if is_trading_day(ds) and ds not in existing:
            gaps.append(ds)
        d += timedelta(days=1)
    return gaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START)
    args = ap.parse_args()

    load_env()
    for k in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]:
        if not os.environ.get(k):
            print(f"[오류] {k}가 없습니다.")
            sys.exit(1)

    yesterday = (datetime.now(KST).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    existing = fetch_existing_daily_dates()
    gaps = find_gaps(args.start, yesterday, existing)

    print(f"점검 구간: {args.start} ~ {yesterday} (오늘은 아직 수집 전일 수 있어 제외)")
    print(f"기존 daily 리포트: {len(existing)}개")

    if not gaps:
        print("공백 없음 - 모든 거래일에 daily 리포트가 있습니다.")
        sys.exit(0)

    print(f"\n[공백 발견] {len(gaps)}개 거래일에 daily_gainers 리포트가 없습니다:")
    for d in gaps:
        print(f"  - {d}")
    print(
        "\n복구 방법: python scripts/backfill_krx_historical.py --dates "
        + ",".join(gaps)
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
