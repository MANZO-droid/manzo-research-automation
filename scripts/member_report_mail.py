# -*- coding: utf-8 -*-
"""
회원 종목 리포트 — 메일 발송기·한도 초과 알림·발송 승인 장치 (P4)

설계서: manzo-site 저장소 docs/superpowers/specs/2026-10-25-member-report-addendum.md (§7, §7-2)

  - ResendSender : Resend(메일 API)로 한 통을 보낸다. 실패 사유에는 메일 주소·본문을 넣지 않는다.
  - 한도 초과 알림: 회원이 하루 한도(3건)를 넘겨 요청을 시도하면 회장님께 요약 메일 1통(회원 닉네임·횟수·시각만).
                    종목코드·이메일·보유 정보는 담지 않는다(회장님은 회원 개인의 보유를 보지 않는다는 원칙).
  - GmailSmtpSender : Gmail(앱 비밀번호)로 보낸다. 도메인이 필요 없고 무료다(하루 500통, Google 공식 안내). 2026-10-31 회장님 선택.
  - 발송 승인 장치: 이 저장소 규칙(CLAUDE.md 8번)상 외부 발송은 직전 명시적 승인이 필요하다. 아래가 모두 갖춰질 때만 실제 발송한다.
        MEMBER_REPORT_SEND_APPROVED=yes (회장님이 저장소 변수로 직접 등록)
        + 발송 수단 하나: GMAIL_ADDRESS + GMAIL_APP_PASSWORD   또는   RESEND_API_KEY + MAIL_FROM
    하나라도 모자라면 발송하지 않는다. 둘 다 있으면 Gmail을 쓴다.
"""
import html
import re
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr

RESEND_URL = "https://api.resend.com/emails"
DAILY_LIMIT = 3                      # 사이트 DB 함수 member_request_report 의 하루 한도와 같은 값
_EMAIL_LIKE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SINGLE_ADDR = re.compile(r"^[^\s@,;<>\"']+@[^\s@,;<>\"']+\.[^\s@,;<>\"']+$")


# ─── 발송 승인 장치 ───────────────────────────────────────────────────────────────
def _has(env, key):
    return bool((env.get(key) or "").strip())


def send_provider(env):
    """갖춰진 발송 수단 이름('gmail' | 'resend') 또는 None. 둘 다 있으면 Gmail."""
    if _has(env, "GMAIL_ADDRESS") and _has(env, "GMAIL_APP_PASSWORD"):
        return "gmail"
    if _has(env, "RESEND_API_KEY") and _has(env, "MAIL_FROM"):
        return "resend"
    return None


def send_gate(env):
    """(허용 여부, 사유). 승인 변수와 발송 수단이 모두 있을 때만 True."""
    if (env.get("MEMBER_REPORT_SEND_APPROVED") or "").strip().lower() != "yes":
        return False, "발송 승인 변수(MEMBER_REPORT_SEND_APPROVED=yes)가 없습니다"
    if send_provider(env) is None:
        return False, "발송 수단이 없습니다(GMAIL_ADDRESS+GMAIL_APP_PASSWORD 또는 RESEND_API_KEY+MAIL_FROM)"
    return True, "승인됨"


def build_sender(env):
    """승인 장치를 통과한 환경에서 발송기를 만든다. 통과하지 못하면 예외."""
    ok, why = send_gate(env)
    if not ok:
        raise RuntimeError(why)
    if send_provider(env) == "gmail":
        return GmailSmtpSender(env["GMAIL_ADDRESS"].strip(), env["GMAIL_APP_PASSWORD"], display_name=(env.get("MAIL_FROM_NAME") or "").strip() or "만조Investment")
    return ResendSender(env["RESEND_API_KEY"], env["MAIL_FROM"])


# ─── Gmail 발송기 ─────────────────────────────────────────────────────────────────
class GmailSmtpSender:
    """Gmail SMTP(앱 비밀번호)로 한 통을 보낸다. Gmail은 보낸 사람을 로그인한 계정 주소로 고정한다.
    실패 사유에는 수신 주소·본문·비밀번호를 넣지 않고, 어떤 종류의 실패인지만 남긴다."""

    def __init__(self, address, app_password, display_name="만조Investment", host="smtp.gmail.com", port=465, smtp_factory=None, timeout=30):
        if not address or not app_password:
            raise ValueError("address 와 app_password 가 필요합니다")
        # 구글이 보여 주는 앱 비밀번호는 4칸씩 띄어쓰기로 표시되므로 공백을 지운다
        self.address, self.password = address, re.sub(r"\s+", "", app_password)
        self.display_name, self.host, self.port, self.timeout = display_name, host, port, timeout
        self._factory = smtp_factory or (lambda h, p, t: smtplib.SMTP_SSL(h, p, timeout=t))

    def _message(self, to, subject, html_body, text_body):
        if not _SINGLE_ADDR.match(to or ""):
            raise RuntimeError("수신 주소 형식 오류")
        msg = EmailMessage()
        msg["From"] = formataddr((self.display_name, self.address))
        msg["To"] = to
        msg["Subject"] = re.sub(r"[\r\n]+", " ", subject or "")      # 헤더 줄바꿈 삽입 방지
        msg.set_content(text_body or "")
        msg.add_alternative(html_body or "", subtype="html")
        return msg

    def send(self, to, subject, html_body, text_body):
        msg = self._message(to, subject, html_body, text_body)
        try:
            smtp = self._factory(self.host, self.port, self.timeout)
            try:
                smtp.login(self.address, self.password)
                smtp.send_message(msg)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
        except smtplib.SMTPAuthenticationError:
            raise RuntimeError("Gmail 로그인 실패(앱 비밀번호·2단계 인증을 확인하세요)") from None
        except smtplib.SMTPRecipientsRefused:
            raise RuntimeError("Gmail 수신 주소 거부") from None
        except smtplib.SMTPException as ex:
            raise RuntimeError(f"Gmail SMTP 오류 ({type(ex).__name__})") from None
        except OSError:
            raise RuntimeError("Gmail 접속 실패") from None


# ─── Resend 발송기 ────────────────────────────────────────────────────────────────
class ResendSender:
    def __init__(self, api_key, mail_from, session=None, timeout=30):
        if not api_key or not mail_from:
            raise ValueError("api_key 와 mail_from 이 필요합니다")
        self.api_key, self.mail_from, self.timeout = api_key, mail_from, timeout
        if session is None:
            import requests
            session = requests.Session()
        self.s = session

    def send(self, to, subject, html_body, text_body):
        r = self.s.post(RESEND_URL, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json={"from": self.mail_from, "to": [to], "subject": subject, "html": html_body, "text": text_body},
                        timeout=self.timeout)
        if not r.ok:
            # 응답 본문에는 수신 주소가 들어 있을 수 있어 상태 코드만 남긴다
            raise RuntimeError(f"Resend 발송 실패 (HTTP {r.status_code})")


# ─── 한도 초과 알림 ───────────────────────────────────────────────────────────────
def _kst_hm(iso, tz_offset_hours=9):
    from datetime import datetime, timezone
    d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return d.astimezone(timezone(timedelta(hours=tz_offset_hours))).strftime("%H:%M")


def pick_alert_rows(rows, today):
    """알림을 보낼 한도 초과 기록. 같은 회원·같은 날에는 하루 1통만 보낸다.
      - 아직 알림 안 보낸 기록(alerted_at 없음)
      - 이미 알린 기록 중, 날짜가 지난 뒤 시도가 더 늘어난 것(추가 시도는 다음 날 요약에 합산)"""
    out = []
    for r in rows:
        new = int(r["attempts"]) - int(r.get("alerted_attempts") or 0)
        if new <= 0:
            continue
        if r.get("alerted_at") is None or str(r["hit_date"]) < str(today):
            out.append(dict(r, new_attempts=new))
    return out


def build_alert(entries, today):
    """entries: [{nickname, new_attempts, hit_date, last_attempt_at, followup}] → (제목, html, text)"""
    subject = f"[만조] 리포트 한도 초과 시도 {len(entries)}명 ({today})"
    lines_text, lines_html = [], []
    for e in entries:
        tail = " (이미 알려 드린 날의 추가 시도)" if e.get("followup") else ""
        s = (f"닉네임 {e['nickname']}님이 {e['hit_date']} 리포트 하루 한도({DAILY_LIMIT}건)를 넘어 "
             f"{e['new_attempts']}회 더 요청했습니다. 마지막 시도 {_kst_hm(e['last_attempt_at'])}{tail}")
        lines_text.append("- " + s)
        lines_html.append(f'<li style="margin:0 0 8px;line-height:1.6">{html.escape(s)}</li>')
    note = "조치가 필요하면 관리자 회원 목록에서 닉네임으로 찾아 '리포트 허용'을 끄시면 됩니다. 종목·이메일·보유 정보는 이 알림에 담지 않습니다."
    text = "\n".join([f"리포트 하루 한도 초과 시도 ({today})", ""] + lines_text + ["", note])
    body = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body style="font-family:Malgun Gothic,sans-serif;padding:16px">'
            f'<h2 style="font-size:16px">리포트 하루 한도 초과 시도 ({html.escape(str(today))})</h2><ul style="padding-left:18px;font-size:14px">'
            + "".join(lines_html) + f'</ul><p style="font-size:12px;color:#777">{html.escape(note)}</p></body></html>')
    return subject, body, text


def process_limit_alerts(db, sender, admin_email, today, now, log=print):
    """한도 초과 기록을 모아 회장님께 요약 메일 한 통을 보내고, 보낸 기록에 표시한다. 반환: 알린 회원 수.
    발송이 실패하면 예외를 그대로 올린다(표시하지 않으므로 다음 실행 때 다시 시도된다)."""
    rows = db.get("member_report_limit_hits", {"select": "user_id,hit_date,attempts,last_attempt_at,alerted_at,alerted_attempts",
                                                "hit_date": f"gte.{today - timedelta(days=3)}", "order": "last_attempt_at.asc", "limit": "100"})
    picked = pick_alert_rows(rows, today)
    if not picked:
        return 0
    entries = []
    for r in picked:
        entries.append({"nickname": db.user_nickname(r["user_id"]) or "(이름 없음)", "new_attempts": r["new_attempts"], "hit_date": r["hit_date"],
                        "last_attempt_at": r["last_attempt_at"], "followup": r.get("alerted_at") is not None})
    subject, html_body, text_body = build_alert(entries, today)
    sender.send(admin_email, subject, html_body, text_body)
    for r in picked:
        db.patch("member_report_limit_hits", {"user_id": f"eq.{r['user_id']}", "hit_date": f"eq.{r['hit_date']}"},
                 {"alerted_at": now.isoformat(), "alerted_attempts": r["attempts"]})
    log(f"[알림] 한도 초과 {len(picked)}명 요약 메일 발송")
    return len(picked)
