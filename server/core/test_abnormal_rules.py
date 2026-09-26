"""异动规则核心模块的最小回归测试。"""

from datetime import date, timedelta
import unittest

from .abnormal_rules import (
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

    def test_main_board_10_day_severe_threshold(self):
        """主板最近 10 个交易日偏离达到 100% 时应触发严重异常。"""

        stock, index = make_bars([100] + [200] * 10, [100] * 11)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertTrue(result.triggered)
        self.assertIsNotNone(result.severe_10d)
        self.assertAlmostEqual(result.severe_10d.deviation, 100.0)

    def test_main_board_10_day_down_threshold_is_minus_50_percent(self):
        """下跌方向达到 -50% 即触发，不能错误使用对称 -100% 阈值。"""

        stock, index = make_bars([100] + [50] * 10, [100] * 11)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNotNone(result.severe_10d)
        self.assertAlmostEqual(result.severe_10d.deviation, -50.0)

    def test_main_board_10_day_down_below_threshold_does_not_trigger(self):
        """下跌未达到 -50% 时不应误判为 10 日严重异常。"""

        stock, index = make_bars([100] + [50.01] * 10, [100] * 11)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNone(result.severe_10d)

    def test_main_board_30_day_down_threshold_is_minus_70_percent(self):
        """下跌方向达到 -70% 即触发 30 日严重异常。"""

        stock, index = make_bars([100] + [30] * 30, [100] * 31)
        result = detect_severe_abnormal(stock, index, Board.MAIN)
        self.assertIsNotNone(result.severe_30d)
        self.assertAlmostEqual(result.severe_30d.deviation, -70.0)

    def test_main_board_30_day_up_threshold_is_plus_200_percent(self):
        """上涨达到 +200% 时触发 30 日严重异常。"""

        stock, index = make_bars([100] + [300] * 30, [100] * 31)
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
