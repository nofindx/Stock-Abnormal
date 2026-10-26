import unittest

from server.resets.decisions import build_reset_context, monitor_reset_date


def confirmed_fact(risk_tone, reset_date, code):
    return {
        "riskTone": risk_tone,
        "ruleResetConfirmed": True,
        "ruleResetDate": reset_date,
        "resetEvidence": {
            "code": code,
            "sourceType": "issuer-disclosure",
            "verifiedRiskTone": risk_tone,
            "sourceUrl": "https://www.cninfo.com.cn/example.pdf",
            "bodyHash": "a" * 64,
        },
    }


class ResetDecisionTests(unittest.TestCase):
    def test_only_confirmed_official_evidence_has_a_reset_date(self):
        ordinary = confirmed_fact("ordinary-abnormality", "20261023", "official_ordinary_abnormality_body")
        pending = dict(ordinary, ruleResetConfirmed=False)

        self.assertEqual(monitor_reset_date(ordinary), "20261023")
        self.assertEqual(monitor_reset_date(pending), "")

    def test_ordinary_and_severe_scopes_remain_separate(self):
        ordinary = confirmed_fact("ordinary-abnormality", "20261021", "official_ordinary_abnormality_body")
        severe = confirmed_fact("severe-10d", "20261023", "official_severe_abnormality_body")
        context = build_reset_context([ordinary, severe])

        self.assertEqual(context.ordinary_reset_dates, ["20261021"])
        self.assertEqual(context.severe_reset_dates, ["20261023"])
        self.assertEqual(context.latest_reset_scope, "severe")
        self.assertEqual(context.latest_reset_date, "20261023")

    def test_risk_warning_fact_does_not_reset_metrics(self):
        context = build_reset_context([{"riskTone": "ordinary", "ruleResetConfirmed": True}])

        self.assertEqual(context.ordinary_reset_dates, [])
        self.assertEqual(context.severe_reset_dates, [])
        self.assertEqual(context.latest_reset_date, "")

    def test_ordinary_event_passes_body_identity_to_calculation_context(self):
        fact = confirmed_fact("ordinary-abnormality", "20261017", "official_ordinary_abnormality_body")
        fact.update({
            "sourceId": "notice-a",
            "symbol": "002696",
            "eventDate": "20261016",
            "eventDirection": "up",
            "contentHash": "b" * 64,
            "countsForSameDirection": True,
        })

        context = build_reset_context([fact])

        self.assertEqual(context.ordinary_events[0]["contentHash"], "b" * 64)
        self.assertEqual(context.ordinary_events[0]["bodyHash"], "a" * 64)
        self.assertEqual(context.ordinary_events[0]["symbol"], "002696")


if __name__ == "__main__":
    unittest.main()
