"""A 股交易异动和严重异常波动的纯计算模块。

本模块只依赖标准库，不访问网络，也不依赖 Tushare。数据获取由上层数据源适配器负责，
这样可以使用同一套规则计算实时结果、历史回放和单元测试结果。

规则依据：
1. 上海证券交易所《上海证券交易所交易规则》及科创板相关交易规则。
2. 深圳证券交易所《深圳证券交易所交易规则》及创业板相关交易规则。
3. 北京证券交易所交易规则。

交易所规则会发生修订。本模块将阈值集中在 BoardRule，正式上线前应以当日交易所公告复核。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Iterable, List, Optional, Sequence, Tuple


class Board(str, Enum):
    """股票所属板块，用于选择普通异动阈值和严重异动次数阈值。"""

    MAIN = "主板"
    CHINEXT = "创业板"
    STAR = "科创板"
    BSE = "北交所"


class Direction(str, Enum):
    """异动方向；零表示没有方向，不参与同向次数统计。"""

    UP = "上涨"
    DOWN = "下跌"
    NONE = "无方向"


class RiskLevel(str, Enum):
    """页面使用的风险等级。"""

    SAFE = "安全"
    WATCH = "关注"
    HIGH = "高危"


@dataclass(frozen=True)
class SevereThreshold:
    """严重异常波动的方向化阈值，百分比数值。"""

    up: float  # 上涨方向阈值，例如 100 表示 +100%。
    down: float  # 下跌方向阈值，例如 -50 表示 -50%。

    def triggered(self, deviation: float) -> bool:
        """判断偏离值是否达到对应方向的监管线。"""

        return deviation >= self.up or deviation <= self.down


@dataclass(frozen=True)
class BoardRule:
    """一个板块的异动规则参数。

    字段说明：
    - ordinary_days：普通异动使用的连续交易日数量。
    - ordinary_deviation：普通异动累计收盘价偏离阈值，使用百分比数值，例如 20 表示 20%。
    - severe_10d_deviation：10 个交易日严重异常波动阈值。
    - severe_30d_deviation：30 个交易日回看窗口的严重异常波动阈值；它不是公告后的监管期长度。
    - severe_same_direction_count：10 个交易日内同向普通异动次数阈值。
    - limit_price_ratio：用于需要涨跌停价格估算时的默认涨跌停幅度。
    - severe_10d_threshold / severe_30d_threshold：上涨和下跌分开的严重异常阈值。
    """

    ordinary_days: int
    ordinary_deviation: float
    severe_10d_deviation: float
    severe_30d_deviation: float
    severe_same_direction_count: int
    limit_price_ratio: float
    severe_10d_threshold: SevereThreshold = SevereThreshold(100.0, -50.0)  # 10 日方向化阈值。
    severe_30d_threshold: SevereThreshold = SevereThreshold(200.0, -70.0)  # 30 日方向化阈值。
    turnover_ratio_multiple: Optional[float] = None  # 普通换手率条件的倍数。
    turnover_sum_threshold: Optional[float] = None  # 普通换手率条件的窗口累计百分比。


# 普通 A 股口径：主板连续 3 个交易日累计偏离 ±20%；创业板和科创板为 ±30%。
# 北交所按《北京证券交易所交易规则》（2026-07-06 生效）第 5.4.2 至 5.4.5 条配置。
# 3 日普通异动为 ±40%；10 日、30 日严重异常为 +150%/-60%、+300%/-75%。
BOARD_RULES = {
    Board.MAIN: BoardRule(
        3, 20.0, 100.0, 200.0, 4, 10.0,
        turnover_ratio_multiple=30.0, turnover_sum_threshold=20.0,
    ),
    Board.CHINEXT: BoardRule(3, 30.0, 100.0, 200.0, 3, 20.0),
    Board.STAR: BoardRule(3, 30.0, 100.0, 200.0, 3, 20.0),
    Board.BSE: BoardRule(
        3, 40.0, 150.0, 300.0, 3, 30.0,
        severe_10d_threshold=SevereThreshold(150.0, -60.0),
        severe_30d_threshold=SevereThreshold(300.0, -75.0),
    ),
}


@dataclass(frozen=True)
class PriceBar:
    """股票或指数的日线收盘数据，日期必须按升序传入。"""

    trade_date: date  # 交易日期。
    close: float  # 收盘价；必须大于 0。
    turnover_rate: Optional[float] = None  # 换手率百分比；没有该字段时为 None。


@dataclass(frozen=True)
class DeviationResult:
    """某个交易区间的股票相对指数累计偏离结果。"""

    window_days: int  # 区间交易日数量，例如 10 或 30。
    start_date: date  # 区间起始日期。
    end_date: date  # 区间结束日期。
    stock_return: float  # 股票区间涨跌幅，百分比数值。
    index_return: float  # 指数区间涨跌幅，百分比数值。
    deviation: float  # 股票涨跌幅减指数涨跌幅，百分比数值。
    direction: Direction  # 偏离方向。


@dataclass(frozen=True)
class OrdinaryAbnormal:
    """一次连续交易日普通异动记录。"""

    start_date: date  # 异动区间起始日。
    end_date: date  # 异动区间结束日。
    window_days: int  # 当前记录的连续交易日数量。
    deviation: float  # 区间累计偏离值，百分比数值。
    direction: Direction  # 上涨或下跌方向。
    trigger_reason: str = "偏离值达到普通异动阈值"  # 触发原因，供页面展示。
    counts_for_same_direction: bool = True  # 换手率触发时为 False，不计入同向次数。


@dataclass(frozen=True)
class SevereAbnormalResult:
    """一只股票当前是否触发严重异常波动，以及触发原因。"""

    severe_10d: Optional[DeviationResult]  # 最近 10 个交易日是否达到当前板块的方向阈值。
    severe_30d: Optional[DeviationResult]  # 最近 30 个交易日回看窗口是否达到当前板块的方向阈值。
    same_direction_count: int  # 最近 10 个交易日内同向普通异动次数。
    same_direction_threshold: int  # 当前板块对应的同向次数阈值。
    same_direction: Direction  # 统计得到的主要方向。
    up_count: int = 0  # 最近 10 个交易日内上涨方向普通异动次数。
    down_count: int = 0  # 最近 10 个交易日内下跌方向普通异动次数。

    @property
    def triggered(self) -> bool:
        """只要三类严重异常条件任一满足，就认为当前触发。"""

        return bool(
            self.severe_10d
            or self.severe_30d
            or self.same_direction_count >= self.same_direction_threshold
        )


@dataclass(frozen=True)
class TriggerPrice:
    """达到目标偏离阈值所需的估算价格。"""

    window_days: int  # 使用的偏离计算窗口。
    target_deviation: float  # 目标偏离值，百分比数值。
    base_price: float  # 计算区间起点价格。
    index_return: float  # 区间指数涨跌幅，百分比数值。
    trigger_price: float  # 达到目标偏离值的理论价格。
    remaining_percent: float  # 当前价格距离触发价格的百分比；已超过则为负数。


@dataclass(frozen=True)
class PredictionPoint:
    """未来一个交易日的监管线预测。"""

    offset: int  # 相对当前交易日的交易日序号，1 表示 T+1。
    trade_date: date  # 预计交易日期。
    target_deviation: float  # 需要达到的目标偏离值。
    projected_index_return: float  # 截至该日假设的指数累计涨跌幅。
    trigger_price: float  # 理论触发价格。


def _validate_bars(stock_bars: Sequence[PriceBar], index_bars: Sequence[PriceBar]) -> None:
    """校验股票和指数序列的日期、价格和长度。"""

    if len(stock_bars) != len(index_bars):
        raise ValueError("股票和指数日线长度必须一致")
    if len(stock_bars) < 2:
        raise ValueError("至少需要两个交易日的收盘数据")
    stock_dates = [item.trade_date for item in stock_bars]
    index_dates = [item.trade_date for item in index_bars]
    if stock_dates != index_dates:
        raise ValueError("股票和指数交易日期必须一一对应")
    if stock_dates != sorted(stock_dates):
        raise ValueError("交易日期必须按升序传入")
    if any(item.close <= 0 for item in stock_bars + index_bars):
        raise ValueError("收盘价必须大于 0")


def interval_return(start_close: float, end_close: float) -> float:
    """计算区间涨跌幅，返回百分比数值而非小数。"""

    if start_close <= 0 or end_close <= 0:
        raise ValueError("价格必须大于 0")
    return (end_close / start_close - 1.0) * 100.0


def calculate_deviation(
    stock_bars: Sequence[PriceBar],
    index_bars: Sequence[PriceBar],
    window_days: int,
) -> DeviationResult:
    """按交易所常用口径计算区间偏离值。

    N 个交易日的区间需要 N+1 个有效收盘价，使用区间第一天收盘价作为起点，
    使用最后一天收盘价作为终点。偏离值 = 股票区间涨跌幅 - 指数同期涨跌幅。
    """

    _validate_bars(stock_bars, index_bars)
    if window_days <= 0:
        raise ValueError("窗口交易日数量必须大于 0")
    if len(stock_bars) < window_days + 1:
        raise ValueError("有效收盘价数量不足，N 日窗口至少需要 N+1 条数据")
    stock_window = stock_bars[-(window_days + 1) :]
    index_window = index_bars[-(window_days + 1) :]
    stock_return = interval_return(stock_window[0].close, stock_window[-1].close)
    index_return = interval_return(index_window[0].close, index_window[-1].close)
    deviation = stock_return - index_return
    direction = Direction.UP if deviation > 0 else Direction.DOWN if deviation < 0 else Direction.NONE
    return DeviationResult(
        window_days=window_days,
        start_date=stock_window[0].trade_date,
        end_date=stock_window[-1].trade_date,
        stock_return=stock_return,
        index_return=index_return,
        deviation=deviation,
        direction=direction,
    )


def calculate_trigger_price(
    base_price: float,
    index_return: float,
    target_deviation: float,
    current_price: float,
    window_days: int,
) -> TriggerPrice:
    """根据指数区间涨跌幅反推达到目标偏离值所需的理论价格。

    公式：目标股票涨跌幅 = 同期指数涨跌幅 + 目标偏离值；
    触发价格 = 区间起点价格 * (1 + 目标股票涨跌幅 / 100)。
    """

    if base_price <= 0 or current_price <= 0:
        raise ValueError("价格必须大于 0")
    target_stock_return = index_return + target_deviation
    trigger_price = base_price * (1.0 + target_stock_return / 100.0)
    remaining_percent = (trigger_price / current_price - 1.0) * 100.0
    return TriggerPrice(
        window_days=window_days,
        target_deviation=target_deviation,
        base_price=base_price,
        index_return=index_return,
        trigger_price=trigger_price,
        remaining_percent=remaining_percent,
    )


def detect_ordinary_abnormal(
    stock_bars: Sequence[PriceBar],
    index_bars: Sequence[PriceBar],
    board: Board,
) -> List[OrdinaryAbnormal]:
    """扫描全部连续窗口，返回达到普通异动阈值的记录。"""

    _validate_bars(stock_bars, index_bars)
    rule = BOARD_RULES[board]
    records: List[OrdinaryAbnormal] = []
    for end in range(rule.ordinary_days, len(stock_bars)):
        window_stock = stock_bars[end - rule.ordinary_days : end + 1]
        window_index = index_bars[end - rule.ordinary_days : end + 1]
        result = calculate_deviation(window_stock, window_index, rule.ordinary_days)
        if abs(result.deviation) >= rule.ordinary_deviation:
            records.append(
                OrdinaryAbnormal(
                    start_date=result.start_date,
                    end_date=result.end_date,
                    window_days=rule.ordinary_days,
                    deviation=result.deviation,
                    direction=result.direction,
                    trigger_reason="连续3个交易日累计收盘价涨跌幅偏离值达到普通异动阈值",
                )
            )
        if _turnover_abnormal(window_end=end, stock_bars=stock_bars, rule=rule):
            # 换手率条件本身没有涨跌方向，因此不参与同向次数统计。
            records.append(
                OrdinaryAbnormal(
                    start_date=stock_bars[end - rule.ordinary_days + 1].trade_date,
                    end_date=stock_bars[end].trade_date,
                    window_days=rule.ordinary_days,
                    deviation=result.deviation,
                    direction=Direction.NONE,
                    trigger_reason="连续3日均换手率/前5日均换手率达到30倍且3日累计换手率达到20%",
                    counts_for_same_direction=False,
                )
            )
    return records


def _turnover_abnormal(
    window_end: int,
    stock_bars: Sequence[PriceBar],
    rule: BoardRule,
) -> bool:
    """按交易所普通异常规则检查主板换手率条件。

    需要当前 3 个交易日和其前 5 个交易日的换手率；缺少换手率数据时返回 False，
    不把缺失数据误判为 0。
    """

    if rule.turnover_ratio_multiple is None or rule.turnover_sum_threshold is None:
        return False
    period_start = window_end - rule.ordinary_days + 1
    previous_start = period_start - 5
    if previous_start < 0:
        return False
    current = [item.turnover_rate for item in stock_bars[period_start : window_end + 1]]
    previous = [item.turnover_rate for item in stock_bars[previous_start:period_start]]
    if any(value is None for value in current + previous):
        return False
    current_values = [float(value) for value in current]
    previous_values = [float(value) for value in previous]
    previous_average = sum(previous_values) / len(previous_values)
    if previous_average <= 0:
        return False
    return (
        sum(current_values) / len(current_values) / previous_average >= rule.turnover_ratio_multiple
        and sum(current_values) >= rule.turnover_sum_threshold
    )


def _count_same_direction(records: Iterable[OrdinaryAbnormal]) -> Tuple[int, Direction, int, int]:
    """统计窗口内同向普通异动段，重叠窗口只计一次。

    交易所的 3 日异动是滚动窗口。连续上涨会让相邻窗口重复覆盖同一段
    行情；只有新的窗口不再与上一段重叠时，才算新的同向异动次数。
    """

    directional_records = sorted(
        (item for item in records if item.counts_for_same_direction and item.direction is not Direction.NONE),
        key=lambda item: (item.end_date, item.start_date),
    )
    up_count = down_count = 0
    previous = None
    for item in directional_records:
        if previous is not None and item.direction is previous.direction and item.start_date <= previous.end_date:
            continue
        if item.direction is Direction.UP:
            up_count += 1
        elif item.direction is Direction.DOWN:
            down_count += 1
        previous = item
    if up_count == 0 and down_count == 0:
        return 0, Direction.NONE, 0, 0
    if up_count >= down_count:
        return up_count, Direction.UP, up_count, down_count
    return down_count, Direction.DOWN, up_count, down_count


def _best_effective_deviation(
    stock_bars: Sequence[PriceBar],
    index_bars: Sequence[PriceBar],
    max_window: int,
) -> Optional[DeviationResult]:
    """在 N、N-1、N-2 个有效交易日中选择当前端点的偏离值。

    交易所的 10/30 日是回看上限；少 1～2 个有效共同交易日可以来自
    首日边界、停牌或指数对齐缺口，但不能退化成任意 3 日窗口。
    """

    _validate_bars(stock_bars, index_bars)
    upper = min(max_window, len(stock_bars) - 1)
    lower = max(3, max_window - 2)
    if upper < lower:
        return None
    results = [
        calculate_deviation(stock_bars, index_bars, window)
        for window in range(lower, upper + 1)
    ]
    positive = [item for item in results if item.deviation > 0]
    if positive:
        return max(positive, key=lambda item: (item.deviation, -item.window_days))
    return min(results, key=lambda item: (item.deviation, item.window_days))


def detect_severe_abnormal(
    stock_bars: Sequence[PriceBar],
    index_bars: Sequence[PriceBar],
    board: Board,
) -> SevereAbnormalResult:
    """检查最近 10 日、30 日偏离值和同向普通异动次数。"""

    _validate_bars(stock_bars, index_bars)
    rule = BOARD_RULES[board]
    severe_10d = None
    severe_30d = None
    result_10d = _best_effective_deviation(stock_bars, index_bars, 10)
    if result_10d is not None and rule.severe_10d_threshold.triggered(result_10d.deviation):
        severe_10d = result_10d
    result_30d = _best_effective_deviation(stock_bars, index_bars, 30)
    if result_30d is not None and rule.severe_30d_threshold.triggered(result_30d.deviation):
        severe_30d = result_30d
    ordinary_records = detect_ordinary_abnormal(stock_bars, index_bars, board)
    # 按最近 10 个交易日的日期窗口统计，不能按记录条数截断；同一窗口可能同时
    # 触发偏离值和换手率两条普通异常记录。
    recent_start = stock_bars[-10].trade_date if len(stock_bars) >= 10 else stock_bars[0].trade_date
    recent_end = stock_bars[-1].trade_date
    recent_records = [
        item
        for item in ordinary_records
        if recent_start <= item.end_date <= recent_end
    ]
    same_count, same_direction, up_count, down_count = _count_same_direction(recent_records)
    return SevereAbnormalResult(
        severe_10d=severe_10d,
        severe_30d=severe_30d,
        same_direction_count=same_count,
        same_direction_threshold=rule.severe_same_direction_count,
        same_direction=same_direction,
        up_count=up_count,
        down_count=down_count,
    )


def predict_trigger_prices(
    last_close: float,
    base_price: float,
    target_deviation: float,
    future_dates: Sequence[date],
    projected_index_daily_returns: Sequence[float],
    window_days: int,
) -> List[PredictionPoint]:
    """按给定的指数日涨跌幅假设生成 T+1 至 T+N 触发价格预测。

    这里的指数涨跌幅是模型输入，不是本函数预测的结果。调用方可以传入历史均值、
    当前指数期货隐含值或中性 0 值。默认预测必须在结果中标注为规则辅助计算，
    不能表述成确定性行情预测。
    """

    if last_close <= 0 or base_price <= 0:
        raise ValueError("价格必须大于 0")
    if len(future_dates) != len(projected_index_daily_returns):
        raise ValueError("未来日期和指数预测涨跌幅长度必须一致")
    points: List[PredictionPoint] = []
    index_return = 0.0
    for offset, (trade_date, daily_return) in enumerate(
        zip(future_dates, projected_index_daily_returns), start=1
    ):
        index_return = (1.0 + index_return / 100.0) * (1.0 + daily_return / 100.0) * 100.0 - 100.0
        trigger = calculate_trigger_price(
            base_price=base_price,
            index_return=index_return,
            target_deviation=target_deviation,
            current_price=last_close,
            window_days=window_days,
        )
        points.append(
            PredictionPoint(
                offset=offset,
                trade_date=trade_date,
                target_deviation=target_deviation,
                projected_index_return=index_return,
                trigger_price=trigger.trigger_price,
            )
        )
    return points
