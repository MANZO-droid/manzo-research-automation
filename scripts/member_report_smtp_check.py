# -*- coding: utf-8 -*-
"""
Gmail 발송 점검 — 로그인까지만 시도한다. 메일은 보내지 않고 DB도 건드리지 않는다.

465번(SSL)과 587번(STARTTLS) 두 방식을 차례로 시도해, 어느 단계(접속·인사·로그인)에서 끊기는지 단계별로 출력한다.
출력에는 앱 비밀번호·전체 주소를 넣지 않는다(주소는 앞 2글자와 도메인만).
필요 환경변수: GMAIL_ADDRESS, GMAIL_APP_PASSWORD
"""
import os
import re
import smtplib
import sys

HOST = "smtp.gmail.com"


def _mask(addr):
    local, _, domain = addr.partition("@")
    return f"{local[:2]}***@{domain}"


def _try(label, connect):
    """connect() -> 로그인까지 마친 smtp 객체. 실패한 단계를 한 줄로 출력한다. 반환: 성공 여부."""
    stage = "접속"
    smtp = None
    try:
        smtp = connect()
        stage = "로그인"
        smtp.login(os.environ["_ADDR"], os.environ["_PW"])
        print(f"[{label}] 성공: 접속·로그인 모두 통과")
        return True
    except smtplib.SMTPAuthenticationError as ex:
        print(f"[{label}] 실패({stage}): 로그인 거부 — 코드 {ex.smtp_code} (주소·앱 비밀번호·2단계 인증 확인 필요)")
    except smtplib.SMTPServerDisconnected:
        print(f"[{label}] 실패({stage}): 서버가 연결을 끊음")
    except smtplib.SMTPException as ex:
        print(f"[{label}] 실패({stage}): SMTP 오류 {type(ex).__name__}")
    except OSError as ex:
        print(f"[{label}] 실패({stage}): 네트워크 오류 {type(ex).__name__}")
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
    return False


def main():
    addr = (os.environ.get("GMAIL_ADDRESS") or "").strip()
    pw = re.sub(r"\s+", "", os.environ.get("GMAIL_APP_PASSWORD") or "")
    if not addr or not pw:
        print("[점검 불가] GMAIL_ADDRESS 또는 GMAIL_APP_PASSWORD 가 비어 있습니다")
        return 2
    os.environ["_ADDR"], os.environ["_PW"] = addr, pw
    print(f"[대상] 주소 {_mask(addr)} / 앱 비밀번호 길이 {len(pw)}자(정상은 16자)")

    def ssl465():
        return smtplib.SMTP_SSL(HOST, 465, timeout=30)

    def starttls587():
        s = smtplib.SMTP(HOST, 587, timeout=30)
        s.ehlo()
        s.starttls()
        s.ehlo()
        return s

    r1 = _try("465 SSL", ssl465)
    r2 = _try("587 STARTTLS", starttls587)
    return 0 if (r1 or r2) else 1


if __name__ == "__main__":
    sys.exit(main())
