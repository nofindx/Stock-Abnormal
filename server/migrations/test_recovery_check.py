"""T2 evidence integrity checks, included in normal backend test discovery."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import unittest

from server.tools.recovery_check import (
    canonical, compare, digest, identifier, recovery_timing, table_digest,
)


class RecoveryEvidenceTests(unittest.TestCase):
    def inventory(self, rows):
        tables = {"fact": dict(table_digest(rows), schemaHash="a" * 64)}
        return {"format": "T2-INVENTORY-1", "tables": tables, "rootHash": digest(tables)}

    def test_order_and_mysql_json_format_do_not_create_false_difference(self):
        before = [{"id": 1, "payload": '{"a":1,"b":2}'}, {"id": 2, "payload": None}]
        after = [{"id": 2, "payload": None}, {"id": 1, "payload": '{"b": 2, "a": 1}'}]
        self.assertEqual(table_digest(before, ["payload"]), table_digest(after, ["payload"]))

    def test_null_zero_string_precision_and_duplicate_rows_are_distinct(self):
        samples = [[{"v": v}] for v in (None, 0, "0", Decimal("0.00000001"))]
        self.assertEqual(len({table_digest(x)["contentHash"] for x in samples}), 4)
        self.assertNotEqual(table_digest([{"v": 1}]), table_digest([{"v": 1}, {"v": 1}]))

    def test_mysql_native_values_can_be_hashed_without_float_loss(self):
        raw = canonical({"price": Decimal("100000000000.12345678"),
                         "date": datetime(2026, 11, 9, 9), "hash": b"\x00\xff"})
        self.assertIn("100000000000.12345678", raw)
        self.assertIn("00ff", raw)

    def test_equal_counts_do_not_hide_changed_fact_direction(self):
        result = compare(self.inventory([{"id": 1, "direction": "up"}]),
                         self.inventory([{"id": 1, "direction": "down"}]))
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["differences"][0]["table"], "fact")

    def test_missing_table_schema_or_row_fails_exact_restoration(self):
        before = self.inventory([{"id": 1}])
        for change in ("schema", "missing", "row"):
            after = deepcopy(before)
            if change == "schema":
                after["tables"]["fact"]["schemaHash"] = "b" * 64
            elif change == "missing":
                after["tables"]["extra"] = after["tables"].pop("fact")
            else:
                after["tables"]["fact"].update(table_digest([]))
            after["rootHash"] = digest(after["tables"])
            self.assertEqual(compare(before, after)["status"], "fail")

    def test_corrupt_or_empty_inventory_cannot_pass(self):
        value = self.inventory([{"id": 1}])
        other = deepcopy(value)
        other["tables"]["fact"]["rowCount"] = 2
        with self.assertRaises(ValueError):
            compare(value, other)
        with self.assertRaises(ValueError):
            compare({}, {})
        self.assertEqual(compare(value, value)["status"], "pass")

    def test_identifier_rejects_sql_injection(self):
        for value in ("t2;DROP", "x`", "stock-abnormal", "", "../prod"):
            with self.assertRaises(ValueError):
                identifier(value)

    def test_recovery_targets_count_detection_delay_in_rto(self):
        result = recovery_timing("2026-11-08T09:00:00+08:00", "2026-11-09T09:00:00+08:00",
                                 "2026-11-09T10:00:00+08:00", "2026-11-09T11:00:00+08:00")
        self.assertEqual(result["rpoSeconds"], 86400)
        self.assertEqual(result["rtoSeconds"], 7200)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(recovery_timing("2026-11-08T08:59:59+08:00", "2026-11-09T09:00:00+08:00",
                                        "2026-11-09T09:00:00+08:00", "2026-11-09T11:00:01+08:00")["status"], "fail")

    def test_naive_or_inverted_recovery_times_are_rejected(self):
        with self.assertRaises(ValueError):
            recovery_timing(*(["2026-11-09T09:00:00"] * 4))
        with self.assertRaises(ValueError):
            recovery_timing("2026-11-09T09:00:01Z", *(["2026-11-09T09:00:00Z"] * 3))


if __name__ == "__main__":
    unittest.main()
