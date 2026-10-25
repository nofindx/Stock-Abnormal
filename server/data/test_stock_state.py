import unittest

from server.data.stock_state import build_stock_state


class StockStateTests(unittest.TestCase):
    def test_unavailable_data_never_becomes_safe(self):
        state = build_stock_state(
            {}, data_available=False, market_data_state="unavailable", data_error="公告覆盖不完整"
        )

        self.assertEqual(state["status"], "unknown")
        self.assertEqual(state["marketRiskState"], "unknown")
        self.assertEqual(state["dataState"], "unavailable")
        self.assertFalse(state["dataAvailable"])
        self.assertEqual(state["dataError"], "公告覆盖不完整")

    def test_announcement_failure_does_not_rewrite_market_axis(self):
        state = build_stock_state({}, data_available=False, facts_data_state="unavailable")

        self.assertEqual(state["marketRiskState"], "safe")
        self.assertEqual(state["factsDataState"], "unavailable")
        self.assertEqual(state["status"], "unknown")

    def test_severe_metric_has_priority_over_ordinary_metric(self):
        state = build_stock_state({"warnings": [
            {"title": "3 日偏离", "upTriggered": True},
            {"title": "10 日偏离", "upTriggered": True},
        ]})

        self.assertEqual(state["status"], "severe-risk")
        self.assertEqual(state["marketRiskState"], "severe-risk")
        self.assertEqual(state["regulatoryState"], "none")
        self.assertEqual(len(state["calculation"]["warnings"]), 2)

    def test_ordinary_metric_does_not_become_severe(self):
        state = build_stock_state({"warnings": [
            {"title": "3 日偏离", "upTriggered": True},
        ]})

        self.assertEqual(state["status"], "ordinary-risk")
        self.assertEqual(state["marketRiskState"], "ordinary-risk")

    def test_partial_market_history_is_not_reported_as_safe(self):
        state = build_stock_state({}, market_data_state="partial")

        self.assertEqual(state["marketRiskState"], "incomplete")
        self.assertEqual(state["status"], "unknown")
        self.assertEqual(state["dataState"], "partial")

    def test_partial_announcement_coverage_is_not_a_safe_summary(self):
        state = build_stock_state({}, facts_data_state="partial")

        self.assertEqual(state["marketRiskState"], "safe")
        self.assertEqual(state["status"], "unknown")
        self.assertEqual(state["factsDataState"], "partial")

    def test_delisting_and_suspension_facts_remain_distinct(self):
        delisting = build_stock_state({}, [{"riskTone": "delisting-warning"}])
        suspended = build_stock_state({}, [{"riskTone": "suspension"}])

        self.assertEqual(delisting["regulatoryState"], "delisting")
        self.assertEqual(delisting["status"], "delisting-warning")
        self.assertEqual(suspended["tradingState"], "suspended")
        self.assertEqual(suspended["status"], "suspended")

    def test_severe_fact_does_not_mutate_calculation(self):
        calculation = {"warnings": [{"title": "30 日偏离", "upTriggered": False}]}
        state = build_stock_state(calculation, [{"riskTone": "severe-30d"}])

        self.assertEqual(state["status"], "severe-monitoring")
        self.assertEqual(state["regulatoryState"], "severe-monitoring")
        self.assertEqual(state["calculation"], calculation)


if __name__ == "__main__":
    unittest.main()
