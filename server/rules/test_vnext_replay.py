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


if __name__ == "__main__":
    unittest.main()
