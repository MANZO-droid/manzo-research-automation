# -*- coding: utf-8 -*-
"""
마켓스코프 자동 수집 스크립트

무엇을 하나:
  1) 텔레그램 공개 채널 13곳(t.me/s/채널명)에서 지정한 하루치(KST 05:00~익일 04:59) 메시지를 수집
  2) 메시지 본문에서 실제로 등장한 종목명을 찾아 언급 수·채널 수를 집계
  3) Gemini로 '금리 인상'처럼 특정 종목이 아닌 시장 테마(이슈)를 추가로 탐지
  4) 언급수 + 채널수*2 = score 로 순위를 매겨 상위 15개를 market-scope-data.json에 저장

사용법:
  python scripts/collect_market_scope.py --date 2026-07-11
  (⁠--date 를 생략하면 오늘 날짜로 실행)

  여러 날짜를 한 번에 채우고 싶으면 --from/--to 사용:
  python scripts/collect_market_scope.py --from 2026-07-09 --to 2026-07-10

필요 환경변수 (.env.local, 저장소에 커밋되지 않음):
  GEMINI_API_KEY
  SUPABASE_URL                사이트가 쓰는 것과 동일한 Supabase 프로젝트
  SUPABASE_SERVICE_ROLE_KEY   RLS를 우회해 market_scope_reports에 쓰기 위한 서버 전용 키
                               (2026-08-01부로 market-scope-data.json 하드코딩 대신 이걸 사용)
"""
import argparse, json, os, re, sys, time, warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
import google.generativeai as genai

# scripts/ 에서 한 단계 위가 이 저장소(리서치자동화)의 루트다(.env.local이 여기 있다).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from krx_calendar import is_trading_day  # noqa: E402

CHANNELS = [
    "moneythemestock", "valjuman", "characteristicstock", "YeouidoStory2",
    "corevalue", "HanaResearch", "bumsong2", "balanceasset", "alphasignal_now",
    "valuefinder", "HANAStrategy", "ym_research", "kmj_retailcosmetics",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


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


def kst_day_window(report_date):
    """report_date(KST 기준 리포트 날짜) -> (전일 05:00 KST ~ 당일 04:59 KST)를 UTC로 변환"""
    d = datetime.strptime(report_date, "%Y-%m-%d")
    start_kst = d - timedelta(days=1)
    start_utc = datetime(start_kst.year, start_kst.month, start_kst.day, 5, 0, 0, tzinfo=timezone.utc) - timedelta(hours=9)
    end_utc = start_utc + timedelta(days=1)
    return start_utc, end_utc


def range_label(report_date):
    d = datetime.strptime(report_date, "%Y-%m-%d")
    prev = d - timedelta(days=1)
    weekday = "월화수목금토일"
    return (f"{prev.strftime('%Y-%m-%d')}({weekday[prev.weekday()]}) 05:00 ~ "
            f"{d.strftime('%Y-%m-%d')}({weekday[d.weekday()]}) 04:59 KST")


def parse_page(html):
    soup = BeautifulSoup(html, "html.parser")
    messages = []
    for wrap in soup.select("div.tgme_widget_message"):
        post_id = wrap.get("data-post", "")
        time_tag = wrap.select_one("time")
        if not time_tag or not time_tag.get("datetime"):
            continue
        dt = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
        text_div = wrap.select_one("div.tgme_widget_message_text")
        text = text_div.get_text("\n", strip=True) if text_div else ""
        stocks = []
        if text_div:
            for a in text_div.select("a"):
                href = a.get("href", "")
                if "m.stock.naver.com" in href and ("/domestic/stock/" in href or "/worldstock/stock/" in href):
                    stocks.append(a.get_text(strip=True))
        messages.append({"id": post_id, "datetime": dt.isoformat(), "text": text, "stocks": stocks})
    return messages


def scrape_channel(channel, window_start, window_end, max_pages=40):
    all_msgs, seen_ids, before = [], set(), None
    for _ in range(max_pages):
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
        except Exception as e:
            print(f"    [{channel}] 요청 실패: {e}")
            break
        msgs = parse_page(r.text)
        new_msgs = [m for m in msgs if m["id"] not in seen_ids]
        if not new_msgs:
            break
        seen_ids.update(m["id"] for m in new_msgs)
        all_msgs.extend(new_msgs)
        oldest = min(new_msgs, key=lambda m: m["datetime"])
        if datetime.fromisoformat(oldest["datetime"]) < window_start:
            break
        before = int(oldest["id"].split("/")[-1])
        time.sleep(0.4)
    return [m for m in all_msgs if window_start <= datetime.fromisoformat(m["datetime"]) < window_end]


def scrape_window(window_start, window_end):
    by_channel = {}
    for ch in CHANNELS:
        print(f"  [{ch}] 수집 중...")
        msgs = scrape_channel(ch, window_start, window_end)
        by_channel[ch] = msgs
        print(f"    -> {len(msgs)}개")
    return by_channel


def collect_candidate_names(messages_by_channel):
    names = set()
    for msgs in messages_by_channel.values():
        for m in msgs:
            for name in m.get("stocks", []):
                name = name.strip()
                if len(name) >= 2:
                    names.add(name)
    return names


def build_stock_stats(messages_by_channel, candidate_names):
    """본문 텍스트 전체에서 종목명 언급 집계. 긴 이름부터 매칭 후 소비해 부분 문자열 충돌 방지."""
    sorted_names = sorted(candidate_names, key=len, reverse=True)
    stats = defaultdict(lambda: {"msg_ids": set(), "channels": set()})
    msg_index = {}
    for channel, msgs in messages_by_channel.items():
        for m in msgs:
            msg_id = m["id"]
            msg_index[msg_id] = {"channel": channel, "text": m["text"], "url": f"https://t.me/{msg_id}"}
            working = m["text"]
            for name in sorted_names:
                if name in working:
                    stats[name]["msg_ids"].add(msg_id)
                    stats[name]["channels"].add(channel)
                    working = working.replace(name, " " * len(name))
    return stats, msg_index


def detect_issues(msg_index, report_date, gemini_model):
    lines = [f"{mid} :: {info['text'].split(chr(10))[0][:90]}" for mid, info in msg_index.items()]
    corpus = "\n".join(lines)
    prompt = f"""아래는 {report_date} 기준 하루 동안 주식 관련 텔레그램 채널에 올라온 메시지 제목 목록입니다.
각 줄은 "메시지ID :: 제목" 형식입니다.

개별 종목명이 아니라, 여러 메시지에 걸쳐 반복되는 '시장 테마/매크로 이슈'(예: 금리 인상, 반도체 업황, 서킷브레이커, 관세 이슈 등)를 최대 3개까지 찾아주세요.
각 이슈에 대해, 그 이슈를 다루는 메시지ID 목록을 정확히 골라주세요 (있는 ID만 사용, 지어내지 마세요).

반드시 아래 JSON 배열 형식으로만 답하세요. 다른 설명 없이 JSON만 출력:
[{{"issue_name": "이슈명(짧게)", "msg_ids": ["id1", "id2"]}}]

메시지 목록:
{corpus[:12000]}
"""
    try:
        resp = gemini_model.generate_content(prompt).text.strip()
        resp = re.sub(r"^```json\s*|\s*```$", "", resp, flags=re.MULTILINE)
        issues_raw = json.loads(resp)
    except Exception as e:
        print(f"  [이슈 탐지 실패] {e}")
        return {}
    issues = {}
    for it in issues_raw:
        name = it.get("issue_name", "").strip()
        ids = [i for i in it.get("msg_ids", []) if i in msg_index]
        if not name or len(ids) < 2:
            continue
        issues[name] = {"msg_ids": set(ids), "channels": {msg_index[i]["channel"] for i in ids}}
    return issues


def make_article(mid, info):
    text = info["text"]
    return {
        "title": text.split("\n")[0][:110],
        "summary": text.replace("\n", " ")[:220],
        "url": info["url"],
    }


def build_items(stock_stats, issue_stats, msg_index, max_items=15):
    pool = []
    for name, s in stock_stats.items():
        mention, channel = len(s["msg_ids"]), len(s["channels"])
        pool.append({"name": name, "type": "종목", "mention": mention, "channel": channel,
                      "score": mention + channel * 2, "msg_ids": s["msg_ids"]})
    for name, s in issue_stats.items():
        mention, channel = len(s["msg_ids"]), len(s["channels"])
        pool.append({"name": name, "type": "이슈", "mention": mention, "channel": channel,
                      "score": mention + channel * 2, "msg_ids": s["msg_ids"]})
    pool.sort(key=lambda x: x["score"], reverse=True)
    items = []
    for rank, it in enumerate(pool[:max_items], 1):
        articles = [make_article(mid, msg_index[mid]) for mid in sorted(it["msg_ids"])]
        items.append({"rank": rank, "name": it["name"], "type": it["type"], "mention": it["mention"],
                       "channel": it["channel"], "score": it["score"], "articles": articles})
    return items


def build_report(report_date, gemini_model):
    start, end = kst_day_window(report_date)
    print(f"\n=== {report_date} 리포트 생성 (수집 구간: {start.isoformat()} ~ {end.isoformat()} UTC) ===")
    by_channel = scrape_window(start, end)
    candidates = collect_candidate_names(by_channel)
    stock_stats, msg_index = build_stock_stats(by_channel, candidates)
    print(f"  메시지 {len(msg_index)}개, 종목명 후보 {len(candidates)}개")
    issue_stats = detect_issues(msg_index, report_date, gemini_model)
    print(f"  탐지된 이슈: {list(issue_stats.keys())}")
    items = build_items(stock_stats, issue_stats, msg_index)
    for it in items:
        print(f"    {it['rank']}. {it['name']} ({it['type']}) mention={it['mention']} channel={it['channel']} score={it['score']}")
    return {
        "report_date": report_date,
        "range_label": range_label(report_date),
        "message_count": len(msg_index),
        "channel_count": len(CHANNELS),
        "items": items,
    }


def save_report_to_supabase(report: dict):
    """market_scope_reports에 report_date 기준 upsert. api/market-scope.js가
    report_date 최신 행을 current로, 나머지를 history로 구성해 서비스한다."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    row = {
        "report_date": report["report_date"],
        "range_label": report["range_label"],
        "message_count": report["message_count"],
        "channel_count": report["channel_count"],
        "items": report["items"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{url}/rest/v1/market_scope_reports?on_conflict=report_date",
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
        raise RuntimeError(f"Supabase market_scope_reports upsert 실패 {r.status_code}: {r.text}")
    print(f"[supabase] market_scope_reports upsert 완료 ({report['report_date']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="리포트 날짜 (YYYY-MM-DD), 생략 시 오늘")
    ap.add_argument("--from", dest="date_from", help="시작 날짜 (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", help="종료 날짜 (YYYY-MM-DD)")
    args = ap.parse_args()

    # "오늘"(인자 없이 자동 실행)인 경우, GEMINI API를 호출하기 전에 먼저
    # KRX 개장일 여부부터 확인해 불필요한 초기화/API 키 요구를 피한다.
    # ⚠ datetime.now()는 실행 서버의 로컬시간(UTC, GitHub Actions 러너 기준)을
    #   반환하므로 반드시 KST로 변환해서 날짜를 계산해야 한다. 예전 실행 환경
    #   (Cowork 스케줄 작업)은 로컬 PC가 이미 KST라 문제가 없었지만, GitHub
    #   Actions(UTC)로 옮기면서 이 부분을 KST 기준으로 고치지 않으면 매일
    #   05:00 KST 실행 시각(=20:00 UTC 전날)에 날짜가 하루 어긋나는 버그가 생긴다.
    if not args.date and not (args.date_from and args.date_to):
        today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        if not is_trading_day(today):
            print(f"[skip] {today}는 KRX 개장일이 아닙니다. 마켓 스코프 리포트를 생성하지 않습니다.")
            return

    load_env()
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        print("[오류] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY가 없습니다. .env.local에 추가해 주세요.")
        sys.exit(1)
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    gemini_model = genai.GenerativeModel("gemini-2.5-flash")

    if args.date_from and args.date_to:
        # 명시적 기간 백필(--from/--to)은 개장일 여부와 무관하게 항상 실행한다.
        d0 = datetime.strptime(args.date_from, "%Y-%m-%d")
        d1 = datetime.strptime(args.date_to, "%Y-%m-%d")
        dates = []
        d = d0
        while d <= d1:
            dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
    elif args.date:
        # 명시적 단일 날짜(--date)도 항상 실행(수동 백필용).
        dates = [args.date]
    else:
        # 위에서 이미 개장일 확인을 마쳤으므로 여기서는 바로 오늘 날짜를 사용한다.
        # (마찬가지로 KST 기준 - 위 today 계산과 동일한 이유)
        dates = [datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")]

    for report_date in dates:
        report = build_report(report_date, gemini_model)
        save_report_to_supabase(report)

    print(f"\n완료: {len(dates)}개 날짜 저장")


if __name__ == "__main__":
    main()
