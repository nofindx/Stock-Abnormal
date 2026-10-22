"""异动规则核心模块的最小回归测试。"""

from datetime import date, timedelta
import unittest

from .abnormal_rules import (
    BOARD_RULES,
    Board,
    Direction,
    PriceBar,
    calculate_deviation,
    calculate_trigger_price,
    detect_ordinary_abnormal,
    detect_severe_abnormal,
)


def make_bars(stock_closes, index_closes):
    """用连续日期构造测试日线。"""

    start = date(2026, 1, 1)
    return (
        [PriceBar(start + timedelta(days=i), value) for i, value in enumerate(stock_closes)],
        [PriceBar(start + timedelta(days=i), value) for i, value in enumerate(index_closes)],
    )


class AbnormalRulesTest(unittest.TestCase):
    """异动规则回归测试集合。"""

    def test_deviation_is_stock_return_minus_index_return(self):
        """偏离值应为股票涨跌幅减指数涨跌幅。"""

        stock, index = make_bars([100, 110], [100, 105])
        result = calculate_deviation(stock, index, 1)
        self.assertAlmostEqual(result.stock_return, 10.0)
        self.assertAlmostEqual(result.index_return, 5.0)
        self.assertAlmostEqual(result.deviation, 5.0)
        self.assertIs(result.direction, Direction.UP)

    def test_trigger_price_uses_index_return_plus_target_deviation(self):
        """触发价格公式应可反推回目标偏离值。"""

        result = calculate_trigger_price(100, 5, 100, 150, 10)
        self.assertAlmostEqual(result.trigger_price, 205.0)
        self.assertAlmostEqual(result.remaining_percent, 36.666667, places=5)

    def test_same_direction_thresholds_match_configured_board_rules(self):
        self.assertEqual(
            {board: BOARD_RULES[board].severe_same_direction_count for board in Board},
            {Board.MAIN: 4, Board.CHINEXT: 3, Board.STAR: 3, Board.BSE: 3},
        )

    def test_main_board_10_day_severe_threshold(self):
        """主板最近 10 个交易日偏离达到 100% 时应触发严重异常。"""

        stock, index = make_bars([100] + [200] * 9, [100] * 10)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertTrue(result.triggered)
        self.assertIsNotNone(result.severe_10d)
        self.assertAlmostEqual(result.severe_10d.deviation, 100.0)

    def test_main_board_three_day_window_uses_three_returns(self):
        """3 日普通异动必须覆盖 3 个收益区间，而不是只看最后 2 段。"""

        stock, index = make_bars([100.0, 107.0, 114.49, 122.5], [100.0] * 4)
        records = detect_ordinary_abnormal(stock, index, Board.MAIN)
        self.assertTrue(records)
        self.assertEqual(records[-1].window_days, 3)
        self.assertGreaterEqual(records[-1].deviation, 20.0)

    def test_main_board_10_day_uses_only_intervals_ending_at_latest_close(self):
        """历史中途峰值已结束，不得继续作为当前严重异常结果。"""

        stock, index = make_bars([100, 100, 205] + [205] * 10, [100] * 13)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNone(result.severe_10d)

    def test_ten_day_rule_can_use_ten_daily_returns(self):
        """10 个交易日包括 10 个涨跌收益区间，起点是此前收盘价。"""

        closes = [100.0 * (1.08 ** day) for day in range(11)]
        stock, index = make_bars(closes, [100.0] * len(closes))
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNotNone(result.severe_10d)
        self.assertEqual(result.severe_10d.window_days, 10)

    def test_main_board_10_day_down_triggers_downside_severe_threshold(self):
        """单股规则按主板 10 日下跌阈值识别严重异常。"""

        stock, index = make_bars([100] + [50] * 9, [100] * 10)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNotNone(result.severe_10d_down)
        self.assertTrue(result.triggered)

    def test_main_board_downside_ordinary_events_count_separately(self):
        """下跌普通异动与上涨异动分开计数。"""

        stock, index = make_bars([100] + [50.01] * 9, [100] * 10)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNone(result.severe_10d)
        self.assertEqual(result.down_count, 1)

    def test_continuous_ordinary_windows_count_separately(self):
        """每个达标的重叠三日窗口分别计次。"""

        stock, index = make_bars([100.0 * (1.1 ** day) for day in range(7)], [100.0] * 7)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertEqual(result.same_direction_count, 4)

    def test_each_qualifying_window_uses_its_confirmation_date_in_rolling_ten_days(self):
        """同向次数按各达标窗口确认日落入最近十个交易日统计。"""

        stock, index = make_bars([100.0 * (1.08 ** day) for day in range(16)], [100.0] * 16)
        result = detect_severe_abnormal(stock, index, Board.MAIN)

        self.assertEqual(result.same_direction_count, 10)
        self.assertEqual(len(result.same_direction_events), 10)
        self.assertEqual(result.same_direction_events[0].start_date, date(2026, 1, 5))
        self.assertEqual(result.same_direction_events[-1].end_date, date(2026, 1, 16))

    def test_same_direction_result_exposes_auditable_events(self):
        """同向次数必须能由起止日期、方向和偏离明细复核。"""
        stock, index = make_bars([100.0 * (1.1 ** day) for day in range(5)], [100.0] * 5)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertEqual(len(result.same_direction_events), 2)
        self.assertEqual(result.same_direction_events[0].start_date, date(2026, 1, 2))
        self.assertIs(result.same_direction_events[0].direction, Direction.UP)
        self.assertGreaterEqual(result.same_direction_events[0].deviation, 20.0)

    def test_negative_ordinary_deviation_is_a_downside_abnormal_record(self):
        """单股完整规则保留达到普通阈值的下跌异动记录。"""

        stock, index = make_bars([100, 75, 75, 75], [100, 100, 100, 100])
        records = detect_ordinary_abnormal(stock, index, Board.MAIN)
        self.assertEqual(len(records), 1)
        self.assertIs(records[0].direction, Direction.DOWN)

    def test_main_board_30_day_down_triggers_downside_severe_threshold(self):
        """单股规则按主板 30 日下跌阈值识别严重异常。"""

        stock, index = make_bars([100] + [30] * 29, [100] * 30)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNotNone(result.severe_30d_down)
        self.assertTrue(result.triggered)

    def test_main_board_30_day_up_threshold_is_plus_200_percent(self):
        """上涨达到 +200% 时触发 30 日严重异常。"""

        stock, index = make_bars([100] + [300] * 29, [100] * 30)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNotNone(result.severe_30d)
        self.assertAlmostEqual(result.severe_30d.deviation, 200.0)

    def test_main_board_turnover_condition_is_ordinary_abnormal(self):
        """主板 3 日换手率条件应生成普通异动记录，但不计入同向次数。"""

        start = date(2026, 1, 1)
        turnover = [0.3] * 5 + [10.0] * 3
        stock = [
            PriceBar(start + timedelta(days=i), 100.0, value)
            for i, value in enumerate(turnover)
        ]
        index = [PriceBar(start + timedelta(days=i), 100.0) for i in range(8)]
        records = detect_ordinary_abnormal(stock, index, Board.MAIN)
        turnover_records = [item for item in records if not item.counts_for_same_direction]
        self.assertTrue(turnover_records)
        self.assertEqual(turnover_records[-1].direction, Direction.NONE)

    def test_bse_uses_current_exchange_thresholds(self):
        """北交所应使用 2026-07-06 生效的独立方向阈值。"""

        stock, index = make_bars([100] + [250] * 9, [100] * 10)
        result = detect_severe_abnormal(stock, index, Board.BSE)
        self.assertIsNotNone(result.severe_10d)
        self.assertAlmostEqual(result.severe_10d.deviation, 150.0)

        stock, index = make_bars([100] + [25] * 29, [100] * 30)
        result = detect_severe_abnormal(stock, index, Board.BSE)
        self.assertIsNone(result.severe_30d)
