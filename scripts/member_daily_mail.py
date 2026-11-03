# -*- coding: utf-8 -*-
"""
회원 일일 메일 (2-3단계): 조건 검색 결과 + 내 보유 종목 알람

설계서: manzo-site 저장소 docs/superpowers/specs/2026-11-02-member-daily-mail-design.md

흐름
  1) 회장님 PC 가 매일 올린 member_daily_picks(오늘 기준일의 스터디 종목 사실 값)를 읽는다.
  2) 메일 수신에 동의한 회원(member_mail_consent.digest_consent = true)만 대상으로 한다.
  3) 회원마다 [조건 검색 결과] + [내 보유 종목 알람(리포트 허용·등록 종목이 있을 때)] 을 조립한다.
     - 문장은 정해진 틀에 사실 값을 채운다(설계서 §5-2). 선정 이유·매수 암시 표현을 쓰지 않는다.
     - 알람선: 회원이 입력한 평균 매수가 대비 −x%(member_mail_prefs, 기본 3). 입력값은 시뮬레이션일 수 있어
       "손익" 대신 "입력하신 평균 매수가 대비 변동률(%)"로만 표시하고 금액은 쓰지 않는다.
  4) 금지 표현 검사를 통과한 메일만 발송하고, 회원·날짜별 기록(member_daily_mail_log)으로 하루 1통을 보장한다.

안전장치
  - 발송 승인 장치(member_report_mail.send_gate)를 통과해야 한다(MEMBER_REPORT_SEND_APPROVED=yes + 발송 수단).
  - 단계 제한: MEMBER_DAILY_RELEASE_ALL=yes 가 없으면 MEMBER_DAILY_TEST_ONLY(허용 주소 목록)의 주소로만 보낸다.
    둘 다 없으면 아무에게도 보내지 않는다.
  - 오늘이 거래일이고 오늘 기준일 자료가 있어야 한다(없으면 보내지 않고 회장님께 한 줄 알림).
  - 로그와 회장님 알림에는 개수만 남긴다(회원 이메일·종목코드·매수가 금지).

사용법
  python scripts/member_daily_mail.py --preview [--out out]    # 첫 대상 회원 한 명의 메일을 파일로만 만든다(발송·기록 없음)
  python scripts/member_daily_mail.py --send                   # 실제 발송(승인 장치 필요)
  --date YYYY-MM-DD   기준일 지정(시험용). 기본은 오늘(KST).
"""
import argparse
import base64
import html
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import member_report as mr  # noqa: E402
import member_report_mail as mm  # noqa: E402

KST = mr.KST
SITE_URL = "https://manzo-site.vercel.app"
DEFAULT_ALERT_PCT = 3
MAX_HOLDINGS = 10

SUMMARY_DISCLAIMER = ("이 메일은 공개된 시세·뉴스를 조건에 따라 정리한 정보이며 특정 종목의 매수·매도를 권유하지 않습니다. "
                      "투자 판단과 책임은 본인에게 있습니다.")
RANK_NOTE = "순위는 정배열 지속일수와 변동성 대비 상승 속도 등을 점수로 계산한 순서이며, 이 점수가 종목의 향후 흐름을 뜻하지 않습니다."
UNSUBSCRIBE_NOTE = ("이 메일은 My page에서 메일 수신에 동의하신 회원께 거래일 장 마감 후 하루 1통 발송됩니다. "
                    "수신을 원하지 않으시면 My page의 '메일 수신 설정'에서 체크를 해제해 주세요.")
HOLDINGS_NOTE = ("평균 매수가는 회원님이 My page에 입력하신 값이며 실제 매수 여부와 무관한 시뮬레이션 값일 수 있습니다. "
                 "알람선은 회원님이 직접 정하는 참고용 기준이며, 입력하신 평균 매수가에서 해당 비율만큼 내려간 가격에 "
                 "닿았는지 알려 드릴 뿐 매도 시점을 알려 드리는 것이 아닙니다.")

# ─── 금지 표현 검사 ────────────────────────────────────────────────────────────────
# 회원 리포트 검사기(member_report.find_banned)와 같은 규칙에 일일 메일 전용 항목을 더한다.
EXTRA_BANNED = [r"후보", r"진입", r"청산", r"처분", r"눌림"]
_EXTRA_RE = re.compile("|".join(EXTRA_BANNED))


def find_forbidden(text):
    """금지 표현·자극적 표현에 걸린 부분 목록(없으면 빈 리스트)."""
    return mr.find_banned(text) + [m.group(0) for m in _EXTRA_RE.finditer(text or "")] + mr.find_sensational(text)


# ─── 숫자 표시 ────────────────────────────────────────────────────────────────────
MINUS = "−"


def _signed(v, digits=1):
    return f"{MINUS if v < 0 else '+'}{abs(v):.{digits}f}%"


def _won(v):
    return f"{round(v):,}원"


def _chg(price, base):
    """price 가 base 대비 몇 % 인지. 기준이 없으면 None."""
    return None if (price is None or not base) else (price / base - 1) * 100


# ─── 조건 검색 결과(스터디 종목) 문구: 설계서 §5-2 의 정해진 틀 ─────────────────────────
def headline(f):
    ad, nh, disp = f["align_days"] or 0, f["new_high"], f["disp_pct"]
    if ad >= 20 and nh:
        return "정배열이 20거래일 이상 이어지고 있으며 오늘 60일 신고가를 기록했습니다"
    if ad >= 20:
        return "정배열이 20거래일 이상 이어지고 있습니다"
    if nh:
        return "오늘 60일 신고가를 기록했습니다"
    if disp is not None and disp < 0:
        return "20일선 아래에서 거래되고 있습니다"
    if ad > 0:
        return "정배열 상태이며 지속 기간은 짧은 편입니다"
    return "정배열이 아직 만들어지지 않았습니다"


def chart_bits(f):
    ad, nh, dsh = f["align_days"] or 0, f["new_high"], f["days_since_high"]
    disp, rsi, atr = f["disp_pct"], f["rsi"], f["atr_pct"]
    bits = [f"5·20·60일선이 {ad}거래일 연속 정배열" if ad > 0 else "정배열이 아직 만들어지지 않음"]
    if nh:
        bits.append("오늘 60일 신고가를 기록")
    elif dsh is not None and dsh < 10:
        bits.append(f"{dsh}거래일 전 60일 신고가, 아직 그 부근")
    if disp is not None:
        bits.append(f"20일선 {_signed(disp)} {'위' if disp >= 0 else '아래'}에 있습니다")
    if rsi is not None:
        bits.append(f"RSI {rsi:.0f} ({'75 초과' if rsi > 75 else ('50~75' if rsi >= 50 else '50 미만')})")
    if atr is not None:
        bits.append(f"일간 변동폭 {atr * 100:.1f}% ({'3% 미만' if atr < 0.03 else ('3~5%' if atr < 0.05 else '5% 이상')})")
    return bits


def chart_warn(f):
    atr, disp = f["atr_pct"], f["disp_pct"]
    if atr is not None and atr >= 0.05:
        return f"일간 변동폭이 {atr * 100:.1f}%로 큰 편입니다. 최근 공시·실적에 변화가 있었는지 확인해 보시기 바랍니다."
    if disp is not None and disp >= 40:
        return f"20일선보다 {disp:.0f}% 높은 위치입니다(단기간 상승폭이 큰 상태)."
    return None


def clean_news(items):
    """기사 제목에 금지·자극적 표현이 있으면 그 기사는 뺀다(증권사 '목표가' 기사 등이 추천처럼 읽히는 것을 막는다)."""
    out = []
    for a in items or []:
        title = html.unescape(str(a.get("title") or "")).strip()
        if title and not find_forbidden(title):
            out.append({"title": title, "link": mr._safe_url(a.get("link"))})
    return out


def news_note(shown, negative):
    if shown == 0:
        return "관련 기사가 없습니다"
    if negative > 0:
        return f"기사 {shown}건 중 부정적 표현이 포함된 기사가 {negative}건 있습니다"
    return f"최근 기사 {shown}건에서 뚜렷한 악재는 걸리지 않았습니다"


def _decode_png(b64):
    if not b64:
        return None
    try:
        return base64.b64decode(b64, validate=True)
    except Exception:
        return None


def _num(v):
    return mr._num(v)


def pick_view(row):
    """DB 행 → 표시용 값 모음(문장·숫자). 문장은 위의 틀 함수만 만든다."""
    price, open_p, prev = _num(row.get("price")), _num(row.get("open_price")), _num(row.get("prev_close"))
    f = {"align_days": row.get("align_days"), "new_high": bool(row.get("new_high")), "days_since_high": row.get("days_since_high"),
         "disp_pct": _num(row.get("disp_pct")), "rsi": _num(row.get("rsi")), "atr_pct": _num(row.get("atr_pct"))}
    news = clean_news(row.get("news"))
    parts = [f"종가 {_won(price)}"]
    c_open, c_prev = _chg(price, open_p), _chg(price, prev)
    parts.append(f"시가 대비 {_signed(c_open)}" if c_open is not None else "시가 대비 -")
    parts.append(f"전일 대비 {_signed(c_prev)}" if c_prev is not None else "전일 대비 -")
    if _num(row.get("score")) is not None:
        parts.append(f"차트 조건 점수 {_num(row['score']):.3f}")
    return {"name": row["stock_name"], "code": row["stock_code"], "price_line": " · ".join(parts), "headline": headline(f),
            "bits": chart_bits(f), "warn": chart_warn(f), "news": news,
            "note": news_note(len(news), int(row.get("news_negative_count") or 0)), "png": _decode_png(row.get("chart_png")),
            "rank": row.get("rank")}


# ─── 내 보유 종목 알람 ─────────────────────────────────────────────────────────────
def alarm_status(avg_price, close, pct):
    """(입력 평균 매수가 대비 변동률 %, 알람선 도달 여부). 값이 없으면 (None, False)."""
    avg, c = _num(avg_price), _num(close)
    if not avg or avg <= 0 or c is None:
        return None, False
    return (c / avg - 1) * 100, c <= avg * (1 - pct / 100)


def holding_card(holding, price, pct, report):
    """회원 한 명의 종목 한 칸. report 는 member_report.generate 결과(inner_html·inner_text 를 쓴다)."""
    chg, reached = alarm_status(holding.get("avg_price"), price.get("close"), pct)
    name = report.get("name") or holding.get("stock_name") or holding["stock_code"]
    if chg is None:
        line = f"알람선 {MINUS}{pct}% 비교 값이 없습니다"
    else:
        line = f"입력하신 평균 매수가 대비 {_signed(chg)} · 알람선 {MINUS}{pct}% " + ("도달" if reached else "도달 전")
    return {"name": name, "code": holding["stock_code"], "line": line, "reached": reached,
            "inner_html": report["inner_html"], "inner_text": report["inner_text"]}


# ─── 메일 조립 ────────────────────────────────────────────────────────────────────
_E = html.escape


def _h2(t):
    return f'<h2 style="font-size:16px;margin:26px 0 10px;color:#1a1a2e">{_E(t)}</h2>'


def _pick_html(v, cid):
    parts = [f'<div style="border:1px solid #e6e2d8;border-radius:10px;padding:16px 18px;margin:0 0 14px">',
             f'<p style="margin:0 0 2px;font-size:17px;font-weight:700;color:#1a1a2e">{_E(v["name"])} '
             f'<span style="font-size:12px;font-weight:400;color:#8a8a98">{_E(v["code"])}</span></p>',
             f'<p style="margin:0 0 10px;font-size:13px;color:#55616f">{_E(v["price_line"])}</p>',
             f'<p style="margin:0 0 12px;padding:8px 12px;background:#f6f4ef;border-left:3px solid #c9b58a;font-size:14px">{_E(v["headline"])}</p>']
    if cid:
        parts.append(f'<img src="cid:{cid}" width="520" alt="{_E(v["name"])} 일봉 차트" '
                     f'style="display:block;width:100%;max-width:520px;height:auto;margin:0 0 12px;border:1px solid #e6e2d8">')
    parts.append('<p style="margin:12px 0 4px;font-size:11px;color:#8a8a98">차트상 위치</p>'
                 '<ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.7">' + "".join(f"<li>{_E(b)}</li>" for b in v["bits"]) + "</ul>")
    parts.append('<p style="margin:12px 0 4px;font-size:11px;color:#8a8a98">최근 기사</p><ul style="margin:0;padding-left:18px;font-size:13.5px;line-height:1.6">')
    for a in v["news"]:
        t = _E(a["title"])
        link = f'<a href="{_E(a["link"])}" style="color:#4A6FA5">{t}</a>' if a["link"] else t
        parts.append(f"<li>{link}</li>")
    parts.append("</ul>")
    parts.append(f'<p style="margin:8px 0 0;font-size:12.5px;color:#8a8a98">{_E(v["note"])}</p>')
    if v["warn"]:
        parts.append(f'<p style="margin:10px 0 0;padding:8px 12px;background:#fbf3e2;border-left:3px solid #b0790f;font-size:13px">{_E(v["warn"])}</p>')
    parts.append("</div>")
    return "".join(parts)


def _pick_text(v):
    lines = [f"■ {v['name']} ({v['code']})", f"  {v['price_line']}", f"  {v['headline']}"]
    lines += [f"  - {b}" for b in v["bits"]]
    lines += [f"  · {a['title']} {a['link']}".rstrip() for a in v["news"]]
    lines.append(f"  {v['note']}")
    if v["warn"]:
        lines.append(f"  ※ {v['warn']}")
    return "\n".join(lines)


def compose(pick_date, picks, cards, pct, with_images):
    """메일 한 통. picks: pick_view 목록, cards: holding_card 목록.
    반환: {subject, html, text, images{cid:bytes}, check_text(금지 표현 검사 대상: 안내·면책 같은 고정 문구는 제외)}"""
    images, body, text, check = {}, [], [], []
    subject = f"[만조] {pick_date} 오늘의 차트 조건 검색 결과 {len(picks)}종목"
    body.append(f'<h1 style="font-size:19px;margin:0 0 4px;color:#1a1a2e">오늘의 차트 조건 검색 결과</h1>'
                f'<p style="margin:0 0 6px;font-size:13px;color:#6b6b80">{_E(pick_date)} 종가 기준</p>')
    text += [f"오늘의 차트 조건 검색 결과 — {pick_date} 종가 기준", ""]
    body.append(_h2(f"조건 충족 종목 {len(picks)}종목"))
    text.append(f"[조건 충족 종목 {len(picks)}종목]")
    for v in picks:
        cid = None
        if with_images and v["png"]:
            cid = f"pick{v['rank']}"
            images[cid] = v["png"]
        body.append(_pick_html(v, cid))
        text.append(_pick_text(v))
        check += [v["price_line"], v["headline"], *v["bits"], v["note"], v["warn"] or "", *[a["title"] for a in v["news"]]]
    body.append(f'<p style="margin:0 0 6px;font-size:12px;color:#8a8a98">{_E(RANK_NOTE)}</p>')
    text += ["", RANK_NOTE]

    if cards:
        body.append(_h2(f"내 보유 종목 알람 {len(cards)}종목"))
        text += ["", f"[내 보유 종목 알람 {len(cards)}종목]"]
        for c in cards:
            badge = ('<span style="margin-left:8px;padding:2px 8px;border-radius:10px;background:#fbf3e2;color:#8a5a00;font-size:12px">알람선 도달</span>'
                     if c["reached"] else "")
            body.append(f'<div style="border:1px solid #e6e2d8;border-radius:10px;padding:16px 18px;margin:0 0 14px">'
                        f'<p style="margin:0 0 2px;font-size:17px;font-weight:700;color:#1a1a2e">{_E(c["name"])} '
                        f'<span style="font-size:12px;font-weight:400;color:#8a8a98">{_E(c["code"])}</span>{badge}</p>'
                        f'<p style="margin:0 0 10px;font-size:13px;color:#55616f">{_E(c["line"])}</p>{c["inner_html"]}</div>')
            text += [f"■ {c['name']} ({c['code']})", f"  {c['line']}", c["inner_text"], ""]
            check += [c["inner_text"]]            # c["line"] 은 고정 틀에 숫자만 채운 문장이고 '평균 매수가'라는 이름을 담고 있어 검사 대상에서 뺀다(틀은 테스트가 검사)
        body.append(f'<p style="margin:0 0 6px;font-size:12px;color:#8a8a98;line-height:1.6">{_E(HOLDINGS_NOTE)}</p>')
        text += [HOLDINGS_NOTE]

    body.append('<hr style="border:none;border-top:1px solid #ddd;margin:24px 0 12px">'
                f'<p style="margin:0 0 4px;font-size:12px;color:#8a8a98;line-height:1.6">{_E(SUMMARY_DISCLAIMER)}</p>'
                f'<p style="margin:0 0 4px;font-size:12px;color:#8a8a98;line-height:1.6">{_E(UNSUBSCRIBE_NOTE)}</p>'
                f'<p style="margin:0;font-size:12px"><a href="{SITE_URL}/mypage.html" style="color:#4A6FA5">My page 열기</a></p>')
    text += ["", SUMMARY_DISCLAIMER, UNSUBSCRIBE_NOTE, f"My page: {SITE_URL}/mypage.html"]
    page = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"></head>'
            '<body style="margin:0;padding:20px;background:#f6f4ef;font-family:Malgun Gothic,Apple SD Gothic Neo,sans-serif">'
            '<div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;padding:24px;color:#1a1a2e">'
            + "".join(body) + "</div></body></html>")
    return {"subject": subject, "html": page, "text": "\n".join(text), "images": images, "check_text": "\n".join(check)}


# ─── 받는 사람 고르기(단계 제한) ──────────────────────────────────────────────────────
def select_recipients(candidates, env):
    """candidates: [(user_id, email)] (수신 동의한 회원). 단계 제한을 적용해 보낼 대상만 돌려준다.
    MEMBER_DAILY_RELEASE_ALL=yes → 전원. 아니면 MEMBER_DAILY_TEST_ONLY 의 주소만. 둘 다 없으면 아무도 없다."""
    if (env.get("MEMBER_DAILY_RELEASE_ALL") or "").strip().lower() == "yes":
        return list(candidates)
    allow = {a.strip().lower() for a in re.split(r"[,\s;]+", env.get("MEMBER_DAILY_TEST_ONLY") or "") if a.strip()}
    return [(u, e) for u, e in candidates if e and e.lower() in allow]


def _in(values):
    return "in.(" + ",".join(values) + ")"


def _short(ex):
    return mr._short_error(ex)


# ─── 실행 ─────────────────────────────────────────────────────────────────────────
def run(db, sender, make_report, today, now=None, env=None, admin_email=None, send=True, is_trading_day=None, log=print):
    """오늘 자 일일 메일을 만든다. send=False 면 발송·기록 없이 첫 대상 한 명의 메일을 돌려준다(미리보기).
    반환: {status, sent, error, skipped, composed, preview}  — 개수만 담는다."""
    env = env if env is not None else os.environ
    now = now or datetime.now(timezone.utc)
    out = {"status": "ok", "sent": 0, "error": 0, "skipped": 0, "composed": 0, "preview": None}
    if is_trading_day is None:
        import krx_calendar
        is_trading_day = krx_calendar.is_trading_day

    def alert(text):
        if send and admin_email:
            try:
                sender.send(admin_email, f"[만조] 일일 메일 점검 알림 ({today})", f"<p>{_E(text)}</p>", text)
            except Exception as ex:                      # 알림 실패가 본 작업을 막지 않는다
                log(f"[알림 실패] {type(ex).__name__}")

    if not is_trading_day(today):
        out["status"] = "not_trading_day"
        log("[발송 안 함] 오늘은 거래일이 아닙니다")
        return out
    rows = db.get("member_daily_picks", {"select": "*", "pick_date": f"eq.{today}", "order": "rank.asc"})
    if not rows:
        out["status"] = "no_data"
        log("[발송 안 함] 오늘 기준일 자료가 없습니다")
        alert(f"오늘({today}) 기준일의 조건 검색 자료가 없어 일일 메일을 보내지 않았습니다(PC 게시 실패일 수 있습니다).")
        return out
    picks = [pick_view(r) for r in rows]

    consent = db.get("member_mail_consent", {"select": "user_id", "digest_consent": "eq.true", "limit": "1000"})
    cands = []
    for c in consent:
        email = db.user_email(c["user_id"])
        cands.append((c["user_id"], email))
    recipients = select_recipients(cands, env)
    log(f"[대상] 동의 {len(consent)}명 중 발송 대상 {len(recipients)}명")
    if not recipients:
        out["status"] = "no_recipients"
        return out
    uids = [u for u, _ in recipients]

    already = set()
    if send:
        done = db.get("member_daily_mail_log", {"select": "user_id", "send_date": f"eq.{today}", "status": "eq.sent"})
        already = {d["user_id"] for d in done}
    access = {a["user_id"] for a in db.get("member_feature_access", {"select": "user_id,holdings_enabled,reports_enabled", "user_id": _in(uids)})
              if a.get("holdings_enabled") and a.get("reports_enabled")}
    prefs = {p["user_id"]: p["alert_drop_pct"] for p in db.get("member_mail_prefs", {"select": "user_id,alert_drop_pct", "user_id": _in(uids)})}
    holds = {}
    if access:
        for h in db.get("member_holdings", {"select": "user_id,stock_code,stock_name,avg_price,status,entry_date", "user_id": _in(sorted(access & set(uids))),
                                             "status": "in.(open,watch)", "order": "entry_date.desc", "limit": "1000"}):
            holds.setdefault(h["user_id"], []).append(h)
    codes = sorted({h["stock_code"] for hs in holds.values() for h in hs[:MAX_HOLDINGS]})
    prices = {}
    if codes:
        for p in db.get("member_prices", {"select": "stock_code,stock_name,market,close,ma5,ma20,ma60,ma120,high_52w,low_52w,trade_date",
                                          "stock_code": _in(codes)}):
            prices[p["stock_code"]] = p
    reports = {}                                          # 종목별로 하루에 한 번만 만든다

    def report_for(code):
        if code not in reports:
            try:
                reports[code] = make_report(code, prices[code])
            except Exception as ex:
                log(f"[보유 종목 리포트 실패] {type(ex).__name__}")
                reports[code] = None
        return reports[code]

    log_rows = []
    for uid, email in recipients:
        if uid in already:
            out["skipped"] += 1
            continue
        try:
            if not email:
                raise RuntimeError("수신 주소를 찾지 못했습니다")
            pct = int(prefs.get(uid) or DEFAULT_ALERT_PCT)
            cards = []
            for h in holds.get(uid, [])[:MAX_HOLDINGS]:
                if h["stock_code"] not in prices:
                    continue
                rep = report_for(h["stock_code"])
                if rep:
                    cards.append(holding_card(h, prices[h["stock_code"]], pct, rep))
            mail = compose(today, picks, cards, pct, with_images=bool(getattr(sender, "supports_inline_images", False)))
            out["composed"] += 1
            bad = find_forbidden(mail["check_text"])
            if bad:
                raise RuntimeError("금지 표현 검사 불통과")
            if not send:
                out["preview"] = mail
                return out
            sender.send(email, mail["subject"], mail["html"], mail["text"], mail["images"] or None)
            log_rows.append({"user_id": uid, "send_date": today, "status": "sent", "holdings_count": len(cards),
                             "sent_at": now.isoformat(), "error_message": None})
            out["sent"] += 1
        except Exception as ex:
            log(f"[발송 실패] {type(ex).__name__}")
            out["error"] += 1
            if send:
                log_rows.append({"user_id": uid, "send_date": today, "status": "error", "holdings_count": 0,
                                 "sent_at": None, "error_message": _short(ex)})
    if send and log_rows:
        db.upsert("member_daily_mail_log", log_rows, "user_id,send_date")
    if out["error"]:
        alert(f"일일 메일 발송 {out['sent']}건 성공, {out['error']}건 실패({today}). 실패 사유는 member_daily_mail_log 에서 확인하세요.")
    return out


# ─── 실행 명령 ────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="회원 일일 메일(조건 검색 결과 + 보유 종목 알람)")
    ap.add_argument("--send", action="store_true", help="실제 발송(승인 장치와 단계 제한을 통과해야 함)")
    ap.add_argument("--preview", action="store_true", help="첫 대상 회원의 메일을 파일로만 만든다(발송·기록 없음)")
    ap.add_argument("--out", default=None, help="미리보기를 파일로 저장할 폴더(생략하면 저장하지 않고 요약만 출력). "
                                               "이 저장소는 공개라서 GitHub Actions 에서는 쓰지 말 것 - 회원의 보유 종목이 담길 수 있다")
    ap.add_argument("--date", help="기준일 YYYY-MM-DD(시험용). 기본은 오늘(KST)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not (a.send or a.preview):
        ap.error("--send 또는 --preview 중 하나가 필요합니다")
    mr._load_env()
    today = a.date or datetime.now(KST).strftime("%Y-%m-%d")
    db = mr.Db()

    if a.send:
        ok, reason = mm.send_gate(os.environ)
        if not ok:
            print(f"[발송 안 함] 승인 장치 미통과: {reason}")
            return 2
        sender = mm.build_sender(os.environ)
        print(f"[발송 수단] {mm.send_provider(os.environ)}")
    else:
        sender = mr.FakeSender()

    import collect_gainers as cg                          # 기사·수급·Groq 호출 함수 재사용
    ai_call = None
    if os.environ.get("GROQ_API_KEY"):
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        ai_call = lambda prompt: cg.call_groq_with_retry(client, prompt)
    base_news = mr.make_news_fn(cg)

    def news_fn(code, trade_date):
        return [x for x in (base_news(code, trade_date) or []) if not find_forbidden(x.get("title", ""))]

    def investors_fn(c, close):
        v = cg.fetch_investor_netbuy(c, close)
        return v if any(v.values()) else None

    def make_report(code, price):
        return mr.generate(code, price, news_fn, investors_fn, ai_call)

    out = run(db, sender, make_report, today, env=os.environ, admin_email=(os.environ.get("ADMIN_ALERT_EMAIL") or "").strip() or None,
              send=a.send)
    if a.preview:
        mail = out.get("preview")
        if not mail:
            print(f"[미리보기 없음] 상태: {out['status']}")
            return 1
        print(f"제목: {mail['subject']}\n차트 {len(mail['images'])}장, 본문 {len(mail['text'])}자, 금지 표현 검사 통과")
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            stem = os.path.join(a.out, f"member-daily-{today}")
            html_out = mail["html"]
            for cid, data in mail["images"].items():
                html_out = html_out.replace(f"cid:{cid}", "data:image/png;base64," + base64.b64encode(data).decode())
            with open(stem + ".html", "w", encoding="utf-8") as f:
                f.write(html_out)
            with open(stem + ".txt", "w", encoding="utf-8") as f:
                f.write(mail["text"])
            print(f"저장: {stem}.html / .txt")
        return 0
    print("[처리 결과] " + ", ".join(f"{k} {v}" for k, v in out.items() if k != "preview"))
    return 1 if out["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
