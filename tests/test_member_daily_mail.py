# -*- coding: utf-8 -*-
import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import member_daily_mail as dm  # noqa: E402
import member_report as mr  # noqa: E402
import member_report_mail as mm  # noqa: E402

TODAY = "2026-11-03"
PNG = b"\x89PNG-test"


def pick_row(rank=1, **kw):
    r = {"pick_date": TODAY, "rank": rank, "stock_code": f"00000{rank}", "stock_name": f"테스트종목{rank}", "price": 10000.0,
         "open_price": 9500.0, "prev_close": 9800.0, "score": 0.9123, "align_days": 25, "new_high": True, "days_since_high": 0,
         "disp_pct": 6.5, "rsi": 71.0, "atr_pct": 0.032,
         "news": [{"title": "정상 기사 제목", "link": "https://example.com/a"}], "news_negative_count": 0,
         "chart_png": base64.b64encode(PNG).decode()}
    r.update(kw)
    return r


class FakeDb:
    """get() 은 eq./in. 필터만 흉내 낸다. upsert 는 기록한다."""

    def __init__(self, tables, emails):
        self.t, self.emails, self.upserts, self.gets = tables, emails, [], []

    def get(self, table, params):
        self.gets.append(table)
        rows = list(self.t.get(table, []))
        for k, v in params.items():
            if k in ("select", "order", "limit"):
                continue
            if v.startswith("eq."):
                want = v[3:]
                rows = [r for r in rows if str(r.get(k)).lower() == want.lower()]
            elif v.startswith("in.("):
                allowed = set(v[4:-1].split(","))
                rows = [r for r in rows if str(r.get(k)) in allowed]
        return rows

    def upsert(self, table, rows, on_conflict):
        self.upserts.append((table, rows, on_conflict))

    def user_email(self, uid):
        return self.emails.get(uid)


class Capture:
    supports_inline_images = True

    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], set(fail_for)

    def send(self, to, subject, html_body, text_body, inline_images=None):
        if to in self.fail_for:
            raise RuntimeError("fail")
        self.sent.append({"to": to, "subject": subject, "html": html_body, "text": text_body, "images": dict(inline_images or {})})


def fake_report(code, price=None):
    return {"name": f"보유{code}", "inner_html": f"<p>수치 표 {code}</p>", "inner_text": f"수치 표 {code}"}


def base_tables(**over):
    t = {
        "member_daily_picks": [pick_row(1), pick_row(2)],
        "member_mail_consent": [{"user_id": "u1", "digest_consent": True}, {"user_id": "u2", "digest_consent": True},
                                {"user_id": "u3", "digest_consent": False}],          # 동의했다가 해제한 회원(u3)은 대상이 아니다
        "member_daily_mail_log": [],
        "member_feature_access": [{"user_id": "u1", "holdings_enabled": True, "reports_enabled": True},
                                  {"user_id": "u2", "holdings_enabled": True, "reports_enabled": False}],
        "member_mail_prefs": [{"user_id": "u1", "alert_drop_pct": 5}],
        "member_holdings": [{"user_id": "u1", "stock_code": "005930", "stock_name": "삼성전자", "avg_price": 100000, "status": "open", "entry_date": "2026-10-01"},
                            {"user_id": "u2", "stock_code": "000660", "stock_name": "SK하이닉스", "avg_price": 50000, "status": "open", "entry_date": "2026-10-01"}],
        "member_prices": [{"stock_code": "005930", "stock_name": "삼성전자", "close": 94000, "trade_date": TODAY},
                          {"stock_code": "000660", "stock_name": "SK하이닉스", "close": 51000, "trade_date": TODAY}],
    }
    t.update(over)
    return t


EMAILS = {"u1": "one@example.com", "u2": "two@example.com", "u3": "three@example.com"}
RELEASE = {"MEMBER_DAILY_RELEASE_ALL": "yes"}


def go(db, sender, env=RELEASE, send=True, trading=True, admin="admin@example.com", make=fake_report):
    return dm.run(db, sender, make, TODAY, env=env, admin_email=admin, send=send, is_trading_day=lambda d: trading, log=lambda *a: None)


class WordingTests(unittest.TestCase):
    def facts(self, **kw):
        f = {"align_days": 25, "new_high": True, "days_since_high": 0, "disp_pct": 6.5, "rsi": 71.0, "atr_pct": 0.032}
        f.update(kw)
        return f

    def test_headline_templates(self):
        self.assertIn("20거래일 이상", dm.headline(self.facts()))
        self.assertIn("60일 신고가", dm.headline(self.facts()))
        self.assertEqual(dm.headline(self.facts(new_high=False)), "정배열이 20거래일 이상 이어지고 있습니다")
        self.assertEqual(dm.headline(self.facts(align_days=5)), "오늘 60일 신고가를 기록했습니다")
        self.assertEqual(dm.headline(self.facts(align_days=5, new_high=False, disp_pct=-2.0)), "20일선 아래에서 거래되고 있습니다")
        self.assertEqual(dm.headline(self.facts(align_days=5, new_high=False)), "정배열 상태이며 지속 기간은 짧은 편입니다")
        self.assertEqual(dm.headline(self.facts(align_days=0, new_high=False)), "정배열이 아직 만들어지지 않았습니다")

    def test_bits_use_ranges_not_judgement_words(self):
        bits = " | ".join(dm.chart_bits(self.facts(rsi=80.0, atr_pct=0.06)))
        self.assertIn("75 초과", bits)
        self.assertIn("5% 이상", bits)
        for old in ("과열", "탄력", "흔들림", "추세 이탈", "잔잔한"):
            self.assertNotIn(old, bits)
        low = " | ".join(dm.chart_bits(self.facts(disp_pct=-4.6, rsi=42.0, atr_pct=0.02, new_high=False, days_since_high=3)))
        self.assertIn("20일선 −4.6% 아래에 있습니다", low)
        self.assertIn("50 미만", low)
        self.assertIn("3% 미만", low)
        self.assertIn("3거래일 전 60일 신고가", low)

    def test_warn_sentences_have_no_stop_line_wording(self):
        w1 = dm.chart_warn(self.facts(atr_pct=0.073))
        w2 = dm.chart_warn(self.facts(atr_pct=0.02, disp_pct=55.0))
        self.assertIn("7.3%", w1)
        self.assertIn("55%", w2)
        for w in (w1, w2):
            self.assertNotIn("하락 참고선", w)
            self.assertEqual(dm.find_forbidden(w), [])
        self.assertIsNone(dm.chart_warn(self.facts()))

    def test_price_line_has_open_and_prev_changes_and_no_stop_price(self):
        v = dm.pick_view(pick_row())
        self.assertIn("종가 10,000원", v["price_line"])
        self.assertIn("시가 대비 +5.3%", v["price_line"])
        self.assertIn("전일 대비 +2.0%", v["price_line"])
        self.assertIn("차트 조건 점수 0.912", v["price_line"])
        v2 = dm.pick_view(pick_row(open_price=None, prev_close=None))
        self.assertIn("시가 대비 -", v2["price_line"])
        self.assertIn("전일 대비 -", v2["price_line"])

    def test_composed_picks_carry_no_recommendation_words(self):
        mail = dm.compose(TODAY, [dm.pick_view(pick_row(1)), dm.pick_view(pick_row(2))], [], 3, True)
        whole = mail["html"] + mail["text"]
        for word in ("추천", "후보", "하락 참고선", "추세점수", "붙잡", "자리", "청산", "진입", "처분"):
            self.assertNotIn(word, whole, word)
        self.assertEqual(dm.find_forbidden(mail["check_text"]), [])
        self.assertIn("특정 종목의 매수·매도를 권유하지 않습니다", mail["html"])        # 고정 면책 문구는 있다
        self.assertEqual(mail["subject"], f"[만조] {TODAY} 오늘의 차트 조건 검색 결과 2종목")

    def test_news_titles_with_banned_or_sensational_words_are_dropped(self):
        row = pick_row(news=[{"title": "A증권 목표가 상향", "link": "https://x.test/1"}, {"title": "코스피 폭락 공포", "link": "https://x.test/2"},
                             {"title": "신규 매수 추천 종목", "link": "https://x.test/3"}, {"title": "정상 기사", "link": "https://x.test/4"},
                             {"title": "링크 이상", "link": "javascript:alert(1)"}], news_negative_count=2)
        v = dm.pick_view(row)
        self.assertEqual([a["title"] for a in v["news"]], ["정상 기사", "링크 이상"])
        self.assertEqual(v["news"][1]["link"], "")                                    # http(s) 아닌 링크는 지운다
        self.assertEqual(v["note"], "기사 2건 중 부정적 표현이 포함된 기사가 2건 있습니다")
        self.assertEqual(dm.news_note(0, 0), "관련 기사가 없습니다")
        self.assertEqual(dm.news_note(3, 0), "최근 기사 3건에서 뚜렷한 악재는 걸리지 않았습니다")


class HoldingTests(unittest.TestCase):
    def test_alarm_threshold_boundaries(self):
        chg, hit = dm.alarm_status(100, 97, 3)
        self.assertTrue(hit)
        self.assertAlmostEqual(chg, -3.0)
        self.assertFalse(dm.alarm_status(100, 97.01, 3)[1])
        chg2, hit2 = dm.alarm_status(100, 110, 3)
        self.assertEqual((round(chg2, 1), hit2), (10.0, False))
        self.assertTrue(dm.alarm_status(100, 90, 10)[1])
        self.assertEqual(dm.alarm_status(None, 90, 3), (None, False))
        self.assertEqual(dm.alarm_status(0, 90, 3), (None, False))

    def test_card_line_uses_percent_only_never_amounts(self):
        h = {"stock_code": "005930", "stock_name": "삼성전자", "avg_price": 123456}
        card = dm.holding_card(h, {"close": 94000}, 5, fake_report("005930"))
        self.assertIn("입력하신 평균 매수가 대비 −23.9%", card["line"])
        self.assertIn("알람선 −5% 도달", card["line"])
        self.assertTrue(card["reached"])
        self.assertNotIn("123456", card["line"] + card["inner_html"])
        self.assertNotIn("123,456", card["line"] + card["inner_html"])
        near = dm.holding_card({**h, "avg_price": 95000}, {"close": 94000}, 5, fake_report("005930"))
        self.assertIn("도달 전", near["line"])
        self.assertFalse(near["reached"])

    def test_card_line_template_has_no_forbidden_words_besides_the_field_name(self):
        h = {"stock_code": "005930", "stock_name": "삼성전자", "avg_price": 100}
        for close in (90, 100, 120):
            line = dm.holding_card(h, {"close": close}, 3, fake_report("005930"))["line"]
            self.assertEqual(dm.find_forbidden(line.replace("평균 매수가", "평균가")), [], line)
        note = (dm.HOLDINGS_NOTE.replace("평균 매수가", "평균가").replace("실제 매수 여부와 무관한 ", "")
                .replace("매도 시점을 알려 드리는 것이 아닙니다", ""))
        self.assertEqual(dm.find_forbidden(note), [])          # 고정 안내문도 항목 이름('평균 매수가')·면책 표현 말고는 금지 표현이 없다

    def test_badge_only_when_reached_and_section_only_with_cards(self):
        picks = [dm.pick_view(pick_row())]
        reached = dm.holding_card({"stock_code": "005930", "stock_name": "x", "avg_price": 100}, {"close": 90}, 3, fake_report("005930"))
        quiet = dm.holding_card({"stock_code": "000660", "stock_name": "y", "avg_price": 100}, {"close": 100}, 3, fake_report("000660"))
        mail = dm.compose(TODAY, picks, [reached, quiet], 3, True)
        self.assertEqual(mail["html"].count("알람선 도달</span>"), 1)
        self.assertIn("내 보유 종목 알람 2종목", mail["html"])
        self.assertIn("시뮬레이션 값일 수 있습니다", mail["html"])
        self.assertNotIn("내 보유 종목 알람", dm.compose(TODAY, picks, [], 3, True)["html"])

    def test_images_only_when_sender_supports(self):
        picks = [dm.pick_view(pick_row())]
        with_img = dm.compose(TODAY, picks, [], 3, True)
        self.assertIn('src="cid:pick1"', with_img["html"])
        self.assertEqual(with_img["images"], {"pick1": PNG})
        without = dm.compose(TODAY, picks, [], 3, False)
        self.assertNotIn("cid:", without["html"])
        self.assertEqual(without["images"], {})


class RecipientTests(unittest.TestCase):
    C = [("u1", "One@Example.com"), ("u2", "two@example.com"), ("u3", None)]

    def test_nothing_without_any_gate_variable(self):
        self.assertEqual(dm.select_recipients(self.C, {}), [])

    def test_test_only_is_case_insensitive_and_exact(self):
        self.assertEqual(dm.select_recipients(self.C, {"MEMBER_DAILY_TEST_ONLY": " one@example.com ; other@x.com"}), [("u1", "One@Example.com")])
        self.assertEqual(dm.select_recipients(self.C, {"MEMBER_DAILY_TEST_ONLY": "example.com"}), [])

    def test_release_all_needs_exact_yes(self):
        self.assertEqual(len(dm.select_recipients(self.C, {"MEMBER_DAILY_RELEASE_ALL": "yes"})), 3)
        self.assertEqual(dm.select_recipients(self.C, {"MEMBER_DAILY_RELEASE_ALL": "true"}), [])
        self.assertEqual(dm.select_recipients(self.C, {"MEMBER_DAILY_RELEASE_ALL": "no", "MEMBER_DAILY_TEST_ONLY": "two@example.com"}), [("u2", "two@example.com")])


class RunTests(unittest.TestCase):
    def test_only_consented_members_receive_mail_and_log_has_no_private_data(self):
        db = FakeDb(base_tables(), EMAILS)
        s = Capture()
        out = go(db, s)
        self.assertEqual((out["sent"], out["error"]), (2, 0))
        self.assertEqual(sorted(m["to"] for m in s.sent), ["one@example.com", "two@example.com"])      # u3 는 동의하지 않아 대상 아님
        table, rows, conflict = db.upserts[0]
        self.assertEqual((table, conflict), ("member_daily_mail_log", "user_id,send_date"))
        self.assertEqual({r["status"] for r in rows}, {"sent"})
        dump = repr(rows)
        for private in ("example.com", "005930", "000660", "100000", "avg_price"):
            self.assertNotIn(private, dump)
        self.assertEqual({r["user_id"]: r["holdings_count"] for r in rows}, {"u1": 1, "u2": 0})   # 개수만 기록

    def test_test_only_gate_limits_recipients(self):
        db, s = FakeDb(base_tables(), EMAILS), Capture()
        out = go(db, s, env={"MEMBER_DAILY_TEST_ONLY": "one@example.com"})
        self.assertEqual([m["to"] for m in s.sent], ["one@example.com"])
        self.assertEqual(out["sent"], 1)

    def test_no_gate_variables_means_no_mail(self):
        db, s = FakeDb(base_tables(), EMAILS), Capture()
        out = go(db, s, env={})
        self.assertEqual((s.sent, db.upserts, out["status"]), ([], [], "no_recipients"))

    def test_holdings_section_only_for_report_enabled_members_with_own_alarm_pct(self):
        db, s = FakeDb(base_tables(), EMAILS), Capture()
        go(db, s)
        mails = {m["to"]: m for m in s.sent}
        one, two = mails["one@example.com"], mails["two@example.com"]
        self.assertIn("내 보유 종목 알람 1종목", one["html"])
        self.assertIn("알람선 −5% 도달", one["html"])                  # u1 의 설정 5% 적용(94000 ≤ 100000×0.95)
        self.assertNotIn("내 보유 종목 알람", two["html"])                    # u2 는 리포트 허용이 꺼져 있다
        self.assertNotIn("보유000660", two["html"] + two["text"])

    def test_default_alarm_pct_is_three(self):
        t = base_tables(member_mail_prefs=[], member_holdings=[{"user_id": "u1", "stock_code": "005930", "stock_name": "삼성전자",
                                                                "avg_price": 97000, "status": "open", "entry_date": "2026-10-01"}])
        db, s = FakeDb(t, EMAILS), Capture()
        go(db, s, env={"MEMBER_DAILY_TEST_ONLY": "one@example.com"})
        self.assertIn("알람선 −3% 도달", s.sent[0]["html"])           # 94000 ≤ 97000×0.97=94090

    def test_closed_holdings_and_cap_of_ten(self):
        hs = [{"user_id": "u1", "stock_code": f"{i:06d}", "stock_name": f"S{i}", "avg_price": 100, "status": "open", "entry_date": f"2026-10-{i:02d}"}
              for i in range(1, 13)]
        hs.append({"user_id": "u1", "stock_code": "999999", "stock_name": "Z", "avg_price": 100, "status": "closed", "entry_date": "2026-10-30"})
        prices = [{"stock_code": h["stock_code"], "stock_name": h["stock_name"], "close": 100, "trade_date": TODAY} for h in hs]
        db, s = FakeDb(base_tables(member_holdings=hs, member_prices=prices), EMAILS), Capture()
        go(db, s, env={"MEMBER_DAILY_TEST_ONLY": "one@example.com"})
        self.assertIn("내 보유 종목 알람 10종목", s.sent[0]["html"])
        self.assertNotIn("보유999999", s.sent[0]["html"])
        self.assertEqual(db.upserts[0][1][0]["holdings_count"], 10)

    def test_already_sent_today_is_skipped(self):
        t = base_tables(member_daily_mail_log=[{"user_id": "u1", "send_date": TODAY, "status": "sent"}])
        db, s = FakeDb(t, EMAILS), Capture()
        out = go(db, s)
        self.assertEqual([m["to"] for m in s.sent], ["two@example.com"])
        self.assertEqual((out["sent"], out["skipped"]), (1, 1))

    def test_error_status_is_retried_but_sent_is_not(self):
        t = base_tables(member_daily_mail_log=[{"user_id": "u1", "send_date": TODAY, "status": "error"}])
        db, s = FakeDb(t, EMAILS), Capture()
        go(db, s)
        self.assertIn("one@example.com", [m["to"] for m in s.sent])

    def test_non_trading_day_sends_nothing_and_writes_nothing(self):
        db, s = FakeDb(base_tables(), EMAILS), Capture()
        out = go(db, s, trading=False)
        self.assertEqual((out["status"], s.sent, db.upserts, db.gets), ("not_trading_day", [], [], []))

    def test_missing_picks_sends_nothing_but_alerts_admin(self):
        db, s = FakeDb(base_tables(member_daily_picks=[]), EMAILS), Capture()
        out = go(db, s)
        self.assertEqual(out["status"], "no_data")
        self.assertEqual([m["to"] for m in s.sent], ["admin@example.com"])
        self.assertIn("자료가 없어", s.sent[0]["text"])
        self.assertEqual(db.upserts, [])

    def test_yesterdays_picks_are_not_used(self):
        old = [pick_row(1, pick_date="2026-11-02")]
        db, s = FakeDb(base_tables(member_daily_picks=old), EMAILS), Capture()
        self.assertEqual(go(db, s)["status"], "no_data")

    def test_one_failure_does_not_stop_the_others_and_is_logged_and_alerted(self):
        db, s = FakeDb(base_tables(), EMAILS), Capture(fail_for=["one@example.com"])
        out = go(db, s)
        self.assertEqual((out["sent"], out["error"]), (1, 1))
        rows = {r["user_id"]: r for r in db.upserts[0][1]}
        self.assertEqual((rows["u1"]["status"], rows["u2"]["status"]), ("error", "sent"))
        self.assertNotIn("example.com", repr(rows))
        self.assertTrue(any(m["to"] == "admin@example.com" for m in s.sent))                     # 회장님께 실패 알림
        admin = [m for m in s.sent if m["to"] == "admin@example.com"][0]
        self.assertNotIn("one@example.com", admin["text"] + admin["html"])

    def test_forbidden_words_in_a_holding_report_block_only_that_members_mail(self):
        def bad_report(code, price):
            r = fake_report(code, price)
            if code == "005930":
                r["inner_text"] = "내일 목표가 상향 예상"
            return r
        db, s = FakeDb(base_tables(), EMAILS), Capture()
        out = go(db, s, make=bad_report)
        self.assertEqual([m["to"] for m in s.sent if m["to"] != "admin@example.com"], ["two@example.com"])
        self.assertEqual((out["sent"], out["error"]), (1, 1))
        row = [r for r in db.upserts[0][1] if r["user_id"] == "u1"][0]
        self.assertEqual((row["status"], row["error_message"]), ("error", "RuntimeError: 금지 표현 검사 불통과"))

    def test_holding_report_failure_drops_only_that_card(self):
        def flaky(code, price):
            raise RuntimeError("boom")
        db, s = FakeDb(base_tables(), EMAILS), Capture()
        out = go(db, s, make=flaky)
        self.assertEqual(out["sent"], 2)
        self.assertNotIn("내 보유 종목 알람", s.sent[0]["html"])

    def test_reports_are_made_once_per_stock(self):
        t = base_tables(member_feature_access=[{"user_id": "u1", "holdings_enabled": True, "reports_enabled": True},
                                               {"user_id": "u2", "holdings_enabled": True, "reports_enabled": True}],
                        member_holdings=[{"user_id": "u1", "stock_code": "005930", "stock_name": "삼성전자", "avg_price": 100, "status": "open", "entry_date": "2026-10-01"},
                                         {"user_id": "u2", "stock_code": "005930", "stock_name": "삼성전자", "avg_price": 120, "status": "watch", "entry_date": "2026-10-02"}])
        calls = []
        def counting(code, price):
            calls.append(code)
            return fake_report(code, price)
        go(FakeDb(t, EMAILS), Capture(), make=counting)
        self.assertEqual(calls, ["005930"])

    def test_preview_composes_one_mail_without_sending_or_logging(self):
        db, s = FakeDb(base_tables(), EMAILS), Capture()
        out = go(db, s, send=False)
        self.assertEqual((s.sent, db.upserts), ([], []))
        self.assertEqual(out["status"], "ok")
        self.assertIn("조건 충족 종목 2종목", out["preview"]["html"])
        self.assertEqual(out["sent"], 0)

    def test_sender_without_image_support_gets_plain_body(self):
        class NoImg(Capture):
            supports_inline_images = False
        db, s = FakeDb(base_tables(), EMAILS), NoImg()
        go(db, s)
        self.assertTrue(s.sent and all("cid:" not in m["html"] and m["images"] == {} for m in s.sent))

    def test_images_are_passed_to_senders_that_support_them(self):
        db, s = FakeDb(base_tables(), EMAILS), Capture()
        go(db, s)
        self.assertEqual(sorted(s.sent[0]["images"]), ["pick1", "pick2"])
        self.assertIn('src="cid:pick1"', s.sent[0]["html"])


class SenderTests(unittest.TestCase):
    def test_gmail_message_embeds_inline_images(self):
        g = mm.GmailSmtpSender("a@example.com", "x" * 16)
        m = g._message("b@example.com", "제목", '<p><img src="cid:pick1"></p>', "본문", {"pick1": PNG})
        types = [p.get_content_type() for p in m.walk()]
        self.assertIn("image/png", types)
        self.assertEqual([p["Content-ID"] for p in m.walk() if p["Content-ID"]], ["<pick1>"])
        plain = g._message("b@example.com", "제목", "<p>x</p>", "본문")
        self.assertNotIn("image/png", [p.get_content_type() for p in plain.walk()])

    def test_support_flags_and_fake_sender_records_images(self):
        self.assertTrue(mm.GmailSmtpSender.supports_inline_images)
        self.assertFalse(mm.ResendSender.supports_inline_images)
        fs = mr.FakeSender()
        fs.send("a@example.com", "s", "<p/>", "t", {"pick2": PNG, "pick1": PNG})
        self.assertEqual(fs.sent[0]["images"], ["pick1", "pick2"])


class ReportPartsTests(unittest.TestCase):
    def test_build_report_exposes_inner_parts_without_disclaimer(self):
        price = {"stock_code": "005930", "stock_name": "삼성전자", "close": 100000, "ma5": 99000, "ma20": 95000, "ma60": 90000,
                 "ma120": 85000, "high_52w": 120000, "low_52w": 70000, "trade_date": "2026-11-03"}
        rep = mr.build_report("005930", price, [{"title": "기사", "url": "https://example.com", "date": "11-03"}])
        self.assertEqual(rep["name"], "삼성전자")
        self.assertNotIn(mr.DISCLAIMER, rep["inner_html"] + rep["inner_text"])
        self.assertNotIn("<h1", rep["inner_html"])
        self.assertIn("가격·차트 위치", rep["inner_html"])
        self.assertIn(mr.DISCLAIMER, rep["html"])                                  # 기존 단독 리포트는 그대로 면책 문구를 가진다
        self.assertIn("[가격·차트 위치]", rep["inner_text"])


if __name__ == "__main__":
    unittest.main()
