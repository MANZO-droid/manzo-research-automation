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


def classify_excluded(ticker: str, name: str) -> str | None:
    """Top10에서 제외해야 하면 사유 문자열, 포함해도 되면 None을 반환한다.

    확인된 사실: ETN은 상품명에 항상 "ETN"이 포함되고, 우선주는 종목명이
    (숫자)우(B)로 끝나는 KRX 표기 관례를 따른다(예: 진흥기업2우B). ETF는
    브랜드명(KODEX/TIGER 등)만으로 이름에서 판별할 수 없어 네이버 ETF
    목록 API로 종목코드를 직접 대조한다.

    확인 필요: 관리종목·정리매매는 로그인 없이 공개된 API를 찾지 못해
    아직 판별하지 않는다. KRX Data Marketplace 계정(KRX_ID/KRX_PW)이
    준비되면 공식 관리종목현황 API로 이 함수에 조건을 추가해야 한다.
    """
    if "ETN" in name.upper():
        return "ETN"
    if ticker in _get_etf_tickers():
        return "ETF"
    if re.search(r"\d?우(B)?$", name):
        return "우선주"
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


def fetch_stock_news(ticker: str, target_date: str, max_articles: int = 15) -> list[dict]:
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

            delta = abs((art_date - target).days)
            if delta > 5:
                if art_date < target - timedelta(days=5):
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


# ─── Groq 분석 (Llama 3.3 70B, 무료 API) ───────────────────────────────────────

class GroqQuotaExhausted(Exception):
    """Groq 429가 재시도 후에도 풀리지 않을 때 발생시켜 전체 실행을 중단시킨다."""


def call_groq_with_retry(client, prompt: str, max_retries: int = 4) -> str:
    wait = 60
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""
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
    raise GroqQuotaExhausted(
        f"Groq 429(rate_limit_error)가 {max_retries}회 재시도 후에도 풀리지 않았습니다. "
        "무료 할당량이 소진된 것으로 보여 자동 실행을 중단합니다. 할당량 회복 후 사람이 "
        "직접(workflow_dispatch 등으로) 다시 실행해야 합니다."
    )


def analyze_stock(client, name: str, ticker: str, date_str: str,
                  change_pct: float, articles: list[dict],
                  technicals: dict | None = None,
                  is_weekly: bool = False) -> tuple[str, str]:
    if not articles:
        return f"{name}에 대한 뉴스 기사를 수집하지 못했습니다.", ""

    period = "주간" if is_weekly else "당일"
    arts_text = "\n".join(
        f"[기사 {i}] ({a['date']}) {a['title']}\n{a['summary']}"
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

    prompt = f"""당신은 한국 주식 전문 애널리스트입니다.
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

    text = call_groq_with_retry(client, prompt)
    rise, chart = "", ""
    m_rise = re.search(r"\[riseReason\](.*?)(?=\[chartAnalysis\]|$)", text, re.DOTALL)
    m_chart = re.search(r"\[chartAnalysis\](.*?)$", text, re.DOTALL)
    if m_rise:
        rise = m_rise.group(1).strip()
    if m_chart:
        chart = m_chart.group(1).strip()
    return rise, chart


# ─── 거래대금 상위(volumeStocks) 수집 ────────────────────────────────────────

def fetch_investor_netbuy(ticker: str, close: int, target_date: str | None = None) -> dict:
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
            table = soup.select("table")[3]  # "외국인 기관 순매매 거래량" 표(날짜별)
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
                amount_raw = tds[5].get_text(strip=True).replace(",", "") if len(tds) > 5 else "0"
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
                    trade_amount = int(amount_raw) if amount_raw.isdigit() else 0
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
                if len(stocks) >= 20:
                    break
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
        s["investors"] = fetch_investor_netbuy(s["ticker"], s["close"])
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
        g["financials"] = {}
        g["naverUrl"] = f"https://finance.naver.com/item/main.naver?code={ticker}"
        time.sleep(0.3)

        # 뉴스
        print(f"     뉴스 수집 중...")
        articles = fetch_stock_news(ticker, date_str, max_articles=15)
        print(f"     → 기사 {len(articles)}개")
        g["news"] = [{"title": a["title"], "summary": a["summary"], "url": a["url"]} for a in articles[:5]]

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
        g["financials"] = {}
        g["naverUrl"] = f"https://finance.naver.com/item/main.naver?code={ticker}"
        time.sleep(0.3)

        articles = fetch_stock_news(ticker, to_date, max_articles=15)
        print(f"     기사 {len(articles)}개")
        g["news"] = [{"title": a["title"], "summary": a["summary"], "url": a["url"]} for a in articles[:5]]

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
