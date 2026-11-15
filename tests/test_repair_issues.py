# -*- coding: utf-8 -*-
"""scripts/repair_issues.py 종료 코드 로직 테스트. 실행:  python -m unittest tests.test_repair_issues -v

핵심: alert_only(진짜 뉴스 없음·네이버에 아직 실적 미반영 등 예상 가능한 미반영
상태)만 남으면 exit 0(실패 메일 없음), 실제 예외·오류·미해결 장애가 있으면 exit 1.

Supabase·네이버·Groq·KRX에 붙지 않는다 - 전부 가짜(Fake)/mock으로만 돈다.
이 저장소를 로컬에서 처음 받았을 때(패키지 미설치)도 돌도록, 세 가지 외부
패키지가 없으면 최소 스텁을 sys.modules에 넣고 임포트한다.
"""
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))


def _ensure(name, **attrs):
    """패키지가 설치돼 있으면 그대로, 없으면 최소 스텁을 만든다."""
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


_ensure("requests")
_ensure("bs4", BeautifulSoup=type("BeautifulSoup", (), {}))
_ensure("groq", Groq=type("Groq", (), {}),
        RateLimitError=type("RateLimitError", (Exception,), {}),
        APIStatusError=type("APIStatusError", (Exception,), {}))
_ensure("google")
_ensure("google.generativeai")
try:  # google.generativeai는 부모 패키지도 sys.modules에 있어야 import가 끝난다.
    import google.generativeai  # noqa: F401
except ImportError:  # pragma: no cover
    sys.modules["google"] = types.ModuleType("google")
    sys.modules["google.generativeai"] = types.ModuleType("google.generativeai")

import repair_issues as ri  # noqa: E402

ENV = {"SUPABASE_URL": "https://stub.invalid", "SUPABASE_SERVICE_ROLE_KEY": "stub", "GROQ_API_KEY": "stub"}

ANALYSIS_ROW = {"trade_date": "2026-11-13", "rank": 1, "ticker": "123456", "name": "가짜종목",
                "close": 10000, "change_pct": 12.3, "rise_reason": None, "chart_analysis": None}
FIN_ROW = {"trade_date": "2026-11-13", "rank": 1, "report_type": "daily", "ticker": "123456",
           "name": "가짜종목", "financials": None}


class LogRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, pipeline, trade_date, issue, action, success, detail=None):
        self.calls.append({"pipeline": pipeline, "trade_date": trade_date, "issue": issue,
                           "action": action, "success": success, "detail": detail or {}})

    def actions(self):
        return [c["action"] for c in self.calls]


class TestEmptyAnalysisRepair(unittest.TestCase):
    def _patch_common(self, logs, analyze_returns=("", ""), fetch_ohlcv=None):
        return [
            mock.patch.object(ri, "find_empty_analysis_rows", return_value=[dict(ANALYSIS_ROW)]),
            mock.patch.object(ri, "log_repair", logs),
            mock.patch.object(ri, "fetch_ohlcv", fetch_ohlcv or (lambda *a, **k: [])),
            mock.patch.object(ri, "calc_technicals", lambda *a, **k: {}),
            mock.patch.object(ri, "calc_ma_lines", lambda *a, **k: []),
            mock.patch.object(ri, "fetch_stock_news_staged", lambda *a, **k: ([], "none")),
            mock.patch.object(ri, "news_to_dicts", lambda *a, **k: []),
            mock.patch.object(ri, "analyze_stock", lambda *a, **k: analyze_returns),
        ]

    def test_retry_still_empty_is_alert_only_not_failure(self):
        """재시도해도 비면(진짜 뉴스 없음) alert_only - 실패로 보지 않는다."""
        logs = LogRecorder()
        patches = self._patch_common(logs)
        for p in patches:
            p.start()
        try:
            out = ri.repair_empty_analysis(client=mock.Mock())
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertTrue(out.hard_ok)
        self.assertEqual(out.alerts, 1)
        self.assertEqual(logs.actions(), ["alert_only"])

    def test_retry_fills_row_is_success(self):
        logs = LogRecorder()
        patches = self._patch_common(logs, analyze_returns=("상승이유", "차트분석"))
        patches.append(mock.patch.object(ri, "sb_patch_rows"))
        for p in patches:
            p.start()
        try:
            out = ri.repair_empty_analysis(client=mock.Mock())
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertTrue(out.hard_ok)
        self.assertEqual(out.alerts, 0)
        self.assertEqual(logs.actions(), ["analysis_retry"])
        self.assertTrue(logs.calls[0]["success"])

    def test_exception_during_retry_is_hard_failure(self):
        """재시도 자체가 터진 경우(네트워크·API 오류)는 예상된 미반영이 아니라 장애."""
        logs = LogRecorder()

        def boom(*a, **k):
            raise RuntimeError("네이버 응답 없음")

        patches = self._patch_common(logs, fetch_ohlcv=boom)
        for p in patches:
            p.start()
        try:
            out = ri.repair_empty_analysis(client=mock.Mock())
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertFalse(out.hard_ok)
        self.assertEqual(out.alerts, 0)
        self.assertEqual(logs.actions(), ["analysis_retry"])
        self.assertFalse(logs.calls[0]["success"])
        self.assertIn("네이버 응답 없음", logs.calls[0]["detail"]["error"])

    def test_no_empty_rows(self):
        with mock.patch.object(ri, "find_empty_analysis_rows", return_value=[]):
            out = ri.repair_empty_analysis(client=mock.Mock())
        self.assertTrue(out.hard_ok)
        self.assertEqual(out.alerts, 0)


class TestEmptyFinancialsRepair(unittest.TestCase):
    def test_naver_has_no_data_is_alert_only(self):
        logs = LogRecorder()
        with mock.patch.object(ri, "find_empty_financials_rows", return_value=[dict(FIN_ROW)]), \
                mock.patch.object(ri, "log_repair", logs), \
                mock.patch.object(ri, "fetch_financials", lambda *a, **k: None):
            out = ri.repair_empty_financials()
        self.assertTrue(out.hard_ok)
        self.assertEqual(out.alerts, 1)
        self.assertEqual(logs.actions(), ["alert_only"])

    def test_filled_financials_is_success(self):
        logs = LogRecorder()
        with mock.patch.object(ri, "find_empty_financials_rows", return_value=[dict(FIN_ROW)]), \
                mock.patch.object(ri, "log_repair", logs), \
                mock.patch.object(ri, "sb_patch_rows"), \
                mock.patch.object(ri, "fetch_financials", lambda *a, **k: {"per": 10.0}):
            out = ri.repair_empty_financials()
        self.assertTrue(out.hard_ok)
        self.assertEqual(out.alerts, 0)
        self.assertEqual(logs.actions(), ["financials_retry"])
        self.assertTrue(logs.calls[0]["success"])

    def test_exception_is_hard_failure(self):
        logs = LogRecorder()

        def boom(*a, **k):
            raise RuntimeError("네이버 재무 API 오류")

        with mock.patch.object(ri, "find_empty_financials_rows", return_value=[dict(FIN_ROW)]), \
                mock.patch.object(ri, "log_repair", logs), \
                mock.patch.object(ri, "fetch_financials", boom):
            out = ri.repair_empty_financials()
        self.assertFalse(out.hard_ok)
        self.assertEqual(out.alerts, 0)
        self.assertEqual(logs.actions(), ["financials_retry"])
        self.assertFalse(logs.calls[0]["success"])


class TestGapsAndRules(unittest.TestCase):
    def test_no_gaps_no_violations(self):
        with mock.patch.object(ri, "find_date_gaps", return_value=[]), \
                mock.patch.object(ri, "find_short_count_dates", return_value=[]):
            out = ri.repair_gaps_and_counts(groq_client=mock.Mock())
        self.assertTrue(out.hard_ok)
        self.assertEqual(out.alerts, 0)
        with mock.patch.object(ri, "find_rule_violation_dates", return_value=[]):
            out = ri.repair_rule_violations(groq_client=mock.Mock())
        self.assertTrue(out.hard_ok)

    def test_unfilled_gap_is_hard_failure(self):
        """아직 데이터가 없어 backfill이 조용히 skip한 경우는 실패로 남긴다(사람이 봐야 함)."""
        logs = LogRecorder()
        with mock.patch.object(ri, "find_date_gaps", return_value=["2026-11-13"]), \
                mock.patch.object(ri, "find_short_count_dates", return_value=[]), \
                mock.patch.object(ri, "backfill_date", lambda *a, **k: False), \
                mock.patch.object(ri, "sb_get", return_value=[]), \
                mock.patch.object(ri, "log_repair", logs):
            out = ri.repair_gaps_and_counts(groq_client=mock.Mock())
        self.assertFalse(out.hard_ok)
        self.assertEqual(logs.actions(), ["backfill_rerun"])
        self.assertFalse(logs.calls[0]["success"])

    def test_exception_in_gap_repair_is_hard_failure(self):
        logs = LogRecorder()

        def boom(*a, **k):
            raise RuntimeError("KRX API 오류")

        with mock.patch.object(ri, "find_date_gaps", return_value=["2026-11-13"]), \
                mock.patch.object(ri, "find_short_count_dates", return_value=[]), \
                mock.patch.object(ri, "backfill_date", boom), \
                mock.patch.object(ri, "log_repair", logs):
            out = ri.repair_gaps_and_counts(groq_client=mock.Mock())
        self.assertFalse(out.hard_ok)


class TestMainExitCode(unittest.TestCase):
    def _run_main(self, gaps, rules, analysis, financials):
        fake_groq = types.ModuleType("groq")
        fake_groq.Groq = lambda **kw: mock.Mock()
        patches = [
            mock.patch.dict(os.environ, ENV),
            mock.patch.dict(sys.modules, {"groq": fake_groq}),
            mock.patch.object(ri, "load_env"),
            mock.patch.object(ri, "repair_gaps_and_counts", return_value=gaps),
            mock.patch.object(ri, "repair_rule_violations", return_value=rules),
            mock.patch.object(ri, "repair_empty_analysis", return_value=analysis),
            mock.patch.object(ri, "repair_empty_financials", return_value=financials),
        ]
        for p in patches:
            p.start()
        buf = StringIO()
        try:
            with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
                ri.main()
        finally:
            for p in reversed(patches):
                p.stop()
        return cm.exception.code, buf.getvalue()

    def test_only_alert_only_exits_zero(self):
        """사용자가 요청한 핵심: alert_only만 남으면 exit 0 → GitHub Actions 성공 → 실패 메일 없음."""
        code, out = self._run_main(ri.RepairOutcome(True, 0), ri.RepairOutcome(True, 0),
                                   ri.RepairOutcome(True, 2), ri.RepairOutcome(True, 1))
        self.assertEqual(code, 0)
        self.assertIn("alert_only 3건", out)
        self.assertIn("빈분석=OK(alert_only 2)", out)

    def test_all_clear_exits_zero(self):
        code, out = self._run_main(ri.RepairOutcome(True, 0), ri.RepairOutcome(True, 0),
                                   ri.RepairOutcome(True, 0), ri.RepairOutcome(True, 0))
        self.assertEqual(code, 0)
        self.assertIn("모든 이슈가 자동으로 해결", out)

    def test_hard_failure_exits_one(self):
        code, out = self._run_main(ri.RepairOutcome(True, 0), ri.RepairOutcome(True, 0),
                                   ri.RepairOutcome(False, 1), ri.RepairOutcome(True, 0))
        self.assertEqual(code, 1)
        self.assertIn("빈분석=FAIL", out)

    def test_missing_env_exits_one(self):
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": "",
                                          "GROQ_API_KEY": ""}), \
                mock.patch.object(ri, "load_env"), \
                redirect_stdout(StringIO()), self.assertRaises(SystemExit) as cm:
            ri.main()
        self.assertEqual(cm.exception.code, 1)


class TestRepairOutcomeShape(unittest.TestCase):
    def test_all_repair_steps_return_outcome(self):
        """모든 수리 함수가 (hard_ok, alerts) 형태를 돌려주는지 - main의 합산 로직 전제."""
        with mock.patch.object(ri, "find_date_gaps", return_value=[]), \
                mock.patch.object(ri, "find_short_count_dates", return_value=[]), \
                mock.patch.object(ri, "find_rule_violation_dates", return_value=[]), \
                mock.patch.object(ri, "find_empty_analysis_rows", return_value=[]), \
                mock.patch.object(ri, "find_empty_financials_rows", return_value=[]):
            outs = [ri.repair_gaps_and_counts(mock.Mock()), ri.repair_rule_violations(mock.Mock()),
                    ri.repair_empty_analysis(mock.Mock()), ri.repair_empty_financials()]
        for out in outs:
            self.assertIsInstance(out, ri.RepairOutcome)
            self.assertIsInstance(out.hard_ok, bool)
            self.assertIsInstance(out.alerts, int)


if __name__ == "__main__":
    unittest.main()
