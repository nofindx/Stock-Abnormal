"""业务层共享数据结构。

这些结构是数据源适配器、计算服务和微信小程序接口之间的统一契约。
字段名使用英文，字段注释和文档使用中文，便于前后端长期维护。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from .abnormal_rules import Board, Direction, RiskLevel


class MonitorType(str, Enum):
    """监控池的三类业务类型。"""

    ORDINARY = "ordinary"  # 普通交易所风险提示。
    SEVERE_10D = "severe_10d"  # 10 个交易日严重异常。
    SEVERE_30D = "severe_30d"  # 30 个交易日严重异常。


@dataclass(frozen=True)
class StockInfo:
    """股票基础资料。"""

    ts_code: str  # Tushare 股票代码，例如 600519.SH。
    symbol: str  # 纯数字股票代码，例如 600519。
    name: str  # 股票简称。
    market: str  # 上市市场，例如 SSE、SZSE、BSE。
    board: Board  # 所属板块，用于选择异动规则。
    is_st: bool  # 是否为 ST 或 *ST 股票。
    list_date: Optional[date] = None  # 上市日期；新股规则判断需要该字段。
    delist_date: Optional[date] = None  # 退市日期；退市整理期可据此识别。


@dataclass(frozen=True)
class DailyMarketBar:
    """股票或指数的标准化日行情。"""

    ts_code: str  # 标的代码。
    trade_date: date  # 交易日期。
    open: Optional[float]  # 开盘价。
    high: Optional[float]  # 最高价。
    low: Optional[float]  # 最低价。
    close: float  # 收盘价，也是异动计算的核心字段。
    pre_close: Optional[float]  # 前收盘价。
    change: Optional[float]  # 涨跌额。
    pct_chg: Optional[float]  # 涨跌幅，百分比数值。
    vol: Optional[float]  # 成交量。
    amount: Optional[float]  # 成交额。
    adj_factor: Optional[float] = None  # 复权因子；监管口径通常优先使用实际收盘价。


@dataclass(frozen=True)
class DataQuality:
    """一次计算输入的数据质量结果。"""

    source: str  # 数据源名称，例如 tushare、akshare、baostock。
    fetched_at: datetime  # 数据抓取时间。
    latest_trade_date: Optional[date]  # 数据源返回的最新交易日。
    is_complete: bool  # 是否覆盖计算所需的全部交易日。
    missing_dates: List[date] = field(default_factory=list)  # 缺失的交易日期。
    warnings: List[str] = field(default_factory=list)  # 不阻断计算但需要展示或记录的警告。


@dataclass(frozen=True)
class DeviationSnapshot:
    """股票当前的 10 日和 30 日偏离快照。"""

    ts_code: str  # 股票代码。
    trade_date: date  # 计算对应的最新交易日。
    ten_day_deviation: Optional[float]  # 10 日 100% 窗口偏离值。
    thirty_day_deviation: Optional[float]  # 30 日 200% 窗口偏离值。
    ten_day_trigger_price: Optional[float]  # 10 日目标线理论触发价。
    thirty_day_trigger_price: Optional[float]  # 30 日目标线理论触发价。
    risk_level: RiskLevel  # 当前风险等级。
    data_quality: DataQuality  # 计算输入的数据质量。


@dataclass(frozen=True)
class AbnormalMonitorRecord:
    """当前或历史监管监控记录。"""

    record_id: str  # 记录唯一标识。
    ts_code: str  # 股票代码。
    stock_name: str  # 股票名称，减少小程序重复查询。
    start_date: date  # 监管期开始日期。
    end_date: Optional[date]  # 监管期结束日期；当前监管记录为空。
    status: str  # current、pending 或 history。
    direction: Direction  # 上涨方向或下跌方向。
    source: str  # 记录来源，例如交易所公告、Tushare。
    source_url: Optional[str] = None  # 公告原文链接，允许为空。
    monitor_type: MonitorType = MonitorType.ORDINARY  # 监控池三类中的一种。
    rule_text: str = ""  # 页面展示的完整中文规则说明。
    remaining_days: Optional[int] = None  # 剩余监控交易日；历史记录为空。
    risk_warning: str = ""  # 面向用户的风险提示文本。


@dataclass(frozen=True)
class PredictionRecord:
    """当日或次日预警记录。"""

    ts_code: str  # 股票代码。
    stock_name: str  # 股票名称。
    prediction_scope: str  # today 或 next_day。
    as_of_date: date  # 预测生成对应的最新交易日。
    predicted_trade_date: date  # 预计触发对应的交易日。
    current_change: float  # 当前交易日股票涨跌幅。
    predicted_change: float  # 规则线对应的剩余涨跌幅。
    deviation: float  # 当前累计偏离值。
    monitor_days: int  # 当前使用的监控窗口。
    trigger_price: float  # 理论触发价格。
    rule_text: str  # 给小程序展示的中文触发规则。
    risk_level: RiskLevel  # 预警风险等级。
    data_quality: DataQuality  # 预测输入的数据质量。


@dataclass(frozen=True)
class SourceHealth:
    """数据源健康检查结果。"""

    source: str  # 数据源名称。
    available: bool  # 当前是否可访问。
    latency_ms: Optional[int]  # 最近一次请求耗时，无法测量时为空。
    last_success_at: Optional[datetime]  # 最近一次成功时间。
    message: str  # 面向日志和管理页面的中文状态说明。
