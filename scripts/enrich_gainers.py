# -*- coding: utf-8 -*-
"""
Supabase daily_gainers 표의 특정 날짜 종목들의 riseReason / chartAnalysis / news
필드를 네이버 금융 뉴스(실제 기사 10개 이상) + Gemini로 다시 작성해 채운다.

2026-08-02: 사이트 저장소의 stock-analysis-data.json이 삭제되고 daily_gainers가
유일한 원천이 되면서, 이 스크립트도 파일 대신 Supabase를 직접 읽고 쓰도록
다시 작성했다. rise_reason/chart_analysis/news 세 컬럼만 부분 upsert하므로
(Supabase REST의 merge-duplicates는 보낸 컬럼만 갱신) ohlcv·technicals 등
나머지 값은 그대로 보존된다.

사용법:
  python scripts/enrich_gainers.py --date 2026-07-16       # 특정 날짜만
  python scripts/enrich_gainers.py --from 2026-07-14 --to 2026-07-16

필요 환경변수 (.env.local):
  GEMINI_API_KEY
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
import argparse, os, re, sys, time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from google import genai

sys.stdout.reconfigure(encoding="utf-8")

KST = timezone(timedelta(hours=9))

# scripts/ 에서 한 단계 위가 이 저장소(리서치자동화)의 루트다(.env.local이 여기 있다).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
}


# ─── 환경변수 로드 ───────────────────────────────────────────────────────────

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


# ─── Supabase 읽기/쓰기 ──────────────────────────────────────────────────────

def fetch_gainers(url: str, key: str, from_date: str, to_date: str) -> list[dict]:
    """daily_gainers에서 [from_date, to_date] 구간의 행을 전부 가져온다."""
    r = requests.get(
        f"{url}/rest/v1/daily_gainers"
        f"?select=trade_date,rank,report_type,ticker,name,change_pct"
        f"&and=(trade_date.gte.{from_date},trade_date.lte.{to_date})"
        f"&order=trade_date,rank",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase daily_gainers 조회 실패 {r.status_code}: {r.text}")
    return r.json()


def upsert_enrichment(url: str, key: str, row: dict):
    """rise_reason/chart_analysis/news 세 컬럼만 부분 upsert(나머지 컬럼 보존)."""
    r = requests.post(
        f"{url}/rest/v1/daily_gainers?on_conflict=trade_date,rank,report_type",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=[row],
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase daily_gainers 부분 upsert 실패 {r.status_code}: {r.text}")


# ─── 네이버 금융 뉴스 수집 ───────────────────────────────────────────────────

def fetch_naver_stock_news(ticker: str, target_date: str, max_articles: int = 15) -> list[dict]:
    articles = []
    target = datetime.strptime(target_date, "%Y-%m-%d").date()

    for page in range(1, 6):
        url = (
            f"https://finance.naver.com/item/news_news.nhn"
            f"?code={ticker}&page={page}&sm=title_entity_id.basic"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"    [뉴스 수집 오류] {ticker} page={page}: {e}")
            break

        rows = soup.select("table.type5 tr")
        found_any = False
        for row in rows:
            title_td = row.select_one("td.title")
            date_td = row.select_one("td.date")
            if not title_td or not date_td:
                continue

            a_tag = title_td.find("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            news_url = "https://finance.naver.com" + href if href.startswith("/") else href

            raw_date = date_td.get_text(strip=True)
            try:
                art_date = datetime.strptime(raw_date[:10], "%Y.%m.%d").date()
            except Exception:
                continue

            delta = abs((art_date - target).days)
            if delta > 3:
                if art_date < target - timedelta(days=3):
                    break
                continue

            found_any = True
            summary = fetch_article_summary(news_url)
            articles.append({"title": title, "summary": summary, "date": str(art_date), "url": news_url})

            if len(articles) >= max_articles:
                return articles

        if not found_any:
            break
        time.sleep(0.3)

    return articles


def fetch_article_summary(url: str, max_chars: int = 300) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        content = soup.select_one("#newsct_article, .newsct_article, #articeBody, .article_body")
        text = content.get_text(" ", strip=True) if content else soup.get_text(" ", strip=True)
        return re.sub(r"\s+", " ", text)[:max_chars]
    except Exception:
        return ""


# ─── Gemini 분석 ─────────────────────────────────────────────────────────────

def build_prompt(stock_name: str, ticker: str, target_date: str,
                 change_pct: float, articles: list[dict], is_weekly: bool = False) -> str:
    period = "주간" if is_weekly else "당일"
    arts_text = ""
    for i, a in enumerate(articles, 1):
        arts_text += f"\n[기사 {i}] ({a['date']}) {a['title']}\n{a['summary']}\n"

    return f"""당신은 한국 주식 전문 애널리스트입니다.
아래 종목의 {period} 급등 이유와 차트 분석을 작성해 주세요.
반드시 순수 한국어로만 작성하세요. 한자나 다른 언어를 섞지 마세요.

종목명: {stock_name} ({ticker})
날짜: {target_date}
{period} 상승률: +{change_pct:.2f}%

=== 실제 수집된 뉴스 기사 {len(articles)}개 ===
{arts_text}

위 기사들을 바탕으로 다음 형식으로 작성하세요.

[riseReason]
- 실제 기사에서 확인된 핵심 급등 원인을 3~5문장으로 서술
- 숫자(계약 금액, 수주액, 수익률 등)가 있으면 반드시 포함
- 추측이 아닌 기사에서 확인된 사실만 작성
- 200자 이상 작성

[chartAnalysis]
- 이동평균선 배열, 거래량 특이점, 지지·저항 구간 등 기술적 분석
- 향후 주목할 가격대 또는 리스크 요인 포함
- 150자 이상 작성

반드시 위 두 섹션([riseReason], [chartAnalysis])을 포함해 작성하세요.
"""


def parse_gemini_output(text: str) -> tuple[str, str]:
    rise, chart = "", ""
    m_rise = re.search(r"\[riseReason\](.*?)(?=\[chartAnalysis\]|$)", text, re.DOTALL)
    m_chart = re.search(r"\[chartAnalysis\](.*?)$", text, re.DOTALL)
    if m_rise:
        rise = m_rise.group(1).strip()
    if m_chart:
        chart = m_chart.group(1).strip()
    return rise, chart


def analyze_with_gemini(client, stock_name: str, ticker: str, target_date: str,
                        change_pct: float, articles: list[dict], is_weekly: bool = False) -> tuple[str, str]:
    if not articles:
        return f"{stock_name} 관련 기사를 수집하지 못했습니다.", ""

    prompt = build_prompt(stock_name, ticker, target_date, change_pct, articles, is_weekly)
    try:
        resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return parse_gemini_output(resp.text)
    except Exception as e:
        print(f"    [Gemini 오류] {e}")
        return "", ""


# ─── 메인 로직 ───────────────────────────────────────────────────────────────

def main():
    load_env()
    gemini_key = os.environ.get("GEMINI_API_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not gemini_key:
        print("[오류] GEMINI_API_KEY가 없습니다. .env.local에 추가해 주세요.")
        sys.exit(1)
    if not supabase_url or not supabase_key:
        print("[오류] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY가 없습니다. .env.local에 추가해 주세요.")
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="단일 날짜 (예: 2026-07-16)")
    parser.add_argument("--from", dest="from_date", help="시작 날짜")
    parser.add_argument("--to", dest="to_date", help="종료 날짜")
    args = parser.parse_args()

    if args.date:
        from_date = to_date = args.date
    elif args.from_date and args.to_date:
        from_date, to_date = args.from_date, args.to_date
    else:
        print("[오류] --date 또는 --from/--to로 대상 날짜를 지정해 주세요 (전체 재분석은 비용이 크므로 기본값 없음).")
        sys.exit(1)

    client = genai.Client(api_key=gemini_key)

    rows = fetch_gainers(supabase_url, supabase_key, from_date, to_date)
    if not rows:
        print(f"[완료] {from_date}~{to_date} 구간에 daily_gainers 행이 없습니다.")
        return

    print(f"보강 대상: {from_date}~{to_date} ({len(rows)}개 종목)")

    for row in rows:
        trade_date = row["trade_date"]
        rank = row["rank"]
        report_type = row["report_type"]
        name = row.get("name", "")
        ticker = row.get("ticker", "")
        change_pct = float(row.get("change_pct", 0))
        is_weekly = report_type == "weekly"

        if not ticker:
            print(f"  [{trade_date} #{rank}] {name} — ticker 없음, skip")
            continue

        print(f"  [{trade_date} #{rank}] {name} ({ticker}) +{change_pct:.2f}% — 뉴스 수집 중...")
        articles = fetch_naver_stock_news(ticker, trade_date, max_articles=15)
        print(f"      기사 {len(articles)}개 수집 완료")

        if len(articles) < 3:
            print(f"      기사 부족 — 분석 건너뜀")
            continue

        rise_reason, chart_analysis = analyze_with_gemini(
            client, name, ticker, trade_date, change_pct, articles, is_weekly
        )
        if not rise_reason and not chart_analysis:
            print(f"      Gemini 분석 실패 — skip")
            continue

        news = [{"title": a["title"], "summary": a["summary"], "url": a["url"]} for a in articles[:5]]
        upsert_enrichment(supabase_url, supabase_key, {
            "trade_date": trade_date,
            "rank": rank,
            "report_type": report_type,
            "rise_reason": rise_reason,
            "chart_analysis": chart_analysis,
            "news": news,
            "updated_at": datetime.now(KST).isoformat(),
        })
        print(f"      [supabase] daily_gainers 갱신 완료")

        time.sleep(1.5)  # API rate limit 방지

    print("\n[완료]")


if __name__ == "__main__":
    main()
