import unittest

from server.core.abnormal_rules import BOARD_RULES as ENGINE_RULES
from server.rules.catalog import BOARD_RULES, RESET_POLICIES, RULE_DEFINITIONS, Board


class RuleCatalogTests(unittest.TestCase):
    def test_calculation_core_uses_the_single_rule_catalog(self):
        self.assertIs(ENGINE_RULES, BOARD_RULES)

    def test_all_supported_boards_have_independent_rule_definitions(self):
        self.assertEqual(set(BOARD_RULES), set(Board))
        self.assertEqual(BOARD_RULES[Board.MAIN].severe_same_direction_count, 4)
        self.assertEqual(BOARD_RULES[Board.BSE].severe_10d_threshold.up, 150.0)

    def test_all_four_rules_and_reset_scopes_are_defined_once(self):
        self.assertEqual(len(RULE_DEFINITIONS), 4)
        self.assertEqual(
            RESET_POLICIES["ordinary_abnormality"]["reset_metrics"],
            ("ordinary_deviation_3d",),
        )
        self.assertEqual(
            set(RESET_POLICIES["severe_abnormality"]["reset_metrics"]),
            set(RULE_DEFINITIONS),
        )


if __name__ == "__main__":
    unittest.main()
