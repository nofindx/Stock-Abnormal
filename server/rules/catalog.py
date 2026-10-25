"""交易板块规则目录。

本模块只描述规则参数，不执行行情计算，也不访问数据源。规则版本由服务配置
绑定；修改阈值时必须同步规则文档并触发计算数据集重建。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


RULE_VERSION = "RULE-2026-10-24.1"


class Board(str, Enum):
    MAIN = "主板"
    CHINEXT = "创业板"
    STAR = "科创板"
    BSE = "北交所"


class Direction(str, Enum):
    UP = "上涨"
    DOWN = "下跌"
    NONE = "无方向"


class RiskLevel(str, Enum):
    SAFE = "安全"
    WATCH = "关注"
    HIGH = "高危"


@dataclass(frozen=True)
class SevereThreshold:
    up: float
    down: float

    def triggered(self, deviation: float) -> bool:
        return deviation >= self.up


@dataclass(frozen=True)
class BoardRule:
    ordinary_days: int
    ordinary_deviation: float
    severe_10d_deviation: float
    severe_30d_deviation: float
    severe_same_direction_count: int
    limit_price_ratio: float
    severe_10d_threshold: SevereThreshold = SevereThreshold(100.0, -50.0)
    severe_30d_threshold: SevereThreshold = SevereThreshold(200.0, -70.0)
    turnover_ratio_multiple: Optional[float] = None
    turnover_sum_threshold: Optional[float] = None


# 产品规则 RULE-2026-10-24.1。此处仅放机器可读参数，解释和来源维护在规则说明。
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
class RuleDefinition:
    key: str
    title: str
    event_class: str
    metric: str
    window_days: int
    unit: str
    description: str
    same_direction_source: str = ""


RULE_DEFINITIONS = {
    "ordinary_deviation_3d": RuleDefinition(
        key="ordinary_deviation_3d",
        title="3 日偏离",
        event_class="ordinary",
        metric="stock_return_minus_index_return",
        window_days=3,
        unit="percent",
        description="按板块阈值独立判断连续三日收盘涨跌幅偏离，属于普通异动。",
    ),
    "same_direction_10d": RuleDefinition(
        key="same_direction_10d",
        title="10 日同向",
        event_class="severe",
        metric="confirmed_ordinary_event_count_by_direction",
        window_days=10,
        unit="events",
        description="统计滚动十个交易日内已确认普通异动事件，按上涨/下跌方向分别计数。",
        same_direction_source="confirmed_ordinary_announcement_event",
    ),
    "severe_deviation_10d": RuleDefinition(
        key="severe_deviation_10d",
        title="10 日偏离",
        event_class="severe",
        metric="stock_return_minus_index_return",
        window_days=10,
        unit="percent",
        description="按板块上涨/下跌阈值独立判断十日累计收盘价偏离。",
    ),
    "severe_deviation_30d": RuleDefinition(
        key="severe_deviation_30d",
        title="30 日偏离",
        event_class="severe",
        metric="stock_return_minus_index_return",
        window_days=30,
        unit="percent",
        description="按板块上涨/下跌阈值独立判断三十日累计收盘价偏离。",
    ),
}


RESET_POLICIES = {
    "ordinary_abnormality": {
        "scope": "ordinary",
        "fact_tones": ("ordinary-abnormality",),
        "reset_evidence_code": "official_ordinary_abnormality_body",
        "confirmed_fact_required": True,
        "reset_metrics": ("ordinary_deviation_3d",),
        "preserve_metrics": ("same_direction_10d", "severe_deviation_10d", "severe_deviation_30d"),
    },
    "severe_abnormality": {
        "scope": "severe",
        "fact_tones": ("severe-10d", "severe-30d"),
        "reset_evidence_code": "official_severe_abnormality_body",
        "confirmed_fact_required": True,
        "reset_metrics": tuple(RULE_DEFINITIONS),
        "preserve_metrics": (),
    },
    "risk_warning": {
        "scope": "none",
        "fact_tones": ("ordinary", "risk-warning"),
        "reset_evidence_code": "",
        "confirmed_fact_required": True,
        "reset_metrics": (),
        "preserve_metrics": tuple(RULE_DEFINITIONS),
    },
}
