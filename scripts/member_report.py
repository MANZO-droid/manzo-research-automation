# -*- coding: utf-8 -*-
"""
회원 종목 리포트 생성 (P3)

무엇을 하나:
  회원이 My page에서 [리포트 받기]를 누르면 member_report_requests 표에 요청이 쌓인다(사이트 쪽 DB 함수가 한도·권한 검사).
  이 스크립트는 종목 단위로 "현재 상태 리포트"를 만든다.
    - 수치(종가·이동평균 대비·52주 고저 대비·배열)는 member_prices 표의 값을 코드가 직접 채운다. AI는 숫자를 쓰지 않는다.
    - 뉴스는 제목·날짜·원문 링크만 보여준다(본문은 AI 입력에만 쓰고 복사하지 않는다).
    - AI(Groq)는 "수치와 기사에서 확인되는 사실을 설명"하는 문단만 쓴다.
      매수·매도·목표가·전망 같은 판단·예측 표현, 외국어 섞임, 근거 없는 숫자가 하나라도 있으면 그 문장 전체를 버리고
      수치 위주의 기본형(AI 설명 없음)으로 대체한다.
    - 회원 정보(매수가·수량·손익)는 이 스크립트에 들어오지 않는다. 입력은 종목코드뿐이다.

설계서: manzo-site 저장소 docs/superpowers/specs/2026-10-25-member-report-addendum.md (§5~§7)

사용법 (P3 — 메일은 보내지 않는다):
  python scripts/member_report.py --preview 005930 --out out          # 견본 리포트 파일 생성(HTML·텍스트·요약 JSON)
  python scripts/member_report.py --preview 005930 --no-ai            # AI 없이 기본형만
  python scripts/member_report.py --preview 005930 --price-json p.json --news-json n.json   # 오프라인 시험용 입력

  실제 요청 처리(process_queue)와 메일 발송은 P4에서 연결한다. 지금은 함수와 테스트만 있다.

필요 환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY(시세 조회), GROQ_API_KEY(AI 설명, 없으면 기본형)
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

DISCLAIMER = "본 메일은 공개된 정보를 정리한 것이며 투자 권유·자문이 아닙니다. 투자 판단과 책임은 본인에게 있습니다."
REQUESTED_NOTE = "회원님이 직접 요청해 발송된 메일입니다."
NEWS_MAX = 8

# ─── 안전장치 1: 금지 표현 ────────────────────────────────────────────────────────
# 행동 권유(매수·매도 등), 목표가, 전망·예상·예측·기대, "~할 것", "~가능성", 평가(저평가 등)는 쓰지 않는다.
# 보수적으로 잡았으므로 정상 문장이 걸려 기본형으로 대체될 수 있다(그쪽이 안전하다).
BANNED_PATTERNS = [
    # "순매수·순매도"는 수급 수치의 이름(사실)이라 허용하고, 행동 권유로 쓰이는 "매수 의견"·"매도 신호" 등은 막는다.
    r"(?<!순)매수", r"(?<!순)매도", r"추천", r"목표\s?(주)?가", r"전망", r"예상", r"예측", r"기대", r"유망",
    r"상승\s?여력", r"하락\s?여력", r"사야", r"팔아", r"사라", r"팔라", r"손절", r"익절",
    r"비중\s?(확대|축소|조절)", r"담아", r"관망", r"투자\s?의견", r"저평가", r"고평가", r"적정\s?주가",
    r"(오를|내릴|상승할|하락할|반등할|돌파할|넘을|깰)\s?(것|가능|듯|수)",
    r"것으로\s?(보인|보입|예상|전망|판단|기대|추정)",
    r"가능성(이|은|도)?\s?(높|있|크|낮|열려)",
    r"(?i:\b(buy|sell|hold|target\s?price)\b)",
]
_BANNED_RE = re.compile("|".join("(?:%s)" % p for p in BANNED_PATTERNS))

# ─── 안전장치 1-2: 자극적인 표현 (회장님 지시 2026-10-28: "폭락장·대반전 시나리오 같은 표현은 가능하면 걸러달라") ─────────
# 뉴스 제목에 이 표현이 있으면 그 기사는 리포트에서 뺀다. AI 설명문에 쓰이면 그 문단을 버린다.
SENSATIONAL_PATTERNS = [
    r"폭락", r"폭등", r"폭망", r"폭증", r"패닉", r"피눈물", r"눈물", r"충격", r"쇼크", r"대반전", r"반전", r"시나리오",
    r"멘붕", r"지옥", r"대박", r"쪽박", r"떡상", r"떡락", r"곡소리", r"난리", r"공포", r"광풍", r"대란", r"붕괴", r"초토화",
    r"비명", r"아수라장", r"아우성",
]
_SENSATIONAL_RE = re.compile("|".join(SENSATIONAL_PATTERNS))


def find_sensational(text):
    return [m.group(0) for m in _SENSATIONAL_RE.finditer(text or "")]


# ─── 안전장치 2: 외국어 섞임 (collect_gainers.has_language_issue 와 같은 범위) ──────────
_FOREIGN_RE = re.compile("[぀-ヿ฀-๿一-鿿ƀ-ɏḀ-ỿ]")


def find_banned(text):
    """금지 표현에 걸린 부분 목록(없으면 빈 리스트)."""
    return [m.group(0) for m in _BANNED_RE.finditer(text or "")]


def default_lang_check(text):
    return bool(_FOREIGN_RE.search(text or ""))


# ─── 안전장치 3: 근거 없는 숫자 ────────────────────────────────────────────────────
def _num_tokens(text):
    out = set()
    for tok in re.findall(r"\d[\d,]*\.?\d*", text or ""):
        tok = tok.replace(",", "").rstrip(".")
        if tok:
            out.add(tok)
    return out


def unsupported_numbers(text, allowed_text):
    """text 안의 두 자리 이상 숫자 중, allowed_text(제공한 수치·기사)에 없는 것. 한 자리 숫자는 검사하지 않는다."""
    allowed = _num_tokens(allowed_text)
    bad = []
    for tok in sorted(_num_tokens(text)):
        if len(tok.replace(".", "")) < 2:
            continue
        if tok not in allowed:
            bad.append(tok)
    return bad


# ─── 수치 (사이트 lib/holdings-calc.js 의 chartPosition 과 같은 규칙) ──────────────────
def _num(v):
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def chart_position(price):
    keys = ("ma5", "ma20", "ma60", "ma120", "high52", "low52")
    empty = {"alignment": None, "vs": {k: None for k in keys}}
    if not price:
        return empty
    close = _num(price.get("close"))
    if close is None:
        return empty

    def rel(base):
        b = _num(base)
        return (close / b - 1) * 100 if b else None

    m5, m20, m60 = _num(price.get("ma5")), _num(price.get("ma20")), _num(price.get("ma60"))
    alignment = None
    if m5 is not None and m20 is not None and m60 is not None:
        alignment = "정배열" if m5 > m20 > m60 else ("역배열" if m5 < m20 < m60 else "혼조")
    return {"alignment": alignment, "vs": {
        "ma5": rel(price.get("ma5")), "ma20": rel(price.get("ma20")), "ma60": rel(price.get("ma60")),
        "ma120": rel(price.get("ma120")), "high52": rel(price.get("high_52w")), "low52": rel(price.get("low_52w")),
    }}


def _won(n):
    return f"{round(n):,}원"


def _pct1(v):
    return "-" if v is None else f"{v:+.1f}%"


def _eok(n):
    return f"{n / 1e8:+,.1f}억원"


def build_facts(price, investors=None):
    """리포트에 들어갈 수치를 '화면에 표시할 문자열' 그대로 만든다(AI에게도 이 문자열만 준다)."""
    cp = chart_position(price)
    vs = cp["vs"]
    rows = [
        ("종가", f"{_won(_num(price['close']))} ({price['trade_date']} 기준)"),
        ("5일선 대비", _pct1(vs["ma5"])), ("20일선 대비", _pct1(vs["ma20"])),
        ("60일선 대비", _pct1(vs["ma60"])), ("120일선 대비", _pct1(vs["ma120"])),
        ("52주 고점 대비 (종가 기준)", _pct1(vs["high52"])), ("52주 저점 대비 (종가 기준)", _pct1(vs["low52"])),
        ("이동평균 배열", cp["alignment"] or "데이터 부족"),
    ]
    inv_rows = []
    if investors and any(investors.get(k) for k in ("foreign", "institution", "individual")):
        inv_rows = [("외국인 순매수", _eok(investors["foreign"])), ("기관 순매수", _eok(investors["institution"])),
                    ("개인 순매수", _eok(investors["individual"]))]
    return {"rows": rows, "investor_rows": inv_rows, "alignment": cp["alignment"], "vs": vs}


def summary_sentence(name, price, facts):
    """AI 없이도 만들 수 있는 한 줄 요약(사실 문장만)."""
    parts = [f"{name}의 종가는 {_won(_num(price['close']))}({price['trade_date']} 기준)입니다."]
    v20 = facts["vs"]["ma20"]
    if v20 is not None:
        parts.append(f"20일선보다 {abs(v20):.1f}% {'높은' if v20 >= 0 else '낮은'} 위치입니다.")
    if facts["alignment"]:
        parts.append(f"이동평균 배열은 {facts['alignment']}입니다.")
    return " ".join(parts)


def facts_text(facts):
    lines = [f"{k}: {v}" for k, v in facts["rows"] + facts["investor_rows"]]
    return "\n".join(lines)


# ─── AI 설명 ─────────────────────────────────────────────────────────────────────
def build_prompt(name, code, ftext, articles):
    arts = "\n".join(f"[{i}] ({a.get('date', '')}) {a.get('title', '')}\n{a.get('summary', '')}"
                     for i, a in enumerate(articles, 1)) or "(수집된 기사 없음)"
    return f"""당신은 한국 주식 시장 정보를 정리하는 에디터입니다. 아래 종목에 대해, 제공된 수치와 기사만 근거로 현재 상태를 설명하는 글을 쓰세요.
반드시 순수 한국어(한글)로만 작성하세요. 한자·영어 단어·다른 나라 글자를 섞지 마세요.

[작성 규칙]
- 2~3개 문단, 전체 500자 이내. 제목·목록·마크다운 없이 줄글로 씁니다.
- 문장은 "~입니다", "~했습니다" 같은 존댓말로 씁니다. "~이다", "~했다" 같은 반말체는 쓰지 않습니다.
- 첫 문단: 수치로 본 현재 위치를 설명합니다. 표에 이미 있는 수치를 전부 나열하지 말고, 가장 두드러진 2~3개만 골라 쓰세요.
- 둘째 문단: 기사에서 확인되는 최근 사건·이슈를 사실대로 요약합니다. 기사에 없는 내용은 절대 지어내지 마세요.
- 이동평균선·52주 고점·저점 대비 수치는 기준선보다 "높은/낮은 위치"라고 표현하세요. 그 수치를 보고 "상승했다·하락했다"고 쓰지 마세요(변동이 아니라 위치입니다).
- 숫자는 아래 '제공된 수치'와 기사에 있는 값만, 표기 그대로 쓰세요. 계산하거나 반올림해서 새 숫자를 만들지 마세요.
- 금지: 매수·매도·보유·손절 등 행동 권유, 목표가, 전망·예상·예측·기대, "~할 것", "~가능성", 호재·악재 같은 평가, 투자의견.
  과거와 현재의 사실만 씁니다.
- 수급(거래 흐름)을 언급할 때는 제공된 값을 부호까지 그대로 "외국인 순매수 -2,101.7억원"처럼 쓰세요. "순매도"라는 말과 괄호 부연("(순매도)" 등)은 쓰지 마세요. 그 밖의 "매수세·매도세" 같은 표현도 쓰지 마세요.
- 자극적인 표현(폭락·폭등·패닉·충격·대반전·시나리오 등)은 쓰지 말고, 차분한 서술로 씁니다.
- 기사가 없으면 둘째 문단은 "최근 수집된 관련 기사가 없습니다."라고만 씁니다.

종목: {name} ({code})

제공된 수치:
{ftext}

기사:
{arts}
"""


def validate_ai_text(text, allowed_text, lang_check=default_lang_check):
    """통과하면 None, 아니면 거절 사유(문자열)."""
    t = (text or "").strip()
    if not t:
        return "빈 응답"
    if len(t) < 60 or len(t) > 900:
        return f"길이({len(t)}자)"
    if lang_check(t):
        return "외국어 섞임"
    hits = find_banned(t)
    if hits:
        return "금지 표현: " + ", ".join(sorted(set(hits))[:5])
    spicy = find_sensational(t)
    if spicy:
        return "자극적 표현: " + ", ".join(sorted(set(spicy))[:5])
    if "순매도" in t:
        return "표기 규칙: '순매도' 사용(부호 있는 순매수 값으로만 표기)"
    if re.search(r"(?m)^\s*(#|[-*•]\s)", t):
        return "형식(제목·목록)"
    bad = unsupported_numbers(t, allowed_text)
    if bad:
        return "근거 없는 숫자: " + ", ".join(bad[:5])
    return None


_ODD_MINUS_RE = re.compile("[‐‑‒–−](?=\\d)")


def normalize_ai_text(text):
    """AI가 숫자 앞 마이너스를 하이픈 모양의 다른 문자(‑, −, –)로 쓰는 경우가 있어 일반 '-'로 바꾼다."""
    return _ODD_MINUS_RE.sub("-", text or "")


def make_news_fn(cg, today=None):
    """네이버 종목 뉴스 수집 함수를 만든다. 기준은 시세일이 아니라 '오늘(KST) 기준 최근 7일'이다.
    (시세일 기준으로 찾으면, 맨 앞 페이지가 전부 시세일 이후 기사일 때 수집이 0건으로 끝난다 - 2026-10-28 실제 확인)"""
    def fetch(code, trade_date):
        d = today or datetime.now(KST).strftime("%Y-%m-%d")
        return cg.fetch_stock_news(code, d, max_articles=12, days_before=7, days_after=1)
    return fetch


def ai_explanation(call, name, code, facts, articles, lang_check=default_lang_check, attempts=2, on_reject=None):
    """(설명문 또는 None, 마지막 거절 사유). 거절되면 한 번 더 시도하고, 그래도 안 되면 None.
    on_reject(text, reason): 거절된 문장을 확인용으로 받아 볼 수 있는 선택 콜백(견본 점검용)."""
    ftext = facts_text(facts)
    allowed = ftext + "\n" + "\n".join(f"{a.get('title', '')} {a.get('summary', '')} {a.get('date', '')}" for a in articles)
    prompt = build_prompt(name, code, ftext, articles)
    reason = "AI 호출 안 함"
    for _ in range(attempts):
        text = normalize_ai_text((call(prompt) or "").strip())
        reason = validate_ai_text(text, allowed, lang_check)
        if reason is None:
            return text, None
        if on_reject:
            on_reject(text, reason)
    return None, reason


# ─── 리포트 조립·표시 ─────────────────────────────────────────────────────────────
def _safe_url(u):
    u = str(u or "")
    return u if re.match(r"^https?://", u, re.I) else ""


def _prefer_relevant(name, articles):
    """종목 이름이 제목·요약에 나오는 기사만 고른다(네이버 종목 뉴스 피드에는 무관한 기사도 섞여 온다).
    그런 기사가 3건 미만이면 걸러내지 않고 전체를 쓴다(뉴스가 텅 비는 것보다 낫다). 반환 길이는 NEWS_MAX 이하."""
    every = _dedupe_news(articles, limit=50)
    matched = [a for a in every if name and name in (a["title"] + " " + a.get("summary", ""))]
    return (matched if len(matched) >= 3 else every)[:NEWS_MAX]


def _dedupe_news(articles, limit=NEWS_MAX):
    seen, out = set(), []
    for a in articles or []:
        # 네이버가 제목에 &quot; 같은 HTML 코드를 그대로 담아 주므로 먼저 풀어 둔다(표시할 때 한 번만 이스케이프한다)
        t = html.unescape(a.get("title") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(dict(a, title=t, summary=html.unescape(a.get("summary") or "")))
        if len(out) >= limit:
            break
    return out


def build_report(code, price, articles, investors=None, ai_text=None):
    """리포트 한 건을 만든다. 반환: {stock_code, report_date, subject, html, text, used_ai}"""
    name = price.get("stock_name") or code
    facts = build_facts(price, investors)
    news = _dedupe_news(articles)
    subject = f"[만조] {name}({code}) 현재 상태 리포트 — {price['trade_date']}"
    e = html.escape

    def table(rows):
        return "".join(
            f'<tr><td style="padding:6px 10px;color:#6b6b80;border-bottom:1px solid #eee">{e(k)}</td>'
            f'<td style="padding:6px 10px;text-align:right;border-bottom:1px solid #eee"><b>{e(v)}</b></td></tr>' for k, v in rows)

    def h2(t):
        return f'<h2 style="font-size:15px;margin:22px 0 8px;color:#1a1a2e">{e(t)}</h2>'

    parts = [f'<h1 style="font-size:19px;margin:0 0 4px;color:#1a1a2e">{e(name)} <span style="font-weight:400;color:#6b6b80">{e(code)}</span></h1>',
             f'<p style="margin:0 0 14px;color:#6b6b80;font-size:13px">현재 상태 리포트 · {e(price["trade_date"])} 기준</p>',
             f'<p style="margin:0 0 6px;font-size:14px;line-height:1.7">{e(summary_sentence(name, price, facts))}</p>',
             h2("가격·차트 위치 (수치)"), f'<table style="width:100%;border-collapse:collapse;font-size:14px">{table(facts["rows"])}</table>']
    if facts["investor_rows"]:
        parts += [h2("거래·수급 흐름 (최근 거래일, 네이버 금융 기준 근사값)"),
                  f'<table style="width:100%;border-collapse:collapse;font-size:14px">{table(facts["investor_rows"])}</table>']
    parts.append(h2("최근 뉴스 (제목·날짜·링크)"))
    if news:
        items = []
        for a in news:
            url = _safe_url(a.get("url"))
            title = e(a["title"])
            link = f'<a href="{e(url)}" style="color:#4A6FA5;text-decoration:none">{title}</a>' if url else title
            items.append(f'<li style="margin:0 0 6px;line-height:1.5">{link} <span style="color:#9a9aa8;font-size:12px">{e(a.get("date", ""))}</span></li>')
        parts.append('<ul style="padding-left:18px;margin:0;font-size:14px">' + "".join(items) + "</ul>")
    else:
        parts.append('<p style="margin:0;font-size:14px;color:#6b6b80">최근 수집된 관련 기사가 없습니다.</p>')
    if ai_text:
        paras = "".join(f'<p style="margin:0 0 10px;font-size:14px;line-height:1.75">{e(p.strip())}</p>' for p in ai_text.split("\n") if p.strip())
        parts += [h2("현재 상태 설명"), paras]
    parts.append(f'<hr style="border:none;border-top:1px solid #ddd;margin:24px 0 12px">'
                 f'<p style="margin:0 0 4px;font-size:12px;color:#8a8a98;line-height:1.6">{e(DISCLAIMER)}</p>'
                 f'<p style="margin:0;font-size:12px;color:#8a8a98">{e(REQUESTED_NOTE)}</p>')
    body_html = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"></head>'
                 '<body style="margin:0;padding:20px;background:#f6f4ef;font-family:Malgun Gothic,Apple SD Gothic Neo,sans-serif">'
                 '<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;padding:24px;color:#1a1a2e">'
                 + "".join(parts) + "</div></body></html>")

    lines = [f"{name} ({code}) 현재 상태 리포트 — {price['trade_date']} 기준", "", summary_sentence(name, price, facts), "", "[가격·차트 위치]"]
    lines += [f"- {k}: {v}" for k, v in facts["rows"]]
    if facts["investor_rows"]:
        lines += ["", "[거래·수급 흐름 (최근 거래일, 근사값)]"] + [f"- {k}: {v}" for k, v in facts["investor_rows"]]
    lines += ["", "[최근 뉴스]"]
    lines += [f"- {a['title']} ({a.get('date', '')}) {_safe_url(a.get('url'))}".rstrip() for a in news] or ["- 최근 수집된 관련 기사가 없습니다."]
    if ai_text:
        lines += ["", "[현재 상태 설명]", ai_text.strip()]
    lines += ["", DISCLAIMER, REQUESTED_NOTE]
    return {"stock_code": code, "report_date": price["trade_date"], "subject": subject,
            "html": body_html, "text": "\n".join(lines), "used_ai": bool(ai_text)}


# ─── Supabase REST (service role) ───────────────────────────────────────────────
class Db:
    def __init__(self, url=None, key=None, session=None):
        import requests
        self.url = (url or os.environ["SUPABASE_URL"]).rstrip("/")
        self.key = key or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self.s = session or requests.Session()

    def _h(self, extra=None):
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        h.update(extra or {})
        return h

    def get(self, table, params):
        r = self.s.get(f"{self.url}/rest/v1/{table}", headers=self._h(), params=params, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Supabase {table} 조회 실패 {r.status_code}")
        return r.json()

    def upsert(self, table, rows, on_conflict):
        r = self.s.post(f"{self.url}/rest/v1/{table}", headers=self._h({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                        params={"on_conflict": on_conflict}, data=json.dumps(rows), timeout=30)
        if not r.ok:
            raise RuntimeError(f"Supabase {table} 저장 실패 {r.status_code}")

    def patch(self, table, params, body):
        """조건에 맞는 행을 고치고, 실제로 고쳐진 행 목록을 돌려준다(0행이면 이미 다른 곳에서 처리된 것)."""
        r = self.s.patch(f"{self.url}/rest/v1/{table}", headers=self._h({"Prefer": "return=representation"}),
                         params=params, data=json.dumps(body), timeout=30)
        if not r.ok:
            raise RuntimeError(f"Supabase {table} 수정 실패 {r.status_code}")
        return r.json()

    def user_email(self, user_id):
        r = self.s.get(f"{self.url}/auth/v1/admin/users/{user_id}", headers=self._h(), timeout=30)
        return (r.json().get("email") if r.ok else None) or None


# ─── 리포트 만들기(종목·날짜별 재사용) ────────────────────────────────────────────────
class ReportMaker:
    """종목코드 하나로 리포트를 만든다. 같은 종목·같은 시세일에 이미 만든 리포트가 있으면 재사용한다(AI 호출 절약)."""

    def __init__(self, db, news_fn, investors_fn=None, ai_call=None, lang_check=default_lang_check, log=print):
        self.db, self.news_fn, self.investors_fn = db, news_fn, investors_fn
        self.ai_call, self.lang_check, self.log = ai_call, lang_check, log

    def __call__(self, code):
        rows = self.db.get("member_prices", {"select": "stock_code,stock_name,market,close,ma5,ma20,ma60,ma120,high_52w,low_52w,trade_date",
                                              "stock_code": f"eq.{code}", "limit": "1"})
        if not rows or _num(rows[0].get("close")) is None:
            raise RuntimeError("시세 정보가 없어 리포트를 만들 수 없습니다")
        price = rows[0]
        cached = self.db.get("member_stock_reports", {"select": "subject,body_html,body_text,used_ai",
                                                       "stock_code": f"eq.{code}", "report_date": f"eq.{price['trade_date']}", "limit": "1"})
        if cached:
            c = cached[0]
            return {"stock_code": code, "report_date": price["trade_date"], "subject": c["subject"],
                    "html": c["body_html"], "text": c["body_text"], "used_ai": c["used_ai"]}
        report = generate(code, price, self.news_fn, self.investors_fn, self.ai_call, self.lang_check, self.log)
        self.db.upsert("member_stock_reports", [{
            "stock_code": code, "report_date": report["report_date"], "subject": report["subject"],
            "body_html": report["html"], "body_text": report["text"], "used_ai": report["used_ai"]}], "stock_code,report_date")
        return report


def generate(code, price, news_fn, investors_fn=None, ai_call=None, lang_check=default_lang_check, log=print, on_reject=None):
    """수집(뉴스·수급) → AI 설명(검증 통과 시) → 조립. 저장은 하지 않는다."""
    try:
        articles = news_fn(code, price["trade_date"]) or []
    except Exception as ex:
        log(f"[뉴스 수집 실패] {code}: {type(ex).__name__}")
        articles = []
    articles = [a for a in articles if not find_sensational(a.get("title", ""))]   # 자극적인 제목의 기사는 뺀다
    articles = _prefer_relevant(price.get("stock_name") or code, articles)
    investors = None
    if investors_fn:
        try:
            investors = investors_fn(code, int(_num(price["close"])))
        except Exception as ex:
            log(f"[수급 수집 실패] {code}: {type(ex).__name__}")
    ai_text = None
    if ai_call:
        facts = build_facts(price, investors)
        ai_text, reason = ai_explanation(ai_call, price.get("stock_name") or code, code, facts, _dedupe_news(articles), lang_check,
                                         on_reject=on_reject)
        if ai_text is None:
            log(f"[AI 설명 버림 → 기본형] {code}: {reason}")
    return build_report(code, price, articles, investors, ai_text)


# ─── 요청 처리 (P4에서 실제 발송기를 연결한다) ──────────────────────────────────────────
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _short_error(ex):
    msg = _EMAIL_RE.sub("***", f"{type(ex).__name__}: {ex}")   # 메일 주소는 기록에 남기지 않는다
    return msg[:200]


class FakeSender:
    """시험용 발송기: 실제로 보내지 않고 기록만 한다(수신 주소는 가려서 보관)."""

    def __init__(self):
        self.sent = []

    def send(self, to, subject, html_body, text_body):
        self.sent.append({"to": (to[0] + "***@" + to.split("@", 1)[1]) if "@" in to else "***", "subject": subject,
                          "html": html_body, "text": text_body})


def process_queue(db, sender, make_report, limit=20, now=None, log=print):
    """대기(pending) 요청을 처리한다. 요청을 '처리 중'으로 선점한 것만 처리하므로 두 번 처리되지 않는다.
    같은 종목은 리포트를 한 번만 만들고, 한 요청이 실패해도 나머지는 계속한다."""
    now = now or datetime.now(timezone.utc)
    pending = db.get("member_report_requests", {"select": "id,user_id,stock_code,requested_at", "status": "eq.pending",
                                                 "order": "requested_at.asc", "limit": str(limit)})
    out = {"claimed": 0, "sent": 0, "error": 0}
    made = {}
    for req in pending:
        rid = req["id"]
        if not db.patch("member_report_requests", {"id": f"eq.{rid}", "status": "eq.pending"}, {"status": "processing"}):
            continue                                               # 다른 실행이 먼저 가져감
        out["claimed"] += 1
        try:
            code = req["stock_code"]
            if code not in made:
                made[code] = make_report(code)
            rep = made[code]
            to = db.user_email(req["user_id"])
            if not to:
                raise RuntimeError("수신 주소를 찾지 못했습니다")
            sender.send(to, rep["subject"], rep["html"], rep["text"])
            db.patch("member_report_requests", {"id": f"eq.{rid}"},
                     {"status": "sent", "processed_at": now.isoformat(), "error_message": None})
            out["sent"] += 1
        except Exception as ex:
            log(f"[요청 실패] {rid}: {type(ex).__name__}")
            db.patch("member_report_requests", {"id": f"eq.{rid}"},
                     {"status": "error", "processed_at": now.isoformat(), "error_message": _short_error(ex)})
            out["error"] += 1
    return out


# ─── 견본 만들기 (CLI) ────────────────────────────────────────────────────────────
def _load_env():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in (".env.local", ".env"):
        path = os.path.join(root, fname)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description="회원 종목 리포트 견본 생성(메일은 보내지 않음)")
    ap.add_argument("--preview", metavar="CODE", required=True, help="종목코드 6자리")
    ap.add_argument("--out", default="out", help="결과 폴더(기본 out)")
    ap.add_argument("--no-ai", action="store_true", help="AI 설명 없이 기본형만")
    ap.add_argument("--price-json", help="시세 행(JSON 파일). 없으면 Supabase member_prices 에서 읽음")
    ap.add_argument("--news-json", help="뉴스 목록(JSON 파일). 없으면 네이버에서 수집")
    ap.add_argument("--no-investors", action="store_true", help="수급 수집 생략")
    ap.add_argument("--show-rejected", action="store_true", help="검증에서 버려진 AI 문장과 사유를 출력(견본 점검용)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"\d{6}", a.preview):
        sys.exit("종목코드는 숫자 6자리여야 합니다")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    _load_env()

    code = a.preview
    if a.price_json:
        with open(a.price_json, encoding="utf-8") as f:
            price = json.load(f)
    else:
        rows = Db().get("member_prices", {"select": "stock_code,stock_name,market,close,ma5,ma20,ma60,ma120,high_52w,low_52w,trade_date",
                                          "stock_code": f"eq.{code}", "limit": "1"})
        if not rows:
            sys.exit(f"member_prices 에 {code} 시세가 없습니다")
        price = rows[0]

    cg = None
    if not (a.news_json and a.no_investors and (a.no_ai or not os.environ.get("GROQ_API_KEY"))):
        import collect_gainers as cg   # 기존 수집·Groq 함수를 재사용한다(groq·bs4 필요)

    if a.news_json:
        with open(a.news_json, encoding="utf-8") as f:
            _news = json.load(f)
        news_fn = lambda c, d: _news
    else:
        news_fn = make_news_fn(cg)
    investors_fn = None
    if not a.no_investors:
        def investors_fn(c, close):
            v = cg.fetch_investor_netbuy(c, close)
            return v if any(v.values()) else None      # 전부 0이면 수집 실패로 보고 생략
    ai_call = None
    if not a.no_ai and os.environ.get("GROQ_API_KEY"):
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        ai_call = lambda prompt: cg.call_groq_with_retry(client, prompt)
    elif not a.no_ai:
        print("[안내] GROQ_API_KEY 가 없어 AI 설명 없이 기본형으로 만듭니다.")

    def show_rejected(text, reason):
        print(f"[버려진 AI 문장] 사유: {reason}\n---\n{text}\n---")

    report = generate(code, price, news_fn, investors_fn, ai_call, on_reject=show_rejected if a.show_rejected else None)
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.join(a.out, f"member-report-{code}-{report['report_date']}")
    with open(stem + ".html", "w", encoding="utf-8") as f:
        f.write(report["html"])
    with open(stem + ".txt", "w", encoding="utf-8") as f:
        f.write(report["text"])
    print(f"제목: {report['subject']}\nAI 설명 포함: {report['used_ai']}\n저장: {stem}.html / .txt")
    return report


if __name__ == "__main__":
    main()
