# -*- coding: utf-8 -*-
"""scripts/member_report.py 테스트. 실행:  python -m unittest tests.test_member_report -v   (저장소 루트에서)
네트워크·Groq·Supabase 없이 가짜(Fake)로만 돈다."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import member_report as mr  # noqa: E402

PRICE = {"stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI", "close": 215500, "ma5": 215000, "ma20": 230000,
         "ma60": 240000, "ma120": 260000, "high_52w": 300000, "low_52w": 90000, "trade_date": "2026-10-23"}
NEWS = [{"title": "삼성전자, 새 반도체 공장 가동", "summary": "신규 라인이 가동을 시작했다.", "date": "2026-10-22", "url": "https://n.news.naver.com/a"},
        {"title": "삼성전자, 새 반도체 공장 가동", "summary": "중복 제목", "date": "2026-10-22", "url": "https://n.news.naver.com/b"}]

GOOD_AI = ("삼성전자의 종가는 215,500원(2026-10-23 기준)으로, 20일선보다 6.3% 낮고 60일선보다 10.2% 낮은 위치에 있습니다. "
           "이동평균은 역배열 상태이며, 52주 고점 대비 28.2% 낮은 수준입니다.\n\n"
           "최근 수집된 기사에서는 새 반도체 공장 가동과 관련한 보도가 확인됩니다.")


class FakeDb:
    def __init__(self, prices=None, reports=None, requests=None, emails=None):
        self.t = {"member_prices": prices or [], "member_stock_reports": reports or [], "member_report_requests": requests or []}
        self.emails, self.upserts, self.race_ids = emails or {}, [], set()

    @staticmethod
    def _match(row, params):
        for k, v in params.items():
            if k in ("select", "limit", "order"):
                continue
            if v.startswith("eq.") and str(row.get(k)) != v[3:]:
                return False
        return True

    def get(self, table, params):
        rows = [r for r in self.t[table] if self._match(r, params)]
        return [dict(r) for r in rows[:int(params.get("limit", 1000))]]

    def upsert(self, table, rows, on_conflict):
        self.upserts.append((table, rows))
        for r in rows:
            self.t[table].append(dict(r))

    def patch(self, table, params, body):
        if table == "member_report_requests" and params.get("status") == "eq.pending" and params["id"][3:] in self.race_ids:
            return []
        changed = []
        for r in self.t[table]:
            if self._match(r, params):
                r.update(body)
                changed.append(dict(r))
        return changed

    def user_email(self, uid):
        return self.emails.get(uid)


class BannedTests(unittest.TestCase):
    def test_banned_sentences_are_caught(self):
        for s in ["증권가에서 목표가를 상향했습니다", "매수 의견이 나왔습니다", "주가가 오를 가능성이 높습니다",
                  "실적 개선이 이어질 것으로 예상됩니다", "지금 사야 합니다", "손절 라인을 지키세요", "BUY rating", "저평가 구간입니다",
                  "비중 확대가 필요합니다", "상승 여력이 큽니다", "반등할 수 있습니다", "투자의견 상향"]:
            self.assertTrue(mr.find_banned(s), "걸러져야 함: " + s)

    def test_net_flow_terms_are_allowed_but_advice_is_not(self):
        # 수급 수치의 이름("순매수·순매도")은 사실이므로 허용
        for s in ["외국인은 2,101.7억원 순매도, 기관은 1,340.7억원 순매수를 기록했습니다.", "개인 순매수가 693.7억원입니다."]:
            self.assertEqual(mr.find_banned(s), [], "허용되어야 함: " + s)
        # 행동 권유·평가로 쓰이는 매수·매도는 여전히 막는다
        for s in ["매수 의견이 나왔습니다", "지금이 매도 시점입니다", "외국인 매도세가 강합니다", "기관 매수세가 이어집니다", "분할 매수 전략"]:
            self.assertTrue(mr.find_banned(s), "걸러져야 함: " + s)

    def test_clean_sentences_pass(self):
        for s in ["종가는 215,500원이며 20일선보다 6.3% 낮습니다.", "이동평균은 역배열 상태입니다.", "공장이 가동을 시작했다는 기사가 있습니다."]:
            self.assertEqual(mr.find_banned(s), [], "통과해야 함: " + s)


class NumberTests(unittest.TestCase):
    def test_unsupported_numbers(self):
        allowed = "종가: 215,500원 (2026-10-23 기준)\n20일선 대비: -6.3%"
        self.assertEqual(mr.unsupported_numbers("종가 215,500원, 20일선보다 6.3% 낮음", allowed), [])
        self.assertEqual(mr.unsupported_numbers("외국인이 3,456억원을 사들임", allowed), ["3456"])
        self.assertEqual(mr.unsupported_numbers("3일 연속", allowed), [])   # 한 자리 숫자는 검사하지 않는다


class ChartPositionTests(unittest.TestCase):
    def test_reverse_alignment_and_values(self):
        cp = mr.chart_position(PRICE)
        self.assertEqual(cp["alignment"], "역배열")
        self.assertAlmostEqual(cp["vs"]["ma20"], -6.3043, places=3)
        self.assertAlmostEqual(cp["vs"]["low52"], 139.4444, places=3)

    def test_aligned_mixed_and_missing(self):
        self.assertEqual(mr.chart_position({"close": 100, "ma5": 98, "ma20": 95, "ma60": 90})["alignment"], "정배열")
        self.assertEqual(mr.chart_position({"close": 100, "ma5": 90, "ma20": 95, "ma60": 92})["alignment"], "혼조")
        e = mr.chart_position({"close": None})
        self.assertIsNone(e["alignment"])
        self.assertIsNone(e["vs"]["ma20"])
        self.assertIsNone(mr.chart_position({"close": 100})["alignment"])


class ValidateAiTests(unittest.TestCase):
    ALLOWED = mr.facts_text(mr.build_facts(PRICE)) + "\n삼성전자, 새 반도체 공장 가동 신규 라인이 가동을 시작했다. 2026-10-22"

    def test_good_text_passes(self):
        self.assertIsNone(mr.validate_ai_text(GOOD_AI, self.ALLOWED))

    def test_rejections(self):
        base = "삼성전자의 종가는 215,500원(2026-10-23 기준)입니다. 이동평균은 역배열 상태이며 기사에서는 공장 가동이 확인됩니다. "
        cases = {
            "금지 표현": base + "증권가에서는 목표가를 상향하며 매수 의견을 제시했습니다.",
            "외국어": base + "反应이 있었습니다 그래서 길이를 맞춥니다.",
            "근거 없는 숫자": base + "외국인이 3,456억원을 사들였다는 내용도 있습니다.",
            "형식": "## 제목\n" + base,
            "길이": "짧음",
            "빈": "",
        }
        for key, text in cases.items():
            self.assertIsNotNone(mr.validate_ai_text(text, self.ALLOWED), key + " 은(는) 거절되어야 함")

    def test_ai_explanation_retries_once(self):
        calls = []
        bad = "삼성전자의 종가는 215,500원입니다. 앞으로 주가가 오를 가능성이 높아 매수 의견을 제시합니다. 충분한 길이를 맞추기 위한 문장입니다."
        seq = [bad, GOOD_AI]
        text, reason = mr.ai_explanation(lambda p: calls.append(p) or seq[len(calls) - 1], "삼성전자", "005930", mr.build_facts(PRICE), NEWS[:1])
        self.assertEqual(text, GOOD_AI)
        self.assertIsNone(reason)
        self.assertEqual(len(calls), 2)

    def test_ai_explanation_gives_up_after_two_bad(self):
        bad = "삼성전자의 종가는 215,500원입니다. 앞으로 주가가 오를 가능성이 높아 매수 의견을 제시합니다. 충분한 길이를 맞추기 위한 문장입니다."
        calls = []
        text, reason = mr.ai_explanation(lambda p: calls.append(p) or bad, "삼성전자", "005930", mr.build_facts(PRICE), [])
        self.assertIsNone(text)
        self.assertIn("금지 표현", reason)
        self.assertEqual(len(calls), 2)

    def test_on_reject_receives_rejected_text_and_reason(self):
        bad = "삼성전자의 종가는 215,500원입니다. 앞으로 주가가 오를 가능성이 높아 매수 의견을 제시합니다. 충분한 길이를 맞추기 위한 문장입니다."
        seen = []
        mr.ai_explanation(lambda p: bad, "삼성전자", "005930", mr.build_facts(PRICE), [], on_reject=lambda t, r: seen.append((t, r)))
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][0], bad)
        self.assertIn("금지 표현", seen[0][1])

    def test_prompt_asks_polite_style_and_signed_net_buy_only(self):
        p = mr.build_prompt("삼성전자", "005930", "x", [])
        self.assertIn("존댓말", p)
        self.assertIn("부호까지 그대로", p)
        self.assertIn("(순매도)", p)          # '순매도' 부연을 쓰지 말라는 지시
        self.assertIn("자극적인 표현", p)
        self.assertIn("2~3개", p)

    def test_net_sell_wording_and_sensational_words_are_rejected(self):
        allowed = mr.facts_text(mr.build_facts(PRICE, {"foreign": -2.1e11, "institution": 1.3e11, "individual": 6.9e10}))
        base = "삼성전자의 종가는 215,500원(2026-10-23 기준)이며 20일선보다 6.3% 낮은 위치에 있습니다. 이동평균 배열은 역배열입니다. "
        # 부호가 있는 "순매수" 값으로만 표기하면 통과한다
        self.assertIsNone(mr.validate_ai_text(base + "외국인 순매수 -2,100.0억원, 기관 순매수 +1,300.0억원입니다. 기사에서는 공장 가동이 확인됩니다.", allowed))
        r1 = mr.validate_ai_text(base + "외국인은 순매도했고 기사에서는 공장 가동이 확인됩니다.", allowed)
        self.assertIsNotNone(r1)
        self.assertIn("순매도", r1)
        r2 = mr.validate_ai_text(base + "증시 폭락 속에서도 기사에서는 공장 가동이 확인됩니다.", allowed)
        self.assertIsNotNone(r2)
        self.assertIn("자극적", r2)
        r3 = mr.validate_ai_text(base + "이어서 대반전 시나리오가 언급되었다는 기사가 있습니다.", allowed)
        self.assertIn("자극적", r3)

    def test_prompt_mentions_net_flow_wording(self):
        self.assertIn("순매수", mr.build_prompt("삼성전자", "005930", "x", []))

    def test_prompt_contains_rules_and_no_member_data(self):
        p = mr.build_prompt("삼성전자", "005930", "종가: 215,500원", NEWS[:1])
        for must in ["순수 한국어", "목표가", "지어내지", "제공된 수치"]:
            self.assertIn(must, p)
        for never in ["매수가", "수량", "평가손익", "내 평균"]:
            self.assertNotIn(never, p.replace("매수·매도", ""))


class RenderTests(unittest.TestCase):
    def test_escapes_and_unsafe_links(self):
        price = dict(PRICE, stock_name="<script>alert(1)</script>")
        news = [{"title": "<img src=x onerror=1>", "url": "javascript:alert(1)", "date": "2026-10-22"},
                {"title": "정상 기사", "url": "https://n.news.naver.com/x", "date": "2026-10-22"}]
        r = mr.build_report("005930", price, news, None, GOOD_AI)
        self.assertNotIn("<script>", r["html"])
        self.assertNotIn("<img", r["html"])
        self.assertNotIn("javascript:", r["html"])
        self.assertIn('href="https://n.news.naver.com/x"', r["html"])
        self.assertTrue(r["subject"].startswith("[만조]"))
        self.assertIn("2026-10-23", r["subject"])

    def test_disclaimer_and_request_note_always_present(self):
        r = mr.build_report("005930", PRICE, NEWS, None, None)
        for part in ("html", "text"):
            self.assertIn("투자 권유·자문이 아닙니다", r[part])
            self.assertIn("직접 요청해 발송된 메일", r[part])

    def test_fallback_has_numbers_but_no_ai_section(self):
        r = mr.build_report("005930", PRICE, NEWS, None, None)
        self.assertFalse(r["used_ai"])
        self.assertNotIn("현재 상태 설명", r["html"])
        self.assertIn("215,500원", r["html"])
        self.assertIn("-6.3%", r["text"])
        self.assertIn("역배열", r["text"])
        self.assertEqual(r["html"].count("새 반도체 공장 가동"), 1)   # 같은 제목 기사는 한 번만

    def test_ai_section_when_given_and_no_news_message(self):
        r = mr.build_report("005930", PRICE, [], None, GOOD_AI)
        self.assertTrue(r["used_ai"])
        self.assertIn("현재 상태 설명", r["html"])
        self.assertIn("최근 수집된 관련 기사가 없습니다", r["html"])

    def test_investor_rows_only_when_available(self):
        self.assertNotIn("외국인", mr.build_report("005930", PRICE, [], None, None)["text"])
        inv = {"foreign": 1.2e9, "institution": -3.4e9, "individual": 2.2e9}
        t = mr.build_report("005930", PRICE, [], inv, None)["text"]
        self.assertIn("외국인 순매수: +12.0억원", t)
        self.assertIn("기관 순매수: -34.0억원", t)

    def test_html_entities_in_news_titles_are_shown_as_text(self):
        news = [{"title": "&quot;연 4% 이자라니&quot; 조정장에 &amp; 기업도", "url": "https://n.news.naver.com/z", "date": "2026-10-26"}]
        r = mr.build_report("005930", PRICE, news, None, None)
        self.assertIn('"연 4% 이자라니" 조정장에 & 기업도', r["text"])
        self.assertNotIn("&quot;", r["text"])
        self.assertNotIn("&amp;quot;", r["html"])           # 이중 이스케이프(메일에 &quot; 가 그대로 보이는 현상) 방지
        self.assertIn("&quot;연 4% 이자라니&quot;", r["html"])  # HTML 에서는 한 번만 이스케이프

    def test_no_member_specific_fields_in_report(self):
        r = mr.build_report("005930", PRICE, NEWS, None, GOOD_AI)
        for word in ("평균단가", "평균 매수가", "평가손익", "수량"):
            self.assertNotIn(word, r["text"])


class NewsFetchAndTextNormalizeTests(unittest.TestCase):
    def test_news_window_is_based_on_today_not_trade_date(self):
        calls = []
        class FakeCg:
            @staticmethod
            def fetch_stock_news(code, date, max_articles, days_before, days_after):
                calls.append((code, date, days_before, days_after))
                return []
        fetch = mr.make_news_fn(FakeCg, today="2026-10-28")
        fetch("005930", "2026-10-27")          # 시세일(10/27)이 아니라 오늘(10/28) 기준으로 찾아야 한다
        self.assertEqual(calls, [("005930", "2026-10-28", 7, 1)])

    def test_odd_minus_signs_become_ascii_hyphen(self):
        self.assertEqual(mr.normalize_ai_text("52주 고점 대비 ‑38.1%, 순매수 −3,546.3억원, –14.8%"),
                         "52주 고점 대비 -38.1%, 순매수 -3,546.3억원, -14.8%")
        self.assertEqual(mr.normalize_ai_text("20일선-60일선 사이"), "20일선-60일선 사이")   # 숫자 앞이 아닌 하이픈은 그대로

    def test_ai_explanation_normalizes_minus_before_validation(self):
        good = ("삼성전자의 종가는 215,500원(2026-10-23 기준)으로 20일선보다 6.3% 낮은 위치에 있습니다. 52주 고점 대비 ‑28.2% 수준이며, "
                "이동평균은 역배열입니다.\n\n최근 수집된 관련 기사가 없습니다.")
        text, reason = mr.ai_explanation(lambda p: good, "삼성전자", "005930", mr.build_facts(PRICE), [])
        self.assertIsNone(reason)
        self.assertIn("대비 -28.2%", text)
        self.assertNotIn("‑", text)

    def test_prompt_asks_position_wording_not_up_down(self):
        self.assertIn("높은/낮은 위치", mr.build_prompt("삼성전자", "005930", "x", []))


class SensationalNewsTests(unittest.TestCase):
    @staticmethod
    def _n(title):
        return {"title": title, "summary": "", "date": "2026-10-26", "url": "https://n.news.naver.com/x"}

    def test_find_sensational(self):
        for s in ["레버리지에 6조 날린 개미들 패닉", "폭락장에 피눈물 흘리는데", "'9천피' 대반전 시나리오 떴다", "곡소리 나는 증시"]:
            self.assertTrue(mr.find_sensational(s), "걸러져야 함: " + s)
        for s in ["삼성전자, 새 반도체 공장 가동", "3분기 실적 발표 앞두고 주주환원책 주목", "코스피 5,500선 내줘"]:
            self.assertEqual(mr.find_sensational(s), [], "통과해야 함: " + s)

    def test_generate_drops_sensational_titles_before_report_and_ai(self):
        arts = [self._n("삼성전자, 신제품 출시"), self._n("삼성전자 실적 발표"), self._n("삼성전자 주주환원책"),
                self._n("폭락장에 피눈물 흘리는 삼성전자 개미"), self._n("삼성전자 대반전 시나리오")]
        seen_prompts = []
        r = mr.generate("005930", PRICE, lambda c, d: arts, None, lambda p: seen_prompts.append(p) or "")
        for word in ("폭락장", "피눈물", "대반전"):
            self.assertNotIn(word, r["text"])
            self.assertNotIn(word, "\n".join(p.split("기사:")[-1] for p in seen_prompts))
        self.assertIn("삼성전자 실적 발표", r["text"])

    def test_all_sensational_means_no_news_message(self):
        arts = [self._n("삼성전자 폭락"), self._n("삼성전자 패닉")]
        r = mr.generate("005930", PRICE, lambda c, d: arts)
        self.assertIn("최근 수집된 관련 기사가 없습니다", r["text"])


class RelevantNewsTests(unittest.TestCase):
    @staticmethod
    def _n(title, summary=""):
        return {"title": title, "summary": summary, "date": "2026-10-26", "url": "https://n.news.naver.com/x"}

    def test_keeps_only_articles_mentioning_the_stock_when_enough(self):
        arts = [self._n("삼성전자, 신제품 출시"), self._n("엉뚱한 회사 소식", "삼성전자와 무관"), self._n("스마트폰 시장", "삼성전자 점유율"),
                self._n("전혀 다른 기사", "아무 관련 없음"), self._n("삼성전자 3분기 실적 발표")]
        got = [a["title"] for a in mr._prefer_relevant("삼성전자", arts)]
        self.assertEqual(got, ["삼성전자, 신제품 출시", "엉뚱한 회사 소식", "스마트폰 시장", "삼성전자 3분기 실적 발표"])

    def test_falls_back_to_all_when_fewer_than_three_match(self):
        arts = [self._n("삼성전자 소식"), self._n("다른 기사 1"), self._n("다른 기사 2")]
        self.assertEqual(len(mr._prefer_relevant("삼성전자", arts)), 3)

    def test_caps_at_news_max_and_dedupes(self):
        arts = [self._n(f"삼성전자 기사 {i}") for i in range(20)] + [self._n("삼성전자 기사 0")]
        got = mr._prefer_relevant("삼성전자", arts)
        self.assertEqual(len(got), mr.NEWS_MAX)
        self.assertEqual(len({a["title"] for a in got}), mr.NEWS_MAX)

    def test_generate_uses_relevant_articles_for_report(self):
        arts = [self._n("삼성전자, 신제품 출시"), self._n("삼성전자 실적 발표"), self._n("삼성전자 주주환원"), self._n("크린앤사이언스 해외 매출")]
        r = mr.generate("005930", PRICE, lambda c, d: arts)
        self.assertNotIn("크린앤사이언스", r["text"])
        self.assertIn("삼성전자 실적 발표", r["text"])


class GenerateTests(unittest.TestCase):
    def test_banned_ai_falls_back_to_basic_report(self):
        logs = []
        bad = "삼성전자의 종가는 215,500원입니다. 앞으로 주가가 오를 가능성이 높아 매수 의견을 제시합니다. 충분한 길이를 맞추기 위한 문장입니다."
        r = mr.generate("005930", PRICE, lambda c, d: NEWS, None, lambda p: bad, log=logs.append)
        self.assertFalse(r["used_ai"])
        self.assertNotIn("매수", r["text"])
        self.assertTrue(any("AI 설명 버림" in x for x in logs))

    def test_good_ai_is_used(self):
        r = mr.generate("005930", PRICE, lambda c, d: NEWS, None, lambda p: GOOD_AI)
        self.assertTrue(r["used_ai"])
        self.assertIn("이동평균은 역배열 상태", r["text"])

    def test_news_and_investor_failures_do_not_stop_report(self):
        def boom(*a):
            raise RuntimeError("network")
        logs = []
        r = mr.generate("005930", PRICE, boom, boom, None, log=logs.append)
        self.assertIn("215,500원", r["text"])
        self.assertEqual(len(logs), 2)


class ReportMakerTests(unittest.TestCase):
    def test_reuses_same_day_report(self):
        db = FakeDb(prices=[PRICE])
        ai_calls = []
        maker = mr.ReportMaker(db, lambda c, d: NEWS, None, lambda p: ai_calls.append(p) or GOOD_AI)
        a = maker("005930")
        b = maker("005930")
        self.assertEqual(len(ai_calls), 1, "같은 종목·같은 날은 AI를 한 번만 부른다")
        self.assertEqual(len(db.upserts), 1)
        self.assertEqual(a["subject"], b["subject"])
        self.assertEqual(b["text"], a["text"])
        self.assertTrue(b["used_ai"])

    def test_missing_price_raises(self):
        with self.assertRaises(RuntimeError):
            mr.ReportMaker(FakeDb(), lambda c, d: [], None, None)("005930")


class ProcessQueueTests(unittest.TestCase):
    def _db(self):
        reqs = [{"id": "r1", "user_id": "u1", "stock_code": "005930", "status": "pending", "requested_at": "2026-10-26T01:00:00Z"},
                {"id": "r2", "user_id": "u2", "stock_code": "005930", "status": "pending", "requested_at": "2026-10-26T01:01:00Z"},
                {"id": "r3", "user_id": "u3", "stock_code": "000660", "status": "pending", "requested_at": "2026-10-26T01:02:00Z"},
                {"id": "r4", "user_id": "u1", "stock_code": "035720", "status": "sent", "requested_at": "2026-10-26T00:00:00Z"}]
        return FakeDb(requests=reqs, emails={"u1": "alice@example.com", "u2": "bob@example.com"})

    @staticmethod
    def _status(db):
        return {r["id"]: r for r in db.t["member_report_requests"]}

    def test_sends_once_per_stock_and_marks_status(self):
        db, sender, made = self._db(), mr.FakeSender(), []
        def make(code):
            made.append(code)
            return {"subject": "s-" + code, "html": "<p>h</p>", "text": "t"}
        out = mr.process_queue(db, sender, make)
        st = self._status(db)
        self.assertEqual(out, {"claimed": 3, "sent": 2, "error": 1})
        self.assertEqual((st["r1"]["status"], st["r2"]["status"], st["r3"]["status"], st["r4"]["status"]), ("sent", "sent", "error", "sent"))
        self.assertEqual(made, ["005930", "000660"], "같은 종목은 리포트를 한 번만 만든다")
        self.assertEqual(len(sender.sent), 2)
        self.assertEqual(st["r3"]["error_message"], "RuntimeError: 수신 주소를 찾지 못했습니다")
        self.assertTrue(all("alice@example.com" not in x["to"] and "bob@example.com" not in x["to"] for x in sender.sent))
        self.assertEqual(sender.sent[0]["to"], "a***@example.com")
        self.assertIsNotNone(st["r1"]["processed_at"])

    def test_claims_before_sending(self):
        db, seen = self._db(), []
        class Probe(mr.FakeSender):
            def send(s, to, subject, html_body, text_body):
                seen.append({r["id"]: r["status"] for r in db.t["member_report_requests"]}["r1"])
                super().send(to, subject, html_body, text_body)
        mr.process_queue(db, Probe(), lambda c: {"subject": "s", "html": "h", "text": "t"})
        self.assertEqual(seen[0], "processing", "발송 직전에는 이미 '처리 중'으로 선점돼 있어야 한다")

    def test_skips_request_claimed_elsewhere(self):
        db = self._db()
        db.race_ids = {"r2"}
        sender = mr.FakeSender()
        out = mr.process_queue(db, sender, lambda c: {"subject": "s", "html": "h", "text": "t"})
        self.assertEqual(out["claimed"], 2)
        self.assertEqual(self._status(db)["r2"]["status"], "pending")
        self.assertEqual(len(sender.sent), 1)

    def test_sender_failure_marks_error_without_email_and_continues(self):
        db = self._db()
        class Flaky(mr.FakeSender):
            def send(s, to, subject, html_body, text_body):
                if to.startswith("alice"):
                    raise RuntimeError("550 rejected alice@example.com")
                super().send(to, subject, html_body, text_body)
        out = mr.process_queue(db, Flaky(), lambda c: {"subject": "s", "html": "h", "text": "t"})
        st = self._status(db)
        self.assertEqual(st["r1"]["status"], "error")
        self.assertNotIn("@example.com", st["r1"]["error_message"])
        self.assertIn("***", st["r1"]["error_message"])
        self.assertEqual(st["r2"]["status"], "sent")
        self.assertEqual(out["sent"], 1)

    def test_report_failure_marks_only_that_stock(self):
        db = self._db()
        def make(code):
            if code == "005930":
                raise RuntimeError("시세 정보가 없어 리포트를 만들 수 없습니다")
            return {"subject": "s", "html": "h", "text": "t"}
        db.emails["u3"] = "carol@example.com"
        out = mr.process_queue(db, mr.FakeSender(), make)
        st = self._status(db)
        self.assertEqual((st["r1"]["status"], st["r2"]["status"], st["r3"]["status"]), ("error", "error", "sent"))
        self.assertEqual(out, {"claimed": 3, "sent": 1, "error": 2})


class LangCheckTests(unittest.TestCase):
    def test_default_lang_check(self):
        self.assertTrue(mr.default_lang_check("반응 反应"))
        self.assertTrue(mr.default_lang_check("tương lai"))
        self.assertFalse(mr.default_lang_check("정상적인 한국어 문장 ABC 123"))


if __name__ == "__main__":
    unittest.main()
