# -*- coding: utf-8 -*-
"""
당일 상승률 상위 종목의 riseReason / chartAnalysis / news 필드를
네이버 금융 뉴스(실제 기사 10개 이상) + Gemini로 재작성한다.

사용법:
  python scripts/enrich_gainers.py                        # 7/11~오늘 전체 보강
  python scripts/enrich_gainers.py --date 2026-07-16      # 특정 날짜만
  python scripts/enrich_gainers.py --from 2026-07-14 --to 2026-07-16

필요 환경변수 (.env.local):
  GEMINI_API_KEY
"""
import argparse, json, os, re, sys, time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

sys.stdout.reconfigure(encoding="utf-8")

# scripts/ 에서 한 단계 위가 이 저장소(리서치자동화)의 루트다(.env.local이 여기 있다).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_ROOT = os.environ.get("SITE_REPO_PATH") or os.path.join(os.path.dirname(ROOT), "만조그룹 2차")
JSON_PATH = os.path.join(SITE_ROOT, "stock-analysis-data.json")
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


# ─── 네이버 금융 뉴스 수집 ───────────────────────────────────────────────────

def fetch_naver_stock_news(ticker: str, target_date: str, max_articles: int = 15) -> list[dict]:
    """
    네이버 금융 종목 뉴스 탭에서 target_date 전후 기사를 최대 max_articles개 수집.
    반환: [{"title": str, "summary": str, "date": str, "url": str}, ...]
    """
    articles = []
    target = datetime.strptime(target_date, "%Y-%m-%d").date()

    for page in range(1, 6):  # 최대 5페이지
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

            raw_date = date_td.get_text(strip=True)  # "2026.07.14 10:23"
            try:
                art_date = datetime.strptime(raw_date[:10], "%Y.%m.%d").date()
            except Exception:
                continue

            # target_date 기준 ±3일 이내 기사만
            delta = abs((art_date - target).days)
            if delta > 3:
                if art_date < target - timedelta(days=3):
                    break  # 더 오래된 기사만 남음
                continue

            found_any = True

            # 기사 본문 앞부분 가져오기
            summary = fetch_article_summary(news_url)

            articles.append({
                "title": title,
                "summary": summary,
                "date": str(art_date),
                "url": news_url,
            })

            if len(articles) >= max_articles:
                return articles

        if not found_any:
            break
        time.sleep(0.3)

    return articles


def fetch_article_summary(url: str, max_chars: int = 300) -> str:
    """기사 URL에서 본문 앞부분만 추출."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        # 네이버 뉴스 본문 영역
        content = soup.select_one("#newsct_article, .newsct_article, #articeBody, .article_body")
        if content:
            text = content.get_text(" ", strip=True)
        else:
            text = soup.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        return text[:max_chars]
    except Exception:
        return ""


# ─── Gemini 분석 ─────────────────────────────────────────────────────────────

def build_prompt(stock_name: str, ticker: str, target_date: str,
                 change_pct: float, articles: list[dict],
                 is_weekly: bool = False) -> str:
    period = "주간" if is_weekly else "당일"
    arts_text = ""
    for i, a in enumerate(articles, 1):
        arts_text += f"\n[기사 {i}] ({a['date']}) {a['title']}\n{a['summary']}\n"

    return f"""당신은 한국 주식 전문 애널리스트입니다.
아래 종목의 {period} 급등 이유와 차트 분석을 작성해 주세요.

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
    """Gemini 응답에서 riseReason, chartAnalysis 파싱."""
    rise = ""
    chart = ""
    m_rise = re.search(r"\[riseReason\](.*?)(?=\[chartAnalysis\]|$)", text, re.DOTALL)
    m_chart = re.search(r"\[chartAnalysis\](.*?)$", text, re.DOTALL)
    if m_rise:
        rise = m_rise.group(1).strip()
    if m_chart:
        chart = m_chart.group(1).strip()
    return rise, chart


def analyze_with_gemini(model, stock_name: str, ticker: str, target_date: str,
                        change_pct: float, articles: list[dict],
                        is_weekly: bool = False) -> tuple[str, str]:
    if not articles:
        return f"{stock_name} 관련 기사를 수집하지 못했습니다.", ""

    prompt = build_prompt(stock_name, ticker, target_date, change_pct, articles, is_weekly)
    try:
        resp = model.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        return parse_gemini_output(resp.text)
    except Exception as e:
        print(f"    [Gemini 오류] {e}")
        return "", ""


# ─── 메인 로직 ───────────────────────────────────────────────────────────────

def enrich_date(model, data: dict, date_key: str) -> bool:
    """단일 날짜의 gainers를 보강. 변경 있으면 True 반환."""
    date_entry = data["dates"].get(date_key)
    if not date_entry:
        print(f"[skip] {date_key} — 데이터 없음")
        return False

    is_weekly = date_entry.get("type") == "weekly"
    gainers = date_entry.get("gainers", [])
    if not gainers:
        print(f"[skip] {date_key} — gainers 없음")
        return False

    print(f"\n{'='*50}")
    print(f"[{date_key}] {'주간' if is_weekly else '당일'} 상승률 {len(gainers)}종목 보강 시작")
    print(f"{'='*50}")

    changed = False
    for g in gainers:
        name = g.get("name", "")
        ticker = g.get("ticker", "")
        change_pct = float(g.get("changePct", 0))
        rank = g.get("rank", "?")

        if not ticker:
            print(f"  [{rank}] {name} — ticker 없음, skip")
            continue

        print(f"  [{rank}] {name} ({ticker}) +{change_pct:.2f}% — 뉴스 수집 중...")

        articles = fetch_naver_stock_news(ticker, date_key, max_articles=15)
        print(f"      기사 {len(articles)}개 수집 완료")

        if len(articles) < 3:
            print(f"      기사 부족 — 분석 건너뜀")
            continue

        rise_reason, chart_analysis = analyze_with_gemini(
            model, name, ticker, date_key, change_pct, articles, is_weekly
        )

        if rise_reason:
            g["riseReason"] = rise_reason
            changed = True
        if chart_analysis:
            g["chartAnalysis"] = chart_analysis
            changed = True

        # news 필드도 실제 기사로 교체
        g["news"] = [
            {"title": a["title"], "summary": a["summary"], "url": a["url"]}
            for a in articles[:5]
        ]

        time.sleep(1.5)  # API rate limit 방지

    return changed


def main():
    load_env()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[오류] GEMINI_API_KEY가 없습니다. .env.local에 추가해 주세요.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    model = client

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="단일 날짜 (예: 2026-07-16)")
    parser.add_argument("--from", dest="from_date", help="시작 날짜")
    parser.add_argument("--to", dest="to_date", help="종료 날짜")
    args = parser.parse_args()

    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    # 대상 날짜 결정
    all_dates = sorted(data["dates"].keys())
    if args.date:
        target_dates = [args.date]
    elif args.from_date and args.to_date:
        target_dates = [d for d in all_dates if args.from_date <= d <= args.to_date]
    else:
        # 기본: 7/11 이후 전체
        target_dates = [d for d in all_dates if d >= "2026-07-11"]

    print(f"보강 대상 날짜: {target_dates}")

    any_changed = False
    for date_key in target_dates:
        changed = enrich_date(model, data, date_key)
        if changed:
            any_changed = True

    if any_changed:
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n[완료] {JSON_PATH} 저장됨")
    else:
        print("\n[완료] 변경 내용 없음")


if __name__ == "__main__":
    main()
