#!/usr/bin/env python3
"""只读核验 prediction_dataset 迁移和股票基础资料覆盖。

退出码：0 表示满足传入的门槛；1 表示门槛不满足；2 表示仓库不可用。
脚本不执行迁移、不删除旧表，也不修改业务数据。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# 允许从仓库根目录或直接执行 `python3 server/tools/...` 两种方式运行。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.services.market_calc_repository import MarketCalcRepository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-zero-legacy-reads",
        action="store_true",
        help="要求当前进程没有从 prediction_cache 回退读取",
    )
    parser.add_argument(
        "--require-complete-stock-basic",
        action="store_true",
        help="要求活跃股票的上市日期、涨跌幅字段和 ST 标记完整",
    )
    args = parser.parse_args()

    repository = MarketCalcRepository()
    if not repository.available:
        print(json.dumps({"available": False, "error": "MySQL 计算数据仓库未配置"}, ensure_ascii=False))
        return 2
    try:
        result = {
            "available": True,
            "predictionMigration": repository.prediction_migration_status(),
            "stockBasicCoverage": repository.stock_basic_coverage(),
        }
    except Exception as exc:  # noqa: BLE001 - CLI 输出可读错误，不泄漏堆栈
        print(json.dumps({"available": False, "error": str(exc)[:300]}, ensure_ascii=False))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    migration = result["predictionMigration"] or {}
    coverage = result["stockBasicCoverage"] or {}
    if args.require_zero_legacy_reads and not migration.get("legacyReadFree", False):
        return 1
    if args.require_complete_stock_basic and not coverage.get("complete", False):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
