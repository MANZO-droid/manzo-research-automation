# -*- coding: utf-8 -*-
"""scripts/member_report_mail.py, member_report_process.py 테스트. 실행:  python -m unittest tests.test_member_report_mail -v
네트워크·Resend·Supabase 없이 가짜(Fake)로만 돈다. 실제 메일은 절대 나가지 않는다."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import member_report as mr  # noqa: E402
import member_report_mail as mm  # noqa: E402
import member_report_process as mp  # noqa: E402

NOW = datetime(2026, 10, 29, 13, 15, tzinfo=timezone.utc)      # KST 22:15
TODAY = date(2026, 10, 29)


class FakeDb:
    def __init__(self, requests=None, hits=None, emails=None, nicknames=None):
        self.t = {"member_report_requests": requests or [], "member_report_limit_hits": hits or []}
        self.emails, self.nicknames, self.patches = emails or {}, nicknames or {}, []

    @staticmethod
    def _match(row, params):
        for k, v in params.items():
            if k in ("select", "limit", "order"):
                continue
            if v.startswith("eq.") and str(row.get(k)) != v[3:]:
                return False
            if v.startswith("gte.") and str(row.get(k)) < v[4:]:
                return False
        return True

    def get(self, table, params):
        rows = [r for r in self.t[table] if self._match(r, params)]
        return [dict(r) for r in rows[:int(params.get("limit", 1000))]]

    def patch(self, table, params, body):
        changed = []
        for r in self.t[table]:
            if self._match(r, params):
                r.update(body)
                changed.append(dict(r))
        self.patches.append((table, params, body))
        return changed

    def user_email(self, uid):
        return self.emails.get(uid)

    def user_nickname(self, uid):
        return self.nicknames.get(uid)


class Capture:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, to, subject, html_body, text_body):
        if self.fail:
            raise RuntimeError("Resend 발송 실패 (HTTP 500)")
        self.sent.append({"to": to, "subject": subject, "html": html_body, "text": text_body})


def hit(uid, d="2026-10-29", attempts=2, alerted_at=None, alerted_attempts=0, last="2026-10-29T13:15:00+00:00"):
    return {"user_id": uid, "hit_date": d, "attempts": attempts, "alerted_at": alerted_at, "alerted_attempts": alerted_attempts, "last_attempt_at": last}


class SendGateTests(unittest.TestCase):
    FULL = {"MEMBER_REPORT_SEND_APPROVED": "yes", "RESEND_API_KEY": "k", "MAIL_FROM": "a@b.c"}

    def test_all_three_required(self):
        self.assertTrue(mm.send_gate(self.FULL)[0])
        for missing in self.FULL:
            env = {k: v for k, v in self.FULL.items() if k != missing}
            ok, why = mm.send_gate(env)
            self.assertFalse(ok, missing + " 이 없으면 막아야 함")
            self.assertTrue(why)

    def test_approval_value_must_be_yes(self):
        for bad in ["", "no", "true", "1", "YESS", None]:
            env = dict(self.FULL, MEMBER_REPORT_SEND_APPROVED=bad)
            self.assertFalse(mm.send_gate(env)[0], repr(bad))
        self.assertTrue(mm.send_gate(dict(self.FULL, MEMBER_REPORT_SEND_APPROVED=" YES "))[0])


class ResendSenderTests(unittest.TestCase):
    class Resp:
        def __init__(self, ok, status=200):
            self.ok, self.status_code = ok, status
            self.text = "to bob@example.com failed"

    class Session:
        def __init__(self, ok=True, status=200):
            self.calls, self._ok, self._st = [], ok, status

        def post(self, url, headers, json, timeout):
            self.calls.append((url, headers, json, timeout))
            return ResendSenderTests.Resp(self._ok, self._st)

    def test_request_shape(self):
        s = self.Session()
        mm.ResendSender("KEY123", "만조 <report@x.test>", session=s).send("bob@example.com", "제목", "<p>h</p>", "t")
        url, headers, body, _ = s.calls[0]
        self.assertEqual(url, "https://api.resend.com/emails")
        self.assertEqual(headers["Authorization"], "Bearer KEY123")
        self.assertEqual(body, {"from": "만조 <report@x.test>", "to": ["bob@example.com"], "subject": "제목", "html": "<p>h</p>", "text": "t"})

    def test_failure_message_has_status_only(self):
        with self.assertRaises(RuntimeError) as cm:
            mm.ResendSender("K", "f@x.y", session=self.Session(ok=False, status=422)).send("bob@example.com", "s", "h", "t")
        self.assertIn("422", str(cm.exception))
        self.assertNotIn("bob@example.com", str(cm.exception))

    def test_requires_key_and_from(self):
        with self.assertRaises(ValueError):
            mm.ResendSender("", "f@x.y", session=self.Session())
        with self.assertRaises(ValueError):
            mm.ResendSender("K", "", session=self.Session())


class AlertSelectionTests(unittest.TestCase):
    def test_selection_rules(self):
        rows = [hit("new"),                                                         # 아직 알리지 않음 → 선택
                hit("same_day_more", attempts=5, alerted_at="2026-10-29T10:00:00Z", alerted_attempts=2),   # 오늘 이미 알림 → 하루 1통이라 제외
                hit("yesterday_more", d="2026-10-28", attempts=5, alerted_at="2026-10-28T10:00:00Z", alerted_attempts=2),  # 어제 알린 뒤 늘어남 → 합산
                hit("done", d="2026-10-28", attempts=2, alerted_at="2026-10-28T10:00:00Z", alerted_attempts=2)]            # 변화 없음 → 제외
        got = {r["user_id"]: r["new_attempts"] for r in mm.pick_alert_rows(rows, TODAY)}
        self.assertEqual(got, {"new": 2, "yesterday_more": 3})


class BuildAlertTests(unittest.TestCase):
    def test_content_has_nickname_count_time_and_no_private_info(self):
        entries = [{"nickname": "<b>짱구</b>", "new_attempts": 3, "hit_date": "2026-10-29", "last_attempt_at": "2026-10-29T13:15:00+00:00", "followup": False},
                   {"nickname": "철수", "new_attempts": 2, "hit_date": "2026-10-28", "last_attempt_at": "2026-10-28T01:05:00Z", "followup": True}]
        subject, html_body, text = mm.build_alert(entries, TODAY)
        self.assertIn("2명", subject)
        self.assertIn("닉네임 <b>짱구</b>님이 2026-10-29 리포트 하루 한도(3건)를 넘어 3회 더 요청했습니다. 마지막 시도 22:15", text)   # KST 변환
        self.assertIn("마지막 시도 10:05", text)
        self.assertIn("추가 시도", text)
        self.assertNotIn("<b>짱구</b>", html_body)       # HTML 에서는 이스케이프
        self.assertIn("&lt;b&gt;", html_body)
        for never in ["@", "005930", "종목코드:", "매수가"]:
            self.assertNotIn(never, text)


class ProcessLimitAlertsTests(unittest.TestCase):
    def _db(self):
        return FakeDb(hits=[hit("u1", attempts=2), hit("u2", attempts=1, last="2026-10-29T12:00:00+00:00")], nicknames={"u1": "짱구", "u2": "철수"})

    def test_one_digest_for_all_users_then_silent(self):
        db, mail = self._db(), Capture()
        self.assertEqual(mm.process_limit_alerts(db, mail, "admin@example.com", TODAY, NOW, log=lambda *_: None), 2)
        self.assertEqual(len(mail.sent), 1, "여러 회원이어도 요약 메일은 한 통")
        self.assertEqual(mail.sent[0]["to"], "admin@example.com")
        self.assertIn("짱구", mail.sent[0]["text"])
        self.assertIn("철수", mail.sent[0]["text"])
        rows = {r["user_id"]: r for r in db.t["member_report_limit_hits"]}
        self.assertEqual((rows["u1"]["alerted_attempts"], rows["u2"]["alerted_attempts"]), (2, 1))
        self.assertIsNotNone(rows["u1"]["alerted_at"])
        # 다시 실행해도 보내지 않는다
        self.assertEqual(mm.process_limit_alerts(db, mail, "admin@example.com", TODAY, NOW, log=lambda *_: None), 0)
        self.assertEqual(len(mail.sent), 1)
        # 같은 날 시도가 더 늘어도(하루 1통) 보내지 않는다
        rows["u1"]["attempts"] = 6
        self.assertEqual(mm.process_limit_alerts(db, mail, "admin@example.com", TODAY, NOW, log=lambda *_: None), 0)
        # 다음 날에는 추가 시도가 합산되어 알려진다
        self.assertEqual(mm.process_limit_alerts(db, mail, "admin@example.com", date(2026, 10, 30), NOW, log=lambda *_: None), 1)
        self.assertIn("4회 더", mail.sent[1]["text"])

    def test_send_failure_keeps_rows_unalerted_for_retry(self):
        db = self._db()
        with self.assertRaises(RuntimeError):
            mm.process_limit_alerts(db, Capture(fail=True), "admin@example.com", TODAY, NOW, log=lambda *_: None)
        self.assertTrue(all(r["alerted_at"] is None for r in db.t["member_report_limit_hits"]))
        self.assertEqual(mm.process_limit_alerts(db, Capture(), "admin@example.com", TODAY, NOW, log=lambda *_: None), 2)

    def test_missing_nickname_never_falls_back_to_email(self):
        db = FakeDb(hits=[hit("u9")], emails={"u9": "secret@example.com"})
        mail = Capture()
        mm.process_limit_alerts(db, mail, "admin@example.com", TODAY, NOW, log=lambda *_: None)
        self.assertIn("(이름 없음)", mail.sent[0]["text"])
        self.assertNotIn("secret@example.com", mail.sent[0]["text"] + mail.sent[0]["html"])


class ProcessAllAndInspectTests(unittest.TestCase):
    def _db(self):
        reqs = [{"id": "r1", "user_id": "u1", "stock_code": "005930", "status": "pending", "requested_at": "2026-10-29T12:30:00+00:00"},
                {"id": "r2", "user_id": "u2", "stock_code": "005930", "status": "pending", "requested_at": "2026-10-29T13:10:00+00:00"}]
        return FakeDb(requests=reqs, hits=[hit("u1")], emails={"u1": "a@example.com", "u2": "b@example.com"}, nicknames={"u1": "짱구"})

    @staticmethod
    def _make(code):
        return {"subject": "s", "html": "h", "text": "t"}

    def test_inspect_is_read_only_and_counts(self):
        db = self._db()
        info = mp.inspect(db, now=NOW)
        self.assertEqual(info["pending"], 2)
        self.assertEqual(info["oldest_pending_min"], 45)
        self.assertEqual(info["stale_pending"], 1)
        self.assertEqual(info["unalerted_limit_hits"], 1)
        self.assertEqual(db.patches, [], "점검 모드는 DB를 고치지 않는다")

    def test_process_all_sends_reports_and_alert(self):
        db, mail = self._db(), Capture()
        out = mp.process_all(db, mail, self._make, "admin@example.com", now=NOW, log=lambda *_: None)
        self.assertEqual((out["sent"], out["error"], out["alerted"], out["alert_error"]), (2, 0, 1, 0))
        self.assertEqual(sorted(m["to"] for m in mail.sent), ["a@example.com", "admin@example.com", "b@example.com"])

    def test_alert_failure_does_not_break_report_delivery(self):
        db = self._db()
        class FailAdmin(Capture):
            def send(s, to, subject, html_body, text_body):
                if to == "admin@example.com":
                    raise RuntimeError("Resend 발송 실패 (HTTP 500)")
                super().send(to, subject, html_body, text_body)
        mail = FailAdmin()
        out = mp.process_all(db, mail, self._make, "admin@example.com", now=NOW, log=lambda *_: None)
        self.assertEqual((out["sent"], out["alert_error"], out["alerted"]), (2, 1, 0))
        self.assertIsNone(db.t["member_report_limit_hits"][0]["alerted_at"])

    def test_no_admin_email_skips_alert_only(self):
        db, mail = self._db(), Capture()
        out = mp.process_all(db, mail, self._make, None, now=NOW, log=lambda *_: None)
        self.assertEqual((out["sent"], out["alerted"]), (2, 0))
        self.assertEqual(len(mail.sent), 2)


class MainGateTests(unittest.TestCase):
    ENV = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "svc"}

    def _run(self, argv, env):
        buf = io.StringIO()
        db = FakeDb(requests=[{"id": "r1", "user_id": "u1", "stock_code": "005930", "status": "pending", "requested_at": "2026-10-29T12:30:00+00:00"}],
                    emails={"u1": "a@example.com"})
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(mr, "Db", lambda: db), mock.patch.object(mr, "_load_env", lambda: None), redirect_stdout(buf):
            code = mp.main(argv)
        return code, buf.getvalue(), db

    def test_default_is_read_only_inspection(self):
        code, out, db = self._run([], self.ENV)
        self.assertEqual(code, 0)
        self.assertIn("점검 모드", out)
        self.assertEqual(db.patches, [])

    def test_send_without_approval_does_nothing(self):
        for extra in [{}, {"RESEND_API_KEY": "k", "MAIL_FROM": "f@x.y"}, {"MEMBER_REPORT_SEND_APPROVED": "yes"},
                      {"MEMBER_REPORT_SEND_APPROVED": "yes", "RESEND_API_KEY": "k"}]:
            code, out, db = self._run(["--send"], dict(self.ENV, **extra))
            self.assertEqual(code, 2, str(extra))
            self.assertIn("발송 안 함", out)
            self.assertEqual(db.patches, [], "승인 장치를 못 넘으면 요청 상태도 건드리지 않는다")
            self.assertEqual(db.t["member_report_requests"][0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
