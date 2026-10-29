import unittest
import json
from pathlib import Path

from .vnext_replay import replay_reset_scope, replay_same_direction


class VNextReplayTests(unittest.TestCase):
    def test_checked_in_fixture_replays_to_expected_result(self):
        path = Path(__file__).parents[1] / "fixtures" / "vnext" / "replay_same_direction.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        result = replay_same_direction(
            fixture["events"], target_date=fixture["target_date"],
            window_dates=fixture["window_dates"], threshold=fixture["threshold"],
            boundaries=fixture["boundaries"],
        )
        for key, value in fixture["expected"].items():
            self.assertEqual(result[key], value)

    def test_duplicate_sources_count_one_and_window_is_by_fact_end_date(self):
        events = [
            {"fact_id": "a", "fact_end_date": "20261020", "direction": "up"},
            {"fact_id": "a", "fact_end_date": "20261020", "direction": "up"},
            {"fact_id": "b", "fact_end_date": "20261021", "direction": "up"},
            {"fact_id": "old", "fact_end_date": "20261001", "direction": "up"},
        ]
        result = replay_same_direction(events, target_date="20261022", window_dates=["20261013", "20261014", "20261015", "20261016", "20261017", "20261020", "20261021", "20261022"], threshold=3)
        self.assertEqual(result["up_count"], 2)
        self.assertFalse(result["up_triggered"])
        self.assertEqual(result["event_ids"], ["a", "b"])

    def test_severe_boundary_clears_prior_events_but_ordinary_does_not(self):
        events = [
            {"fact_id": "old", "fact_end_date": "20261020", "direction": "up"},
            {"fact_id": "new", "fact_end_date": "20261021", "direction": "up"},
        ]
        window = ["20261020", "20261021", "20261022"]
        ordinary = [{"effective_date": "20261021", "severity": "ordinary"}]
        severe = [{"effective_date": "20261021", "severity": "severe"}]
        self.assertEqual(replay_same_direction(events, target_date="20261022", window_dates=window, threshold=2, boundaries=ordinary)["up_count"], 2)
        self.assertEqual(replay_same_direction(events, target_date="20261022", window_dates=window, threshold=2, boundaries=severe)["up_count"], 1)
        self.assertEqual(replay_reset_scope(ordinary, "20261022"), ("ordinary_deviation_3d",))
        self.assertEqual(len(replay_reset_scope(severe, "20261022")), 4)

    def test_pending_direction_makes_exact_count_unknown(self):
        result = replay_same_direction(
            [{"fact_id": "pending", "fact_end_date": "20261021", "direction": None}],
            target_date="20261022", window_dates=["20261021", "20261022"], threshold=3,
        )
        self.assertIsNone(result["exact_count"])
        self.assertEqual(result["pending_event_ids"], ["pending"])

    def test_pause_resume_policy_reset_scopes(self):
        ordinary_pause = [{"effective_date": "20261021", "reset_type": "ordinary_pause_resume"}]
        severe_pause = [{"effective_date": "20261021", "reset_type": "severe_pause_resume"}]
        self.assertEqual(replay_reset_scope(ordinary_pause, "20261022"), ("ordinary_deviation_3d", "severe_deviation_10d", "severe_deviation_30d"))
        self.assertEqual(len(replay_reset_scope(severe_pause, "20261022")), 4)

    def test_product_policy_fixture_is_replayed(self):
        path = Path(__file__).parents[1] / "fixtures" / "vnext" / "product_policy_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        by_id = {case["id"]: case for case in cases}
        window = ["20261020", "20261021", "20261022"]
        ordinary = by_id["F-Q2-ORDINARY-COUNT-RESET"]
        result = replay_same_direction(ordinary["input"]["events"], target_date="20261022", window_dates=window, threshold=3, boundaries=[ordinary["input"]["boundary"]])
        self.assertEqual(result["up_count"], ordinary["expected"]["up_count"])
        self.assertEqual(list(replay_reset_scope([ordinary["input"]["boundary"]], "20261022")), ordinary["expected"]["reset_scope"])
        severe = by_id["F-Q2-SEVERE-RESET-D1"]
        severe_result = replay_same_direction(severe["input"]["events"], target_date="20261022", window_dates=window, threshold=3, boundaries=[severe["input"]["boundary"]])
        self.assertEqual(severe_result["up_count"], severe["expected"]["up_count"])
        pending = by_id["F-Q2-DIRECTION-PENDING"]
        pending_result = replay_same_direction(pending["input"]["events"], target_date="20261022", window_dates=window, threshold=3)
        self.assertIsNone(pending_result["exact_count"])


if __name__ == "__main__":
    unittest.main()
