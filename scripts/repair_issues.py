# -*- coding: utf-8 -*-
"""
자동 수리: 매일 정규 수집 뒤에 돌아, 대시보드(admin.html)가 잡아내는 이슈 중
"사실관계로 확인 가능한 것"만 스스로 고치고 repair_log에 기록한다. 판단이
필요한 것은 고치지 않고 알림만 남긴다(기존처럼 - GitHub Actions 실패 시
저장소 소유자 이메일).

자동으로 고치는 것:
  1. 날짜 공백/건수 부족 (daily_gainers·volume_stocks) - backfill_krx_historical의
     backfill_date()를 그 날짜에 재실행한다. KRX Open API가 최신 며칠치를
     아직 안 올려놨을 수 있어(collect_gainers.fetch_krx_day_prices 참고),
     이번에도 안 되면 다음 실행 때 다시 시도한다(재시도이지, 포기가 아니다).
  2. 관리종목·ETF·ETN·우선주·리츠가 섞여 들어간 날 - 최근 N거래일의
     daily_gainers 행을 classify_excluded로 재검증하고, 위반이 있으면 그
     날짜 전체를 #1과 같은 방식으로 재구성한다.
  3. 상승이유·차트분석이 비어있는 행 - 뉴스 재수집 + LLM 재시도 1회. 그래도
     비면(=진짜 뉴스가 없는 경우와 구분 불가) 고치지 않고 alert_only로 남긴다
     (없는 사실을 지어내지 않기 위함).

자동으로 고치지 않는 것(알림만): 위 재시도로도 안 풀리는 것, market_scope
(자체 공백 연장 로직이 따로 있어 겹치지 않게 제외).

종료 코드: 남은 미해결 이슈가 있으면 1(기존 report-gap-check와 같은 방식으로
GitHub Actions가 실패 표시 → 저장소 소유자 이메일 알림), 전부 해결되거나
애초에 이슈가 없으면 0.

필요 환경변수 (.env.local): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, KRX_OPENAPI_KEY,
GROQ_API_KEY(재시도용 - 정규 자동화와 같은 provider를 기본으로 쓴다)
"""
import os, sys
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import (  # noqa: E402
    load_env, classify_excluded, fetch_ohlcv, calc_technicals, calc_ma_lines,
    fetch_stock_news_staged, news_to_dicts, analyze_stock, KST,
)
from krx_calendar import is_trading_day  # noqa: E402
from backfill_krx_historical import backfill_date  # noqa: E402

LOOKBACK_DAYS = 10  # 위반 재검증·분석 재시도는 최근 며칠치만 본다(전체를 매번 훑을 필요 없음)


def sb_get(table: str, query: str) -> list:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.get(f"{url}/rest/v1/{table}?{query}",
                      headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def sb_patch_rows(table: str, rows: list, on_conflict: str):
    if not rows:
        return
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.post(
        f"{url}/rest/v1/{table}?on_conflict={on_conflict}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows, timeout=30,
    )
    r.raise_for_status()


def log_repair(pipeline: str, trade_date: str | None, issue: str, action: str, success: bool, detail: dict | None = None):
    row = {
        "pipeline": pipeline, "trade_date": trade_date, "issue": issue, "action": action,
        "success": success, "detail": detail or {}, "checked_at": datetime.now(KST).isoformat(),
    }
    try:
        sb_patch_rows("repair_log", [row], "id")  # id는 bigserial이라 실제 충돌 없이 항상 insert됨
    except Exception as e:
        print(f"  [repair_log 기록 오류] {e}")
    tag = "OK" if success else "FAIL"
    print(f"  [{tag}] {pipeline} {trade_date or ''} {issue} -> {action}")


# ── 1) 날짜 공백/건수 부족 ────────────────────────────────────────────────

def find_date_gaps(start_date: str, end_date: str) -> list[str]:
    existing = {row["trade_date"] for row in sb_get(
        "daily_gainers", "select=trade_date&report_type=eq.daily&order=trade_date.asc")}
    gaps = []
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    while d <= end:
        ds = d.strftime("%Y-%m-%d")
        if is_trading_day(ds) and ds not in existing:
            gaps.append(ds)
        d += timedelta(days=1)
    return gaps


def find_short_count_dates(days: int) -> list[str]:
    """최근 days거래일 중 daily_gainers 행이 10개 미만인 날짜(부분 실패)."""
    since = (datetime.now(KST).date() - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    rows = sb_get("daily_gainers", f"select=trade_date&report_type=eq.daily&trade_date=gte.{since}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["trade_date"]] = counts.get(r["trade_date"], 0) + 1
    return sorted(d for d, n in counts.items() if n != 10)


def repair_gaps_and_counts(groq_client) -> bool:
    """모두 해결되면 True, 하나라도 못 고치면 False."""
    yesterday = (datetime.now(KST).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.now(KST).date() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    gaps = find_date_gaps(start, yesterday)
    shorts = find_short_count_dates(LOOKBACK_DAYS)
    target_dates = sorted(set(gaps) | set(shorts))
    if not target_dates:
        return True

    all_ok = True
    for date_str in target_dates:
        issue = "날짜 공백(리포트 없음)" if date_str in gaps else "건수 부족(10개 미달)"
        try:
            backfill_date(groq_client, date_str, analyze_fn=analyze_stock)
            # 재실행 후 실제로 채워졌는지 다시 확인한다 - KRX 대체소스도 아직
            # 데이터가 없으면 backfill_date가 조용히 skip할 수 있어서(같은
            # 함수의 "[skip] KRX 데이터 없음" 분기), 성공 여부를 직접 검증한다.
            after = sb_get("daily_gainers", f"select=rank&report_type=eq.daily&trade_date=eq.{date_str}")
            ok = len(after) == 10
            log_repair("gainers", date_str, issue, "backfill_rerun", ok,
                       {"rows_after": len(after)})
            all_ok = all_ok and ok
        except Exception as e:
            log_repair("gainers", date_str, issue, "backfill_rerun", False, {"error": str(e)})
            all_ok = False
    return all_ok


# ── 2) 관리종목 등 규정 위반 재검증 ────────────────────────────────────────

def find_rule_violation_dates(days: int) -> list[str]:
    since = (datetime.now(KST).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = sb_get("daily_gainers",
                  f"select=trade_date,ticker,name&report_type=eq.daily&trade_date=gte.{since}")
    bad_dates = set()
    for r in rows:
        base_dd = r["trade_date"].replace("-", "")
        if classify_excluded(r["ticker"], r["name"], base_dd=base_dd):
            bad_dates.add(r["trade_date"])
    return sorted(bad_dates)


def repair_rule_violations(groq_client) -> bool:
    bad_dates = find_rule_violation_dates(LOOKBACK_DAYS)
    if not bad_dates:
        return True
    all_ok = True
    for date_str in bad_dates:
        try:
            backfill_date(groq_client, date_str, analyze_fn=analyze_stock)
            after = sb_get("daily_gainers",
                           f"select=ticker,name&report_type=eq.daily&trade_date=eq.{date_str}")
            still_bad = any(classify_excluded(r["ticker"], r["name"], base_dd=date_str.replace("-", ""))
                            for r in after)
            log_repair("gainers", date_str, "관리종목 등 제외 대상이 Top10에 포함됨",
                      "rule_violation_fix", not still_bad, {"rows_after": len(after)})
            all_ok = all_ok and not still_bad
        except Exception as e:
            log_repair("gainers", date_str, "관리종목 등 제외 대상이 Top10에 포함됨",
                      "rule_violation_fix", False, {"error": str(e)})
            all_ok = False
    return all_ok


# ── 3) 빈 분석(rise_reason/chart_analysis) 재시도 ──────────────────────────

def find_empty_analysis_rows(days: int) -> list[dict]:
    since = (datetime.now(KST).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = sb_get(
        "daily_gainers",
        f"select=trade_date,rank,ticker,name,close,change_pct,rise_reason,chart_analysis"
        f"&report_type=eq.daily&trade_date=gte.{since}",
    )
    return [r for r in rows if not r.get("rise_reason") or not r.get("chart_analysis")]


def repair_empty_analysis(client) -> bool:
    rows = find_empty_analysis_rows(LOOKBACK_DAYS)
    if not rows:
        return True
    all_ok = True
    for r in rows:
        date_str, ticker, name = r["trade_date"], r["ticker"], r["name"]
        try:
            ohlcv_all = fetch_ohlcv(ticker, count=200)
            ohlcv = [o for o in ohlcv_all if o["date"] <= date_str]
            technicals = calc_technicals(ohlcv, r["close"], 0)
            technicals["maLines"] = calc_ma_lines(ohlcv, window=60)
            articles, stage = fetch_stock_news_staged(ticker, name, date_str, max_articles=15)
            news = news_to_dicts(articles, date_str, stage=stage)
            rise, chart = analyze_stock(client, name, ticker, date_str, r["change_pct"], articles,
                                        technicals=technicals)
            fixed = bool(rise) and bool(chart)
            if fixed:
                sb_patch_rows("daily_gainers", [{
                    "trade_date": date_str, "rank": r["rank"], "report_type": "daily",
                    "rise_reason": rise, "chart_analysis": chart, "news": news,
                    "updated_at": datetime.now(KST).isoformat(),
                }], "trade_date,rank,report_type")
                log_repair("gainers", date_str, f"{name} 상승이유/차트분석 공란", "analysis_retry", True,
                          {"ticker": ticker, "articles": len(articles)})
            else:
                # 재시도해도 안 채워짐 - 진짜 뉴스가 없는 경우와 구분이 안 되므로
                # 값을 지어내지 않고 알림만 남긴다.
                log_repair("gainers", date_str, f"{name} 상승이유/차트분석 공란", "alert_only", False,
                          {"ticker": ticker, "articles": len(articles), "note": "재시도해도 공란 - 수동 확인 필요"})
                all_ok = False
        except Exception as e:
            log_repair("gainers", date_str, f"{name} 상승이유/차트분석 공란", "alert_only", False,
                      {"ticker": ticker, "error": str(e)})
            all_ok = False
    return all_ok


def main():
    load_env()
    for k in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]:
        if not os.environ.get(k):
            print(f"[오류] {k}가 없습니다.")
            sys.exit(1)

    # 정규 자동화와 같은 provider(Groq)를 기본으로 쓴다 - 매일 조금씩만
    # 도는 수리 작업이라 별도 Gemini 할당량까지는 필요 없다고 보고 설계함.
    # (call_groq_with_retry 자체에 120b→20b 모델 자동 폴백이 이미 들어있다.)
    if not os.environ.get("GROQ_API_KEY"):
        print("[오류] GROQ_API_KEY가 없습니다.")
        sys.exit(1)
    from groq import Groq
    groq_client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)

    print(f"[자동 수리 시작] {datetime.now(KST).isoformat()}")

    ok1 = repair_gaps_and_counts(groq_client)
    ok2 = repair_rule_violations(groq_client)
    ok3 = repair_empty_analysis(groq_client)

    print(f"[자동 수리 종료] 날짜공백/건수={ok1}, 규정위반={ok2}, 빈분석={ok3}")
    if ok1 and ok2 and ok3:
        print("모든 이슈가 자동으로 해결됐습니다(또는 애초에 없었습니다).")
        sys.exit(0)
    print("일부 이슈는 자동으로 해결되지 않았습니다 - repair_log를 확인하세요.")
    sys.exit(1)


if __name__ == "__main__":
    main()
