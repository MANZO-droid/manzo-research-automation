# -*- coding: utf-8 -*-
"""
당일/주간 상승률 상위 10위 자동 수집 스크립트

무엇을 하나:
  1) 네이버 증권에서 당일 KOSPI+KOSDAQ 상승률 상위 종목 실시간 수집
  2) 종목별 OHLCV 120일치 → 이동평균·거래량비율 등 기술적 지표 계산
  3) 네이버 금융 뉴스 10개 이상 수집
  4) Groq(Llama 3.3 70B, 무료 API)로 상승이유·차트분석 작성
  5) Supabase(daily_gainers, volume_stocks)에 upsert → 사이트가 /api/top-gainers로 즉시 조회

사용법:
  python scripts/collect_gainers.py               # 인자 없이 실행 = 무인 자동 실행
                                                    #   krx_calendar.get_weekly_report_trigger()로
                                                    #   오늘 발행 여부와 daily/weekly 모드를 자동 결정.
                                                    #   (개장일 아니면 아무것도 안 하고 종료)
  python scripts/collect_gainers.py --date 2026-07-17          # 수동 지정(구버전 방식, 캘린더 미고려)
  python scripts/collect_gainers.py --mode weekly               # 주간 리포트 강제 실행(수동)

자동화:
  - GitHub Actions(.github/workflows/gainers-daily.yml)에서 인자 없이 매일 호출 →
    krx_calendar 기준으로 daily/weekly/스킵을 자동 결정 (Task 1 참고, AUTOMATION_NOTES.md).
  - 기존 Windows 작업 스케줄러(평일 4시 daily / 토요일 4시 weekly, 인자로 모드 강제)는
    GitHub Actions 전환 후 중복 실행 방지를 위해 비활성화 권장.

필요 환경변수 (.env.local):
  GROQ_API_KEY               (2026-08-01부로 무료 티어 유지를 위해 Anthropic Claude에서 교체됨)
  SUPABASE_URL                사이트가 쓰는 것과 동일한 Supabase 프로젝트
  SUPABASE_SERVICE_ROLE_KEY   RLS를 우회해 daily_gainers/volume_stocks에 쓰기 위한 서버 전용 키
                               (2026-08-01부로 stock-analysis-data.json 하드코딩 대신 이걸 사용)
"""
import argparse, os, re, sys, time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

from groq import Groq, RateLimitError, APIStatusError
import requests
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")

# scripts/ 에서 한 단계 위가 이 저장소(리서치자동화)의 루트다. .env.local이 여기 있다.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from krx_calendar import is_trading_day, get_weekly_report_trigger  # noqa: E402
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
}
GROQ_MODEL = "llama-3.3-70b-versatile"
KST = timezone(timedelta(hours=9))

# ─── Top10 제외 대상 판별 (우선주·ETF·ETN / 관리종목·정리매매는 확인 필요) ────────

_ETF_TICKER_CACHE: set | None = None


def _get_etf_tickers() -> set:
    """네이버 ETF 목록 API에서 ETF 종목코드 전체를 가져와 캐시한다 (로그인 불필요)."""
    global _ETF_TICKER_CACHE
    if _ETF_TICKER_CACHE is not None:
        return _ETF_TICKER_CACHE
    try:
        r = requests.get(
            "https://finance.naver.com/api/sise/etfItemList.nhn",
            headers=HEADERS, timeout=15,
        )
        items = r.json().get("result", {}).get("etfItemList", [])
        _ETF_TICKER_CACHE = {item["itemcode"] for item in items}
    except Exception as e:
        print(f"  [ETF 목록 수집 오류] {e}")
        _ETF_TICKER_CACHE = set()
    return _ETF_TICKER_CACHE


_ADMIN_ISSUE_CACHE: dict[str, dict] = {}  # base_dd(YYYYMMDD) -> {ticker: reason}


def _get_admin_issue_tickers(base_dd: str) -> dict:
    """KRX Open API(KRX_OPENAPI_KEY)의 종목기본정보에서 SECT_TP_NM(소속부)에
    "관리종목"·"정리매매"가 포함된 종목을 조회해 {ticker: 사유} 로 반환한다.
    2026-08-02 추가 - 케이엠제약(225430)이 관리종목인데 Top10에 섞여 있던 걸
    회장님이 지적해 발견(그 전까지는 이 필터가 아예 없었음, KRX_ID/KRX_PW
    로그인이 필요한 줄 알았는데 KRX_OPENAPI_KEY로 로그인 없이 조회 가능했다)."""
    global _ADMIN_ISSUE_CACHE
    if base_dd in _ADMIN_ISSUE_CACHE:
        return _ADMIN_ISSUE_CACHE[base_dd]
    result: dict = {}
    key = os.environ.get("KRX_OPENAPI_KEY")
    if not key:
        _ADMIN_ISSUE_CACHE[base_dd] = result
        return result
    for market in ("stk", "ksq"):
        try:
            r = requests.get(
                f"https://data-dbg.krx.co.kr/svc/apis/sto/{market}_isu_base_info",
                headers={"AUTH_KEY": key}, params={"basDd": base_dd}, timeout=20,
            )
            for row in r.json().get("OutBlock_1", []):
                sect = row.get("SECT_TP_NM", "")
                if "관리종목" in sect:
                    result[row["ISU_SRT_CD"]] = "관리종목"
                elif "정리매매" in sect:
                    result[row["ISU_SRT_CD"]] = "정리매매"
        except Exception as e:
            print(f"  [관리종목 조회 오류] {market} {base_dd}: {e}")
    _ADMIN_ISSUE_CACHE[base_dd] = result
    return result


def classify_excluded(ticker: str, name: str, base_dd: str | None = None) -> str | None:
    """Top10에서 제외해야 하면 사유 문자열, 포함해도 되면 None을 반환한다.

    확인된 사실: ETN은 상품명에 항상 "ETN"이 포함되고, 우선주는 종목명이
    (숫자)우(B)로 끝나는 KRX 표기 관례를 따른다(예: 진흥기업2우B). ETF는
    브랜드명(KODEX/TIGER 등)만으로 이름에서 판별할 수 없어 네이버 ETF
    목록 API로 종목코드를 직접 대조한다. 관리종목·정리매매는 KRX Open API
    종목기본정보의 SECT_TP_NM(소속부)로 판별한다(KRX_OPENAPI_KEY 필요 -
    없으면 이 검사만 조용히 건너뛴다). 리츠는 종목명이 항상 "리츠"로
    끝나는 KRX 표기 관례로 판별한다(예: 마스턴프리미어리츠, SK리츠 -
    2026-08-04 추가, 재무정보 표(기업실적분석)가 없어 데이터가 항상
    비는 문제가 있어 회장님이 제외 요청).

    base_dd(YYYYMMDD)를 주면 그 날짜 기준으로 조회하고(백필용), 생략하면
    오늘(KST) 기준 - 매일 자동 실행은 실행 당일 상태만 알면 되므로 충분하다.
    """
    if "ETN" in name.upper():
        return "ETN"
    if ticker in _get_etf_tickers():
        return "ETF"
    if re.search(r"\d?우(B)?$", name):
        return "우선주"
    if name.endswith("리츠"):
        return "리츠"
    if base_dd is None:
        base_dd = datetime.now(KST).strftime("%Y%m%d")
    admin_issue = _get_admin_issue_tickers(base_dd)
    if ticker in admin_issue:
        return admin_issue[ticker]
    return None


# ─── 환경변수 로드 ────────────────────────────────────────────────────────────

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


# ─── 네이버 증권 상승률 상위 수집 ─────────────────────────────────────────────

def fetch_top_gainers(market_url: str, top_n: int = 20) -> list[dict]:
    """네이버 증권 상승률 상위 페이지에서 종목 수집."""
    stocks = []
    try:
        r = requests.get(market_url, headers=HEADERS, timeout=15)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type_2 tr")
        for row in rows:
            tds = row.select("td")
            if len(tds) < 10:
                continue
            a = tds[1].find("a")
            if not a:
                continue
            href = a.get("href", "")
            # 정식 종목은 항상 6자리 숫자 코드. "0197X0"처럼 문자가 섞인 코드는
            # 개별종목 선물연계 ETN 등 파생상품이므로 애초에 후보에 넣지 않는다.
            m = re.search(r"code=(\d{6})(?![0-9A-Za-z])", href)
            if not m:
                continue
            ticker = m.group(1)
            name = a.get_text(strip=True)
            close_raw = tds[2].get_text(strip=True).replace(",", "")
            rate_raw = tds[4].get_text(strip=True).replace("+", "").replace("%", "").replace(",", "")
            vol_raw = tds[5].get_text(strip=True).replace(",", "") if len(tds) > 5 else "0"
            try:
                close = int(close_raw)
                change_pct = float(rate_raw)
                volume = int(vol_raw) if vol_raw.replace("0","").isdigit() or vol_raw.isdigit() else 0
            except Exception:
                continue
            stocks.append({
                "ticker": ticker,
                "name": name,
                "close": close,
                "changePct": change_pct,
                "volume": volume,
                "tradeAmount": close * volume,
            })
            if len(stocks) >= top_n:
                break
    except Exception as e:
        print(f"  [수집 오류] {market_url}: {e}")
    return stocks


def save_raw_candidates(date_str: str, kospi: list[dict], kosdaq: list[dict]):
    """평일 실행마다 그날의 원본 상승률 후보(KOSPI+KOSDAQ)를 raw_top_candidates에
    저장한다. 주간(weekly) 리포트가 실시간 재조회 대신 이 표에서 실제 일별
    등락률을 읽어 복리 계산할 수 있게 하기 위함(2026-08-01 버그 수정 - 예전엔
    '오늘' 데이터를 5번 반복 조회해 복리 계산하는 바람에 수치가 틀렸었다)."""
    now = datetime.now(KST).isoformat()
    rows = [
        {
            "trade_date": date_str, "market": "kospi", "ticker": s["ticker"], "name": s["name"],
            "close": s["close"], "change_pct": s["changePct"], "trade_amount": s.get("tradeAmount"),
            "updated_at": now,
        }
        for s in kospi
    ] + [
        {
            "trade_date": date_str, "market": "kosdaq", "ticker": s["ticker"], "name": s["name"],
            "close": s["close"], "change_pct": s["changePct"], "trade_amount": s.get("tradeAmount"),
            "updated_at": now,
        }
        for s in kosdaq
    ]
    supabase_upsert("raw_top_candidates", rows, "trade_date,ticker")


def fetch_weekly_candidates_from_db(week_start: str, week_end: str) -> dict:
    """raw_top_candidates에서 week_start~week_end 구간의 실제 일별 등락률을 모아
    ticker별 복리 누적 등락률을 계산한다(예전처럼 실시간 페이지를 반복 재조회하지
    않고, 그 주 각 평일 실행 때 실제로 저장해둔 값을 사용 - 진짜 주간 등락률)."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = requests.get(
        f"{url}/rest/v1/raw_top_candidates?select=trade_date,ticker,name,close,change_pct"
        f"&trade_date=gte.{week_start}&trade_date=lte.{week_end}&order=trade_date.asc",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()

    weekly_map: dict[str, dict] = {}
    for row in rows:
        t = row["ticker"]
        if t not in weekly_map:
            weekly_map[t] = {"ticker": t, "name": row["name"], "close": row["close"], "daily_changes": []}
        weekly_map[t]["name"] = row["name"]
        weekly_map[t]["close"] = row["close"]  # 마지막(최신) 값으로 갱신
        weekly_map[t]["daily_changes"].append(row["change_pct"])
    return weekly_map


def get_daily_top10(date_str: str) -> list[dict]:
    """KOSPI+KOSDAQ 합산 상승률 상위 10종목 반환 (우선주·ETF·ETN 제외)."""
    kospi = fetch_top_gainers("https://finance.naver.com/sise/sise_rise.naver", top_n=40)
    time.sleep(0.5)
    kosdaq = fetch_top_gainers("https://finance.naver.com/sise/sise_rise_ksdaq.naver", top_n=40)

    all_stocks = kospi + kosdaq
    # 등락률 내림차순 정렬, 중복 ticker 제거, 제외 대상은 건너뛰고 다음 순위로 채움
    seen = set()
    top10 = []
    for s in sorted(all_stocks, key=lambda x: x["changePct"], reverse=True):
        if s["ticker"] in seen:
            continue
        seen.add(s["ticker"])
        reason = classify_excluded(s["ticker"], s["name"], base_dd=date_str.replace("-", ""))
        if reason:
            print(f"  [제외] {s['name']} ({s['ticker']}) - {reason}")
            continue
        top10.append(s)
        if len(top10) >= 10:
            break
    if len(top10) < 10:
        print(f"  [경고] 제외 처리 후 {len(top10)}개만 확보됨 (10개 미달)")
    for i, s in enumerate(top10, 1):
        s["rank"] = i
    return top10


def get_weekly_top10(from_date: str, to_date: str) -> list[dict]:
    """
    from_date ~ to_date 기간의 주간 상승률 상위 10종목.

    2026-08-01 버그 수정: 예전엔 이 기간의 각 평일을 "다시 조회"한다면서 실제로는
    매번 네이버의 실시간(현재) 페이지만 반복 조회했다 - 그 페이지는 과거 날짜를
    보여줄 수 없는 실시간 전용 페이지라서, 결과적으로 "오늘 하루치 등락률을
    거래일 수만큼 복리 계산"하는 것과 같아져 수치가 완전히 틀렸다(예: 두산
    +271.29% = 1.30^5-1, 즉 하루 +30%를 5제곱한 값과 정확히 일치했다).
    이제는 raw_top_candidates에 매 평일 실행마다 실제로 저장해둔 그날의 원본
    후보를 읽어 진짜 일별 등락률로 복리 계산한다(save_raw_candidates 참고).
    이번 주부터 저장되므로, 과거 주(이 함수가 고쳐지기 전)는 소급 재계산이
    안 된다 - 애초에 raw_top_candidates에 그 시절 데이터가 없기 때문.
    """
    weekly_map = fetch_weekly_candidates_from_db(from_date, to_date)
    if not weekly_map:
        print(f"  [경고] raw_top_candidates에 {from_date}~{to_date} 데이터가 없습니다"
              f"(이 기간에 평일 자동 실행이 없었거나, 이 기능 도입 이전 주간입니다).")

    # 주간 누적 상승률 계산 (복리)
    for t, s in weekly_map.items():
        cumulative = 1.0
        for d in s.get("daily_changes", []):
            cumulative *= (1 + d / 100)
        s["weeklyChangePct"] = round((cumulative - 1) * 100, 2)

    candidates = sorted(weekly_map.values(), key=lambda x: x["weeklyChangePct"], reverse=True)
    top10 = []
    for s in candidates:
        reason = classify_excluded(s["ticker"], s["name"])
        if reason:
            print(f"  [제외] {s['name']} ({s['ticker']}) - {reason}")
            continue
        top10.append(s)
        if len(top10) >= 10:
            break
    if len(top10) < 10:
        print(f"  [경고] 제외 처리 후 {len(top10)}개만 확보됨 (10개 미달)")
    for i, s in enumerate(top10, 1):
        s["rank"] = i
        s["changePct"] = s["weeklyChangePct"]
    return top10


# ─── 재무 정보(기업실적분석) ──────────────────────────────────────────────────

def fetch_financials(ticker: str) -> dict:
    """네이버 종목 메인 페이지의 "기업실적분석" 표에서 가장 최근 실제 발표된
    분기 실적(추정치(E) 제외)을 읽어온다. 매출증가율은 같은 분기 전년동기 대비.

    ⚠ 이 페이지(item/main.naver)는 EUC-KR이 아니라 UTF-8이다 - 다른 함수들처럼
    r.encoding을 강제로 euc-kr로 지정하면 한글이 깨진다(직접 확인한 실수 -
    2026-08-01 재무정보 기능 추가 중 발견)."""
    try:
        r = requests.get(
            f"https://finance.naver.com/item/main.naver?code={ticker}",
            headers=HEADERS, timeout=10,
        )
        soup = BeautifulSoup(r.text, "html.parser")
        # "기업실적분석" 표를 고정 인덱스가 아니라 클래스로 찾는다 - 종목마다
        # 앞쪽 "주요 시세" 표 개수가 달라(대형주는 2개, 대부분은 1개) 인덱스가
        # 흔들린다(직접 확인 - 005930은 인덱스4, 044380은 인덱스3이었음).
        table = soup.select_one("table.tb_type1_ifrs")
        if table is None:
            return {}
        thead_rows = table.select("thead tr")
        date_ths = thead_rows[1].select("th")
        dates = [th.get_text(strip=True).split("(")[0].strip() for th in date_ths]
        is_estimate = ["(E)" in th.get_text() for th in date_ths]

        tbody = table.select_one("tbody")
        rows = {}
        for row in tbody.select("tr"):
            th = row.select_one("th")
            label = th.get_text(strip=True) if th else ""
            rows[label] = [td.get_text(strip=True).replace(",", "") for td in row.select("td")]

        # 분기 컬럼은 뒤쪽 6개(연간 4개 + 분기 6개 = 총 10개 컬럼 기준). 그중
        # 오른쪽부터 훑어 추정치(E)가 아닌 첫 컬럼 = 가장 최근 실제 발표 분기.
        quarter_start = max(0, len(dates) - 6)
        idx = None
        for i in range(len(dates) - 1, quarter_start - 1, -1):
            if not is_estimate[i]:
                idx = i
                break
        if idx is None:
            return {}

        def num(label, i):
            vals = rows.get(label, [])
            if i >= len(vals) or not vals[i]:
                return None
            try:
                return float(vals[i])
            except ValueError:
                return None

        revenue = num("매출액", idx)
        operating_profit = num("영업이익", idx)
        operating_margin = num("영업이익률", idx)
        if revenue is None or operating_profit is None:
            return {}

        # 전년동기(4분기 전) 매출액으로 YoY 매출증가율 계산
        revenue_growth = None
        prev_idx = idx - 4
        if prev_idx >= quarter_start:
            prev_revenue = num("매출액", prev_idx)
            if prev_revenue:
                revenue_growth = round((revenue - prev_revenue) / prev_revenue * 100, 1)

        y, m = dates[idx].split(".")
        q = {"03": 1, "06": 2, "09": 3, "12": 4}.get(m, 0)
        period = f"{y}년 {q}분기" if q else dates[idx]

        return {
            "period": period,
            "revenue": int(revenue * 1e8),           # 억원 -> 원
            "operatingProfit": int(operating_profit * 1e8),
            "operatingMargin": operating_margin,
            "revenueGrowth": revenue_growth,
        }
    except Exception as e:
        print(f"    [재무정보 수집 오류] {ticker}: {e}")
        return {}


# ─── OHLCV + 기술적 지표 ─────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str, count: int = 120) -> list[dict]:
    """네이버 fchart API에서 OHLCV 데이터 수집."""
    url = (
        f"https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={ticker}&timeframe=day&count={count}&requestType=0"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        # EUC-KR XML → UTF-8 변환 후 파싱
        xml_text = r.content.decode("euc-kr", errors="replace")
        xml_text = xml_text.replace('encoding="EUC-KR"', 'encoding="UTF-8"')
        root = ElementTree.fromstring(xml_text.encode("utf-8"))
        ohlcv = []
        for item in root.findall(".//item"):
            parts = item.get("data", "").split("|")
            if len(parts) < 6:
                continue
            date_raw, open_, high, low, close_, vol = parts[:6]
            try:
                ohlcv.append({
                    "date": f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}",
                    "open": int(open_),
                    "high": int(high),
                    "low": int(low),
                    "close": int(close_),
                    "volume": int(vol),
                })
            except Exception:
                continue
        return ohlcv
    except Exception as e:
        print(f"  [OHLCV 오류] {ticker}: {e}")
        return []


def calc_ma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 0)


def calc_technicals(ohlcv: list[dict], close: int, volume: int) -> dict:
    closes = [c["close"] for c in ohlcv]
    volumes = [c["volume"] for c in ohlcv]
    highs = [c["high"] for c in ohlcv]
    lows = [c["low"] for c in ohlcv]

    ma5 = calc_ma(closes, 5)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    ma120 = calc_ma(closes, 120)

    w52_high = max(highs[-252:]) if len(highs) >= 52 else (max(highs) if highs else close)
    w52_low = min(lows[-252:]) if len(lows) >= 52 else (min(lows) if lows else close)

    vol_avg20 = int(sum(volumes[-20:]) / min(20, len(volumes))) if volumes else 0
    vol_ratio = round(volume / vol_avg20, 1) if vol_avg20 else 0

    pct_from_high = round((close - w52_high) / w52_high * 100, 1) if w52_high else 0
    pct_from_low = round((close - w52_low) / w52_low * 100, 1) if w52_low else 0

    trend = "상승추세" if ma5 and ma20 and ma5 > ma20 else "하락추세"

    # 골든크로스/데드크로스 감지 (최근 3일)
    cross = None
    if len(closes) >= 22:
        prev_ma5 = calc_ma(closes[:-1], 5)
        prev_ma20 = calc_ma(closes[:-1], 20)
        if prev_ma5 and prev_ma20 and ma5 and ma20:
            if prev_ma5 <= prev_ma20 and ma5 > ma20:
                cross = "골든크로스"
            elif prev_ma5 >= prev_ma20 and ma5 < ma20:
                cross = "데드크로스"

    return {
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "current": close,
        "w52High": w52_high,
        "w52Low": w52_low,
        "pctFromHigh": pct_from_high,
        "pctFromLow": pct_from_low,
        "volToday": volume,
        "volAvg20": vol_avg20,
        "volRatio": vol_ratio,
        "trend": trend,
        "cross": cross,
    }


# ─── 네이버 뉴스 수집 ─────────────────────────────────────────────────────────

def fetch_article_summary(url: str) -> str:
    try:
        if "n.news.naver.com" in url:
            # 통합검색(fetch_naver_general_news)에서 오는 링크는 이미 실제 기사
            # 주소라 리다이렉트가 없고, 이 페이지는 UTF-8이다 - euc-kr을 강제하면 깨진다.
            r = requests.get(url, headers=HEADERS, timeout=8)
            r.encoding = r.apparent_encoding or "utf-8"
        else:
            r = requests.get(url, headers=HEADERS, timeout=8)
            r.encoding = "euc-kr"
            # finance.naver.com/item/news_read.naver는 실제 기사를 주지 않고
            # <SCRIPT>top.location.href='https://n.news.naver.com/...'</SCRIPT> 로만 응답한다.
            # requests는 이 JS 리다이렉트를 따라가지 않으므로 직접 파싱해서 재요청한다.
            m = re.search(r"top\.location\.href=['\"]([^'\"]+)['\"]", r.text)
            if m:
                r = requests.get(m.group(1), headers=HEADERS, timeout=8)
                r.encoding = r.apparent_encoding or "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#dic_area, #newsct_article, .newsct_article, #articeBody, .article_body, #content")
        text = content.get_text(" ", strip=True) if content else soup.get_text(" ", strip=True)
        return re.sub(r"\s+", " ", text)[:400]
    except Exception:
        return ""


def fetch_stock_news(ticker: str, target_date: str, max_articles: int = 15,
                     days_before: int = 5, days_after: int = 5) -> list[dict]:
    """target_date 기준 days_before일 전 ~ days_after일 후 사이의 종목 뉴스를 수집한다.
    기본은 대칭 ±5일(기존 동작 유지). 뉴스가 아예 안 잡히는 종목을 뒤늦게 다시
    확인할 때는 days_after=0, days_before=7처럼 "그날부터 앞선 1주일"만 보도록
    호출한다(2026-08-06, 회장님 요청 - patch_gainer_fields.py --mode news 참고)."""
    articles = []
    target = datetime.strptime(target_date, "%Y-%m-%d").date()

    for page in range(1, 6):
        url = (
            f"https://finance.naver.com/item/news_news.nhn"
            f"?code={ticker}&page={page}&sm=title_entity_id.basic"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            break

        rows = soup.select("table.type5 tr")
        found_in_range = False
        for row in rows:
            title_td = row.select_one("td.title")
            date_td = row.select_one("td.date")
            if not title_td or not date_td:
                continue
            a_tag = title_td.find("a")
            if not a_tag:
                continue
            raw_date = date_td.get_text(strip=True)
            try:
                art_date = datetime.strptime(raw_date[:10], "%Y.%m.%d").date()
            except Exception:
                continue

            if art_date > target + timedelta(days=days_after) or art_date < target - timedelta(days=days_before):
                if art_date < target - timedelta(days=days_before):
                    break
                continue

            found_in_range = True
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            news_url = "https://finance.naver.com" + href if href.startswith("/") else href
            summary = fetch_article_summary(news_url)
            articles.append({"title": title, "summary": summary, "date": str(art_date), "url": news_url})

            if len(articles) >= max_articles:
                return articles

        if not found_in_range:
            break
        time.sleep(0.3)

    return articles


def relative_label(article_date: str, target_date: str) -> str:
    """기사 날짜가 리포트 날짜(target_date) 기준으로 며칠/몇 주/몇 개월 전인지
    표시용 문구를 만든다(2026-08-08 회장님 요청 - "당일 기사가 아니면 옆에
    며칠 전/몇 주 전인지 표기"). 기사가 리포트 날짜보다 미래거나 당일이면
    "당일"로 취급한다."""
    a = datetime.strptime(article_date, "%Y-%m-%d").date()
    t = datetime.strptime(target_date, "%Y-%m-%d").date()
    diff = (t - a).days
    if diff <= 0:
        return "당일"
    if diff < 7:
        return f"{diff}일 전"
    if diff < 30:
        return f"{diff // 7}주 전"
    return f"{diff // 30}개월 전"


def fetch_naver_general_news(query: str, target_date: str, max_articles: int = 15,
                             days_before: int = 30, days_after: int = 0) -> list[dict]:
    """네이버 금융 종목뉴스 탭(fetch_stock_news)에서 못 찾을 때 쓰는 2차 소스 -
    네이버 통합 뉴스검색(모바일, m.search.naver.com). 2026-08-08 추가(회장님
    요청 - 크롤링 범위 확대). 종목코드가 아니라 종목명으로 검색하므로 동명
    기업 기사가 섞일 위험이 있어 반드시 종목뉴스 탭이 비었을 때만 보조로 쓴다.

    ⚠ 개별 기사의 정확한 게시일을 이 검색 결과 화면에서 안정적으로 뽑을
    방법을 찾지 못했다(모바일 검색도 날짜가 DOM에 노출되지 않음) - 없는
    사실을 지어내지 않기 위해 기사별 date는 채우지 않고, 호출 쪽에서
    "검색에 사용한 범위"(예: 1개월 이내)만 표시하게 한다."""
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    date_from = (target - timedelta(days=days_before)).strftime("%Y.%m.%d")
    date_to = (target + timedelta(days=days_after)).strftime("%Y.%m.%d")
    articles = []
    try:
        r = requests.get(
            "https://m.search.naver.com/search.naver",
            params={
                "where": "m_news", "query": query, "sort": "1",
                "ds": date_from, "de": date_to,
                "nso": f"so:r,p:from{date_from.replace('.', '')}to{date_to.replace('.', '')}",
            },
            headers=HEADERS, timeout=15,
        )
        soup = BeautifulSoup(r.text, "html.parser")
        seen = set()
        for a in soup.select('a[href*="n.news.naver.com"], a[href*="news.naver.com"]'):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href or not title or href in seen:
                continue
            seen.add(href)
            summary = fetch_article_summary(href)
            articles.append({"title": title, "summary": summary, "url": href, "date": None})
            if len(articles) >= max_articles:
                break
    except Exception as e:
        print(f"    [네이버 통합검색 오류] {query}: {e}")
    return articles


def fetch_stock_news_staged(ticker: str, name: str, target_date: str,
                            max_articles: int = 15) -> tuple[list[dict], str]:
    """당일 → 과거 1주일 → 과거 2주일 → 과거 1개월 순으로 범위를 넓혀가며 뉴스를
    찾는다(2026-08-08 회장님 요청). 각 단계에서 기사를 찾으면 그 단계에서 멈춘다.
    네이버 종목뉴스 탭에서 1개월까지도 못 찾으면, 2차 소스(네이버 통합
    뉴스검색, 종목명으로 검색)를 같은 4단계로 한 번 더 시도한다.
    반환값의 두 번째 항목은 실제로 기사를 찾은 단계 이름(로그·검증용) -
    "(통합검색)" 접미사가 붙으면 2차 소스에서 찾은 것."""
    stages = [("당일", 0), ("1주일", 7), ("2주일", 14), ("1개월", 30)]
    for stage_name, days_before in stages:
        articles = fetch_stock_news(ticker, target_date, max_articles=max_articles,
                                    days_before=days_before, days_after=0)
        if articles:
            return articles, stage_name
    for stage_name, days_before in stages:
        if days_before == 0:
            continue  # 통합검색은 "당일"만 걸면 결과가 거의 없어 의미가 적어 건너뜀
        articles = fetch_naver_general_news(name, target_date, max_articles=max_articles,
                                            days_before=days_before, days_after=0)
        if articles:
            return articles, f"{stage_name}(통합검색)"
    return [], "없음"


def news_to_dicts(articles: list[dict], target_date: str, limit: int = 5,
                  stage: str | None = None) -> list[dict]:
    """기사 리스트를 저장용 {title, summary, url, date, relativeLabel} 형태로 변환한다.
    이전에는 date를 저장하지 않아 사이트에서 "며칠 전"을 계산할 방법이 없었다.

    stage에 "(통합검색)"이 붙어 있으면(2차 소스, fetch_naver_general_news) 그
    기사들은 정확한 날짜를 모른다(date=None) - "3일 전"처럼 지어내지 않고,
    검색에 실제로 쓴 범위를 그대로 라벨로 쓴다(예: "1개월 이내")."""
    approx_label = f"{stage.replace('(통합검색)', '')} 이내" if stage and "통합검색" in stage else None
    out = []
    for a in articles[:limit]:
        date = a.get("date")
        if date:
            label = relative_label(date, target_date)
        else:
            date = None
            label = approx_label or "날짜 확인 안 됨"
        out.append({
            "title": a["title"], "summary": a["summary"], "url": a["url"],
            "date": date, "relativeLabel": label,
        })
    return out


# ─── Groq 분석 (Llama 3.3 70B, 무료 API) ───────────────────────────────────────

class GroqQuotaExhausted(Exception):
    """Groq 429가 재시도 후에도 풀리지 않을 때 발생시켜 전체 실행을 중단시킨다."""


_KANA_RE = re.compile(r"[぀-ヿ]")  # 히라가나/가타카나 - 한국어에는 없음


def has_language_issue(text: str) -> bool:
    """Groq(llama-3.3-70b)가 "반드시 순수 한국어로만 작성" 지시를 가끔 무시하고
    일본어로 응답하는 사례가 실제로 여러 건 확인됐다(2026-08-04/05, 회장님
    지적). 히라가나/가타카나 존재를 언어 오염의 신호로 쓴다."""
    return bool(_KANA_RE.search(text))


def call_groq_with_retry(client, prompt: str, max_retries: int = 4) -> str:
    wait = 60
    last_text = ""
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content or ""
            if has_language_issue(text):
                last_text = text
                print(f"    [Groq 언어 오염] 일본어 감지, 재시도 ({attempt+1}/{max_retries})...")
                continue
            return text
        except RateLimitError as e:
            retry_after = None
            try:
                retry_after = e.response.headers.get("retry-after")
            except Exception:
                pass
            wait_sec = min(int(float(retry_after)) + 5, 120) if retry_after else wait
            print(f"    [Groq 429] {wait_sec}초 대기 후 재시도 ({attempt+1}/{max_retries})...")
            time.sleep(wait_sec)
            wait = min(wait * 2, 120)
        except APIStatusError as e:
            print(f"    [Groq 오류] {e}")
            return ""
    if last_text:
        # 언어 오염이 재시도로도 안 풀림 - 할당량 문제가 아니므로 중단시키지 않고
        # 마지막 응답을 그대로 반환한다(사후 검사로 다시 잡아낼 수 있음).
        print("    [Groq 언어 오염] 재시도 소진 - 마지막 응답을 그대로 사용")
        return last_text
    raise GroqQuotaExhausted(
        f"Groq 429(rate_limit_error)가 {max_retries}회 재시도 후에도 풀리지 않았습니다. "
        "무료 할당량이 소진된 것으로 보여 자동 실행을 중단합니다. 할당량 회복 후 사람이 "
        "직접(workflow_dispatch 등으로) 다시 실행해야 합니다."
    )


def build_analysis_prompt(name: str, ticker: str, date_str: str, change_pct: float,
                          articles: list[dict], technicals: dict | None = None,
                          is_weekly: bool = False) -> str:
    """Groq/Gemini 등 어떤 LLM을 쓰든 동일한 프롬프트를 쓰기 위해 분리(백필
    스크립트가 별도 할당량의 Gemini로 이 함수를 재사용함 - analyze_stock 참고)."""
    period = "주간" if is_weekly else "당일"
    arts_text = "\n".join(
        f"[기사 {i}] ({a.get('date', date_str)}) {a['title']}\n{a['summary']}"
        for i, a in enumerate(articles, 1)
    )

    t = technicals or {}
    technicals_section = (
        f"ma5={t.get('ma5')}, ma20={t.get('ma20')}, ma60={t.get('ma60')}, ma120={t.get('ma120')}, "
        f"현재가={t.get('current')}, 52주고가={t.get('w52High')}, 52주저가={t.get('w52Low')}, "
        f"고가대비={t.get('pctFromHigh')}%, 저가대비={t.get('pctFromLow')}%, "
        f"거래량비율(20일평균 대비)={t.get('volRatio')}, 추세={t.get('trend')}, "
        f"골든/데드크로스 발생 여부={t.get('cross') or '크로스 없음'}"
    )

    return f"""당신은 한국 주식 전문 애널리스트입니다.
아래 종목의 {period} 급등 이유와 차트 분석을 실제 수집된 기사를 바탕으로 작성하세요.
반드시 순수 한국어로만 작성하세요. "附近", "以上", "現在"처럼 한자를 섞어 쓰지 말고,
"부근", "이상", "현재"처럼 한글로만 표기하세요.

종목: {name} ({ticker})
날짜: {date_str}
{period} 상승률: +{change_pct:.2f}%

=== 실제 수집 기사 {len(articles)}개 ===
{arts_text}

=== 실제 계산된 기술적 지표 (차트 분석은 반드시 이 수치만 근거로 작성) ===
{technicals_section}

아래 형식으로 작성하세요:

[riseReason]
기사에서 확인된 핵심 급등 원인을 3~5문장으로 서술하세요.
- 계약금액·수주액·수익률 등 구체적 수치가 있으면 반드시 포함
- 추측이 아닌 기사에서 확인된 사실만 작성
- 위 기사에 없는 사건(공시 종류, 계약 상대방, 날짜, 금액 등)은 절대 지어내지 마세요
- 200자 이상

[chartAnalysis]
위 "실제 계산된 기술적 지표"에 있는 수치만 근거로 이동평균선 배열, 거래량 특이점,
지지·저항 구간 등 기술적 특징과 향후 주목할 가격대 또는 리스크 요인을 150자 이상으로
작성하세요. "골든/데드크로스 발생 여부"가 "크로스 없음"이면 골든크로스나 데드크로스가
발생했다고 쓰지 마세요.
"""


def build_chart_only_prompt(name: str, ticker: str, date_str: str, change_pct: float,
                            technicals: dict | None = None, is_weekly: bool = False) -> str:
    """뉴스 기사가 전혀 없어 riseReason은 못 채우더라도, 차트 분석은 기술적
    지표만으로 작성 가능하다(2026-08-07 - 회장님이 뉴스 없는 종목의 차트
    해설 누락을 지적해 추가). build_analysis_prompt의 [chartAnalysis] 부분만
    떼어낸 버전."""
    period = "주간" if is_weekly else "당일"
    t = technicals or {}
    technicals_section = (
        f"ma5={t.get('ma5')}, ma20={t.get('ma20')}, ma60={t.get('ma60')}, ma120={t.get('ma120')}, "
        f"현재가={t.get('current')}, 52주고가={t.get('w52High')}, 52주저가={t.get('w52Low')}, "
        f"고가대비={t.get('pctFromHigh')}%, 저가대비={t.get('pctFromLow')}%, "
        f"거래량비율(20일평균 대비)={t.get('volRatio')}, 추세={t.get('trend')}, "
        f"골든/데드크로스 발생 여부={t.get('cross') or '크로스 없음'}"
    )
    return f"""당신은 한국 주식 전문 애널리스트입니다.
아래 종목의 {period} 차트를 실제 계산된 기술적 지표만 근거로 분석하세요.
반드시 순수 한국어로만 작성하세요. 한자나 다른 언어를 섞지 마세요.

종목: {name} ({ticker})
날짜: {date_str}
{period} 상승률: +{change_pct:.2f}%

=== 실제 계산된 기술적 지표 (반드시 이 수치만 근거로 작성) ===
{technicals_section}

[chartAnalysis]
위 수치만 근거로 이동평균선 배열, 거래량 특이점, 지지·저항 구간 등 기술적
특징과 향후 주목할 가격대 또는 리스크 요인을 150자 이상으로 작성하세요.
"골든/데드크로스 발생 여부"가 "크로스 없음"이면 골든크로스나 데드크로스가
발생했다고 쓰지 마세요. 뉴스나 상승 이유는 언급하지 말고 차트/지표
얘기만 하세요.
"""


def parse_analysis_response(text: str) -> tuple[str, str]:
    rise, chart = "", ""
    m_rise = re.search(r"\[riseReason\](.*?)(?=\[chartAnalysis\]|$)", text, re.DOTALL)
    m_chart = re.search(r"\[chartAnalysis\](.*?)$", text, re.DOTALL)
    if m_rise:
        rise = m_rise.group(1).strip()
    if m_chart:
        chart = m_chart.group(1).strip()
    return rise, chart


def parse_chart_only_response(text: str) -> str:
    m = re.search(r"\[chartAnalysis\](.*?)$", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def analyze_stock(client, name: str, ticker: str, date_str: str,
                  change_pct: float, articles: list[dict],
                  technicals: dict | None = None,
                  is_weekly: bool = False) -> tuple[str, str]:
    if not articles:
        return f"{name}에 대한 뉴스 기사를 수집하지 못했습니다.", ""
    prompt = build_analysis_prompt(name, ticker, date_str, change_pct, articles, technicals, is_weekly)
    text = call_groq_with_retry(client, prompt)
    return parse_analysis_response(text)


# ─── 거래대금 상위(volumeStocks) 수집 ────────────────────────────────────────

def fetch_investor_netbuy(ticker: str, close: int, target_date: str | None = None,
                           trade_amount: int | None = None) -> dict:
    """네이버 종목 페이지(frgn.naver)에서 기관·외국인 순매매량(주)을 읽어 종가와
    곱해 순매수 금액(원)을 근사한다. 개인은 -(기관+외국인)의 역산값(사이트 UI
    안내문과 동일한 근사 방식 - index.html의 "※ 개인 순매수는..." 참고).

    target_date(YYYY-MM-DD)를 주면 이 표가 실제로 제공하는 날짜별 이력에서
    해당 날짜 행을 찾는다(최대 3페이지=약 30영업일 앞까지 탐색 - 과거 데이터
    백필용). 생략하면 최신(1페이지 첫 행)을 반환한다(일일 자동 실행용)."""
    target = target_date.replace("-", ".") if target_date else None
    try:
        for page in range(1, 4 if target else 2):
            r = requests.get(
                f"https://finance.naver.com/item/frgn.naver?code={ticker}&page={page}",
                headers=HEADERS, timeout=10,
            )
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")
            # "외국인 기관 순매매 거래량" 표를 고정 인덱스가 아니라 summary 텍스트로
            # 찾는다 - ETF/ETN은 앞쪽 "주요 시세" 표가 1개뿐이라 인덱스가 밀려서
            # 페이지 네비게이션 표를 순매수 표로 잘못 읽는 버그가 있었다(2026-08-08
            # 발견 - 거래대금 94억원짜리 종목의 기관 순매수가 582원으로 나옴,
            # fetch_financials의 table.tb_type1_ifrs 인덱스 버그와 같은 유형).
            table = soup.find("table", summary=lambda s: s and "외국인" in s and "기관" in s)
            if table is None:
                print(f"    [순매수 표 없음] {ticker} - 표 구조가 예상과 다름")
                break
            for row in table.select("tr"):
                tds = row.select("td")
                if len(tds) < 9:
                    continue
                date_text = tds[0].get_text(strip=True)
                if not date_text:
                    continue
                if target and date_text != target:
                    continue
                inst_raw = tds[5].get_text(strip=True).replace(",", "").replace("+", "")
                frgn_raw = tds[6].get_text(strip=True).replace(",", "").replace("+", "")
                try:
                    inst_shares = int(inst_raw)
                    frgn_shares = int(frgn_raw)
                except ValueError:
                    continue
                # 정합성 검사: 기관·외국인 순매매량(주)은 그날 총거래량을 넘을 수 없다.
                # KRX 원본 거래대금(trade_amount, 원)을 종가로 나눠 총거래량(주)을
                # 역산해 기준으로 삼는다 - 네이버 표의 거래량 컬럼(tds[4])은 일부
                # ETF에서 실제로는 다른 값(거래대금으로 추정)을 보여주는 이상 사례가
                # 있어(2026-08-08 발견, 252670에서 기관 순매매량이 총거래량의 2배로
                # 계산됨) 이 컬럼 자체를 기준으로 쓸 수 없다.
                if trade_amount and close:
                    implied_volume = trade_amount / close
                    if abs(inst_shares) > implied_volume * 1.5 or abs(frgn_shares) > implied_volume * 1.5:
                        print(f"    [순매수 이상치] {ticker} {date_text}: 순매매량이 추정 총거래량({implied_volume:.0f}주)을 초과 - 저장 안 함")
                        return {"individual": 0, "institution": 0, "foreign": 0}
                institution = inst_shares * close
                foreign = frgn_shares * close
                individual = -(institution + foreign)
                return {"individual": individual, "institution": institution, "foreign": foreign}
            if not target:
                break
            time.sleep(0.2)
    except Exception as e:
        print(f"    [순매수 수집 오류] {ticker}: {e}")
    return {"individual": 0, "institution": 0, "foreign": 0}


def fetch_prev_volume_stocks() -> dict:
    """Supabase에서 가장 최근 trade_date의 volume_stocks를 ticker 기준으로 조회해
    {ticker: {rank, tradeAmount}} 형태로 반환. 오늘 것과 비교해 전일 순위·거래대금을 낸다."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    try:
        r = requests.get(
            f"{url}/rest/v1/volume_stocks?select=trade_date,rank,ticker,trade_amount"
            f"&order=trade_date.desc,rank.asc&limit=10",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return {}
        latest_date = rows[0]["trade_date"]
        return {row["ticker"]: {"rank": row["rank"], "tradeAmount": row["trade_amount"]}
                for row in rows if row["trade_date"] == latest_date}
    except Exception as e:
        print(f"  [전일 거래대금 조회 오류] {e}")
        return {}


def fetch_volume_stocks() -> list[dict]:
    stocks = []
    urls = [
        "https://finance.naver.com/sise/sise_quant.naver",
        "https://finance.naver.com/sise/sise_quant_ksdaq.naver",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("table.type_2 tr")
            for row in rows:
                tds = row.select("td")
                if len(tds) < 10:
                    continue
                a = tds[1].find("a")
                if not a:
                    continue
                href = a.get("href", "")
                m = re.search(r"code=(\d{6})(?![0-9A-Za-z])", href)
                if not m:
                    continue
                ticker = m.group(1)
                name = a.get_text(strip=True)
                close_raw = tds[2].get_text(strip=True).replace(",", "")
                rate_raw = tds[4].get_text(strip=True).replace("+", "").replace("%", "").replace(",", "")
                # tds[5]=거래량(주), tds[6]=거래대금(백만원 단위) - 예전엔 tds[5]를
                # tradeAmount로 잘못 읽어서 실제로는 "거래량 상위"를 "거래대금 상위"로
                # 표시하고 있었다(2026-08-08 발견 - 회장님이 순매수 금액 이상함을
                # 지적해 조사하다가, 이 페이지가 기본적으로 거래량순 정렬이라 저가
                # 레버리지/인버스 ETF가 항상 상위 10위를 독점하고 있었음을 확인).
                amount_raw = tds[6].get_text(strip=True).replace(",", "") if len(tds) > 6 else "0"
                # 전일 대비 가격(전일비): <em class="bu_pup|bu_pdn|bu_p2">의 부호 + <span> 숫자
                price_change = 0
                em = tds[3].select_one("em")
                span = tds[3].select_one("span")
                if em and span:
                    num_raw = span.get_text(strip=True).replace(",", "")
                    try:
                        num = int(num_raw)
                        classes = em.get("class") or []
                        price_change = -num if "bu_pdn" in classes else num
                    except ValueError:
                        pass
                try:
                    close = int(close_raw)
                    change_pct = float(rate_raw)
                    trade_amount = int(amount_raw) * 1_000_000 if amount_raw.isdigit() else 0
                except Exception:
                    continue
                stocks.append({
                    "ticker": ticker,
                    "name": name,
                    "close": close,
                    "changePct": change_pct,
                    "tradeAmount": trade_amount,
                    "priceChange": price_change,
                    "naverUrl": f"https://finance.naver.com/item/main.naver?code={ticker}",
                })
                # 이 페이지는 거래량순 정렬이라 거래대금 상위가 뒤쪽 행에 있을 수
                # 있다 - 한 페이지(최대 100행)를 전부 읽은 뒤 거래대금으로 다시
                # 정렬해서 진짜 상위 10을 뽑는다(20개로 끊으면 놓칠 수 있음).
        except Exception as e:
            print(f"  [거래대금 수집 오류] {e}")
        time.sleep(0.5)

    # 거래대금 내림차순 상위 10개
    top10 = sorted(stocks, key=lambda x: x["tradeAmount"], reverse=True)[:10]

    prev = fetch_prev_volume_stocks()
    for i, s in enumerate(top10, 1):
        s["rank"] = i
        p = prev.get(s["ticker"])
        s["prevRank"] = p["rank"] if p else None
        s["prevTradeAmount"] = p["tradeAmount"] if p else None
        s["investors"] = fetch_investor_netbuy(s["ticker"], s["close"], trade_amount=s.get("tradeAmount"))
        time.sleep(0.3)
    return top10


# ─── 메인 파이프라인 ──────────────────────────────────────────────────────────

def run_daily(client, date_str: str):
    print(f"\n[당일 리포트] {date_str}")

    # 원본 후보 저장 (이번 주 토요일 주간 리포트가 나중에 실제 값으로 복리
    # 계산할 수 있게 - get_weekly_top10() 참고). daily 리포트 자체와는 무관.
    kospi_raw = fetch_top_gainers("https://finance.naver.com/sise/sise_rise.naver", top_n=100)
    kosdaq_raw = fetch_top_gainers("https://finance.naver.com/sise/sise_rise_ksdaq.naver", top_n=100)
    save_raw_candidates(date_str, kospi_raw, kosdaq_raw)

    print("1. 상승률 상위 10종목 수집 중...")
    gainers = get_daily_top10(date_str)
    print(f"   → {len(gainers)}개 수집 완료")

    print("2. 거래대금 상위 10종목 수집 중...")
    volume_stocks = fetch_volume_stocks()
    print(f"   → {len(volume_stocks)}개 수집 완료")

    print("3. 종목별 OHLCV·뉴스·분석 진행 중...")
    for g in gainers:
        name, ticker = g["name"], g["ticker"]
        print(f"\n  [{g['rank']}] {name} ({ticker}) +{g['changePct']:.2f}%")

        # OHLCV
        ohlcv = fetch_ohlcv(ticker, count=120)
        g["ohlcv"] = ohlcv[-60:] if len(ohlcv) > 60 else ohlcv  # 최근 60일만 저장
        g["technicals"] = calc_technicals(ohlcv, g["close"], g.get("volume", 0))
        g["w52High"] = g["technicals"]["w52High"]
        g["w52Low"] = g["technicals"]["w52Low"]
        g["financials"] = fetch_financials(ticker)
        g["naverUrl"] = f"https://finance.naver.com/item/main.naver?code={ticker}"
        time.sleep(0.3)

        # 뉴스 (당일 → 1주일 → 2주일 → 1개월 순으로 확장 검색)
        print(f"     뉴스 수집 중...")
        articles, stage = fetch_stock_news_staged(ticker, name, date_str, max_articles=15)
        print(f"     → 기사 {len(articles)}개 ({stage})")
        g["news"] = news_to_dicts(articles, date_str, stage=stage)

        # Groq 분석
        print(f"     Groq 분석 중...")
        rise, chart = analyze_stock(client, name, ticker, date_str, g["changePct"], articles,
                                    technicals=g["technicals"])
        g["riseReason"] = rise
        g["chartAnalysis"] = chart
        time.sleep(1)

    return {
        "date": date_str,
        "updatedAt": datetime.now(KST).isoformat(),
        "gainers": gainers,
        "volumeStocks": volume_stocks,
    }


def run_weekly(client, date_str: str, from_date: str, to_date: str):
    print(f"\n[주간 리포트] {date_str} ({from_date} ~ {to_date})")
    print("1. 주간 상승률 상위 10종목 수집 중...")
    gainers = get_weekly_top10(from_date, to_date)
    print(f"   → {len(gainers)}개 수집 완료")

    print("2. 거래대금 상위 10종목 수집 중...")
    volume_stocks = fetch_volume_stocks()

    print("3. 종목별 OHLCV·뉴스·분석 진행 중...")
    for g in gainers:
        name, ticker = g["name"], g["ticker"]
        print(f"\n  [{g['rank']}] {name} ({ticker}) 주간 +{g['changePct']:.2f}%")
        ohlcv = fetch_ohlcv(ticker, count=120)
        g["ohlcv"] = ohlcv[-60:] if len(ohlcv) > 60 else ohlcv
        g["technicals"] = calc_technicals(ohlcv, g["close"], g.get("volume", 0))
        g["w52High"] = g["technicals"]["w52High"]
        g["w52Low"] = g["technicals"]["w52Low"]
        g["financials"] = fetch_financials(ticker)
        g["naverUrl"] = f"https://finance.naver.com/item/main.naver?code={ticker}"
        time.sleep(0.3)

        articles, stage = fetch_stock_news_staged(ticker, name, to_date, max_articles=15)
        print(f"     기사 {len(articles)}개 ({stage})")
        g["news"] = news_to_dicts(articles, to_date, stage=stage)

        rise, chart = analyze_stock(client, name, ticker, date_str, g["changePct"], articles,
                                    technicals=g["technicals"], is_weekly=True)
        g["riseReason"] = rise
        g["chartAnalysis"] = chart
        time.sleep(1)

    return {
        "date": date_str,
        "type": "weekly",
        "weekRange": f"{from_date} ~ {to_date}",
        "updatedAt": datetime.now(KST).isoformat(),
        "gainers": gainers,
        "volumeStocks": volume_stocks,
    }


def supabase_upsert(table: str, rows: list[dict], on_conflict: str):
    """Supabase REST로 upsert(service_role key, RLS 우회). 실패하면 예외를 던진다 -
    부분 실패한 데이터가 조용히 누락되는 것을 막기 위해 여기서 멈춘다."""
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
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase {table} upsert 실패 {r.status_code}: {r.text}")
    print(f"[supabase] {table} {len(rows)}행 upsert 완료")


def gainer_to_row(date_str: str, g: dict, report_type: str,
                   week_start: str | None, week_end: str | None) -> dict:
    return {
        "trade_date": date_str,
        "rank": g["rank"],
        "report_type": report_type,
        "week_start": week_start,
        "week_end": week_end,
        "ticker": g["ticker"],
        "name": g["name"],
        "close": g["close"],
        "change_pct": g["changePct"],
        "trade_amount": g.get("tradeAmount"),
        "ohlcv": g.get("ohlcv", []),
        "technicals": g.get("technicals"),
        "financials": g.get("financials", {}),
        "news": g.get("news", []),
        "rise_reason": g.get("riseReason", ""),
        "chart_analysis": g.get("chartAnalysis", ""),
        "updated_at": datetime.now(KST).isoformat(),
    }


def volume_to_row(date_str: str, v: dict) -> dict:
    return {
        "trade_date": date_str,
        "rank": v["rank"],
        "ticker": v["ticker"],
        "name": v["name"],
        "close": v["close"],
        "change_pct": v["changePct"],
        "trade_amount": v["tradeAmount"],
        "naver_url": v.get("naverUrl"),
        "investors": v.get("investors"),
        "prev_rank": v.get("prevRank"),
        "price_change": v.get("priceChange"),
        "prev_trade_amount": v.get("prevTradeAmount"),
        "updated_at": datetime.now(KST).isoformat(),
    }


def save_to_supabase(date_str: str, entry: dict, report_type: str,
                      week_start: str | None, week_end: str | None):
    gainer_rows = [gainer_to_row(date_str, g, report_type, week_start, week_end)
                    for g in entry["gainers"]]
    supabase_upsert("daily_gainers", gainer_rows, "trade_date,rank,report_type")

    volume_rows = [volume_to_row(date_str, v) for v in entry["volumeStocks"]]
    supabase_upsert("volume_stocks", volume_rows, "trade_date,rank")


def main():
    load_env()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("[오류] GROQ_API_KEY가 없습니다. .env.local에 추가해 주세요.")
        sys.exit(1)
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        print("[오류] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY가 없습니다. .env.local에 추가해 주세요.")
        sys.exit(1)

    client = Groq(api_key=api_key, max_retries=0)

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="리포트 날짜 (기본: 오늘, YYYY-MM-DD)")
    parser.add_argument("--mode", choices=["daily", "weekly", "auto"], default=None,
                        help="daily=당일, weekly=주간, auto=요일만으로 판단(구버전 방식). "
                             "생략 시(무인/자동 실행) KRX 거래일 캘린더(krx_calendar)로 "
                             "오늘 발행 여부·daily/weekly 모드를 자동 결정한다.")
    args = parser.parse_args()

    # --date/--mode를 둘 다 명시하지 않은 경우 = 무인 자동 실행(예: GitHub Actions
    # 크론)으로 간주하고, KRX 거래일 캘린더 기준으로 "오늘 실행 여부"와
    # "daily/weekly 모드"를 자동으로 결정한다. (Task 1 요구사항)
    # --date 또는 --mode를 명시하면(수동 실행/백필) 기존 동작을 그대로 유지한다.
    unattended = args.date is None and args.mode is None

    week_start = week_end = None

    if unattended:
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
        trigger = get_weekly_report_trigger(date_str)
        if not trigger["shouldRun"]:
            print(f"[skip] {date_str}: KRX 거래일 캘린더 기준 오늘은 발행일이 아닙니다 "
                  f"(mode={trigger['mode']}). 개장일이 아니거나, 주간 리포트 발행 트리거일이 "
                  f"아직 아닙니다.")
            return
        mode = trigger["mode"]
        weekday = datetime.strptime(date_str, "%Y-%m-%d").weekday()
        if mode == "weekly":
            week_start, week_end = trigger["weekStart"], trigger["weekEnd"]
            print(f"[자동 판단] {date_str} → weekly 모드 (주간 구간 {week_start} ~ {week_end})")
        else:
            print(f"[자동 판단] {date_str} → daily 모드")
    else:
        date_str = args.date or datetime.now(KST).strftime("%Y-%m-%d")
        weekday = datetime.strptime(date_str, "%Y-%m-%d").weekday()  # 0=월, 6=일

        mode = args.mode or "auto"
        if mode == "auto":
            mode = "weekly" if weekday == 5 else "daily"  # 토요일=주간 (구버전 단순 판단)

        if mode == "weekly":
            # 주간: 해당 주의 월~금 (구버전 방식 - 캘린더 미고려, 하위호환용)
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            mon = dt - timedelta(days=dt.weekday())
            fri = mon + timedelta(days=4)
            week_start, week_end = mon.strftime("%Y-%m-%d"), fri.strftime("%Y-%m-%d")

    try:
        if mode == "daily":
            if not unattended and weekday >= 5:
                print(f"[skip] {date_str}은 주말입니다. --mode daily 강제 실행이 아니면 건너뜁니다.")
                return
            entry = run_daily(client, date_str)
        else:
            entry = run_weekly(client, date_str, week_start, week_end)
    except GroqQuotaExhausted as e:
        # 여기서 멈추면 Supabase upsert는 실행되지 않는다 - 부분적으로만
        # 분석된 데이터를 저장하지 않기 위함. 워크플로우는 실패로 표시되고,
        # 사람이 할당량 회복 후 "Run workflow"로 직접 다시 실행해야 한다.
        print(f"\n[중단] {e}")
        sys.exit(1)

    save_to_supabase(date_str, entry, mode, week_start, week_end)
    print("\n완료!")


if __name__ == "__main__":
    main()
