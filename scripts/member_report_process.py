# -*- coding: utf-8 -*-
"""
회원 종목 리포트 — 요청 처리 실행 명령 (P4)

  python scripts/member_report_process.py            # 점검 모드(기본): 대기 요청·한도 초과 기록 개수만 읽어 보여준다. DB 수정·메일 발송 없음.
  python scripts/member_report_process.py --send     # 실제 처리: 대기 요청의 리포트를 만들어 회원에게 발송하고, 한도 초과 알림을 회장님께 발송한다.
                                                     # 발송 승인 장치(member_report_mail.send_gate)를 통과하지 못하면 아무것도 하지 않고 종료 코드 2.

필요 환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (모든 모드)
  --send 에서 추가: GROQ_API_KEY(없으면 AI 설명 없는 기본형), MEMBER_REPORT_SEND_APPROVED=yes,
                   발송 수단: GMAIL_ADDRESS + GMAIL_APP_PASSWORD (또는 RESEND_API_KEY + MAIL_FROM),
                   ADMIN_ALERT_EMAIL(없으면 한도 초과 알림만 생략)
이 스크립트는 로그에 회원 이메일·종목코드를 남기지 않는다(개수만 출력).
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import member_report as mr  # noqa: E402
import member_report_mail as mm  # noqa: E402

KST = timezone(timedelta(hours=9))
STALE_MINUTES = 30            # 이보다 오래 대기 중이면 '막혀 있음'으로 본다(설계서 §5, §9)


def inspect(db, now=None):
    """점검(읽기만). 반환: {pending, stale_pending, oldest_pending_min, unalerted_limit_hits}"""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(KST).date()
    pending = db.get("member_report_requests", {"select": "id,requested_at", "status": "eq.pending", "order": "requested_at.asc", "limit": "200"})
    oldest = None
    if pending:
        first = datetime.fromisoformat(str(pending[0]["requested_at"]).replace("Z", "+00:00"))
        oldest = int((now - first).total_seconds() // 60)
    hits = db.get("member_report_limit_hits", {"select": "user_id,hit_date,attempts,alerted_at,alerted_attempts,last_attempt_at",
                                                "hit_date": f"gte.{today - timedelta(days=3)}", "limit": "100"})
    return {"pending": len(pending), "oldest_pending_min": oldest,
            "stale_pending": 0 if oldest is None else int(oldest >= STALE_MINUTES),
            "unalerted_limit_hits": len(mm.pick_alert_rows(hits, today))}


def process_all(db, sender, make_report, admin_email, now=None, log=print):
    """실제 처리 한 번: 대기 요청 처리 + 한도 초과 알림. 한쪽이 실패해도 다른 쪽은 진행한다."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(KST).date()
    out = {"claimed": 0, "sent": 0, "error": 0, "alerted": 0, "alert_error": 0}
    out.update(mr.process_queue(db, sender, make_report, now=now, log=log))
    if admin_email:
        try:
            out["alerted"] = mm.process_limit_alerts(db, sender, admin_email, today, now, log=log)
        except Exception as ex:           # 알림 실패는 요청 처리와 무관하다. 표시하지 않았으므로 다음 실행 때 다시 시도된다.
            out["alert_error"] = 1
            log(f"[알림 실패] {type(ex).__name__}")
    else:
        log("[안내] ADMIN_ALERT_EMAIL 이 없어 한도 초과 알림은 건너뜁니다.")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="회원 종목 리포트 요청 처리 (기본: 점검만)")
    ap.add_argument("--send", action="store_true", help="실제로 리포트·알림 메일을 발송(승인 장치 필요)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    mr._load_env()
    db = mr.Db()

    if not a.send:
        info = inspect(db)
        print("[점검 모드] DB 수정·메일 발송 없음")
        print(f"대기 요청 {info['pending']}건 (가장 오래된 {info['oldest_pending_min']}분 전), 막힘 경고 {info['stale_pending']}, "
              f"알림 대기 중인 한도 초과 {info['unalerted_limit_hits']}명")
        return 0

    ok, reason = mm.send_gate(os.environ)
    if not ok:
        print(f"[발송 안 함] 승인 장치 미통과: {reason}")
        return 2

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import collect_gainers as cg   # 뉴스·수급·Groq 호출 함수 재사용
    ai_call = None
    if os.environ.get("GROQ_API_KEY"):
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        ai_call = lambda prompt: cg.call_groq_with_retry(client, prompt)
    else:
        print("[안내] GROQ_API_KEY 가 없어 AI 설명 없는 기본형으로 만듭니다.")

    def investors_fn(c, close):
        v = cg.fetch_investor_netbuy(c, close)
        return v if any(v.values()) else None

    maker = mr.ReportMaker(db, mr.make_news_fn(cg), investors_fn, ai_call)
    sender = mm.build_sender(os.environ)          # Gmail(앱 비밀번호) 또는 Resend. 승인 장치를 이미 통과한 환경이다.
    print(f"[발송 수단] {mm.send_provider(os.environ)}")
    out = process_all(db, sender, maker, (os.environ.get("ADMIN_ALERT_EMAIL") or "").strip() or None)
    print("[처리 결과] " + ", ".join(f"{k} {v}" for k, v in out.items()))
    return 1 if (out["error"] or out["alert_error"]) else 0


if __name__ == "__main__":
    sys.exit(main())
