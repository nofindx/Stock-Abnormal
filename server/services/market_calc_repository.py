"""单股计算的最小持久化层。

只保存股票基础资料、滚动收益率、已计算指标和按目标交易日归档的数据集，
不保存原始日线、成交量或换手率。
未配置 MySQL 时返回不可用状态，由 MarketService 使用本地开发回退链路。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


class MarketCalcRepository:
    """CloudBase MySQL 计算基础数据仓库。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or self._config_from_env()
        self._pymysql = None
        self._initialised = False

    @staticmethod
    def _config_from_env() -> Dict[str, Any]:
        def value(name: str, fallback: str = "") -> str:
            return str(os.getenv(name, os.getenv(f"MARKET_{name}", fallback)) or "").strip()

        host = value("MYSQL_HOST") or value("MYSQL_ADDRESS")
        port_text = value("MYSQL_PORT")
        if ":" in host and not port_text:
            host, port_text = host.rsplit(":", 1)
        port = int(port_text or 3306)
        return {
            "host": host,
            "port": port,
            "user": value("MYSQL_USER") or value("MYSQL_USERNAME"),
            "password": value("MYSQL_PASSWORD"),
            "database": value("MYSQL_DATABASE") or value("MYSQL_DB") or "stock_abnormal",
            "charset": "utf8mb4",
            "connect_timeout": 5,
            "autocommit": False,
        }

    @property
    def available(self) -> bool:
        return bool(self.config.get("host") and self.config.get("user") and self.config.get("database"))

    def _driver(self):
        if self._pymysql is None:
            try:
                import pymysql
            except ImportError:
                return None
            self._pymysql = pymysql
        return self._pymysql

    @contextmanager
    def connection(self):
        driver = self._driver()
        if not self.available or driver is None:
            raise RuntimeError("MySQL 计算数据仓库未配置")
        connection = driver.connect(**self.config)
        try:
            yield connection
        finally:
            connection.close()

    def ensure_schema(self) -> None:
        if not self.available or self._initialised:
            return
        with self.connection() as db:
            with db.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS stock_basic (
                        ts_code VARCHAR(16) PRIMARY KEY,
                        symbol VARCHAR(12) NOT NULL,
                        name VARCHAR(80) NOT NULL,
                        name_initials VARCHAR(80) NOT NULL,
                        market VARCHAR(16) NOT NULL,
                        board VARCHAR(16) NOT NULL,
                        is_st TINYINT NOT NULL DEFAULT 0,
                        list_status CHAR(1) NOT NULL DEFAULT 'L',
                        list_date CHAR(8) NOT NULL DEFAULT '',
                        delist_date CHAR(8) NOT NULL DEFAULT '',
                        price_limit_enabled TINYINT NULL,
                        updated_at DATETIME NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS stock_calc_base (
                        ts_code VARCHAR(16) PRIMARY KEY,
                        as_of_trade_date CHAR(8) NOT NULL,
                        latest_close DECIMAL(18,6) NULL,
                        stock_return_vector JSON NOT NULL,
                        deviation_3 DECIMAL(18,8) NULL,
                        deviation_10 DECIMAL(18,8) NULL,
                        deviation_30 DECIMAL(18,8) NULL,
                        same_direction_up SMALLINT NOT NULL DEFAULT 0,
                        same_direction_down SMALLINT NOT NULL DEFAULT 0,
                        data_stage VARCHAR(16) NOT NULL DEFAULT 'formal',
                        quote_updated_at DATETIME NULL,
                        updated_at DATETIME NOT NULL,
                        CONSTRAINT fk_stock_calc_basic FOREIGN KEY (ts_code) REFERENCES stock_basic(ts_code)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS index_calc_base (
                        index_code VARCHAR(16) PRIMARY KEY,
                        index_name VARCHAR(80) NOT NULL,
                        as_of_trade_date CHAR(8) NOT NULL,
                        latest_close DECIMAL(18,6) NULL,
                        index_return_vector JSON NOT NULL,
                        data_stage VARCHAR(16) NOT NULL DEFAULT 'formal',
                        quote_updated_at DATETIME NULL,
                        updated_at DATETIME NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS market_calc_job (
                        job_name VARCHAR(40) PRIMARY KEY,
                        last_success_date CHAR(8) NOT NULL DEFAULT '',
                        last_attempt_at DATETIME NULL,
                        last_error TEXT NOT NULL,
                        last_failure_date CHAR(10) NOT NULL DEFAULT '',
                        retry_marker VARCHAR(32) NOT NULL DEFAULT ''
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS prediction_cache (
                        scope VARCHAR(16) PRIMARY KEY,
                        trade_date CHAR(8) NOT NULL DEFAULT '',
                        updated_at DATETIME NULL,
                        items JSON NOT NULL,
                        data_stage VARCHAR(16) NOT NULL DEFAULT 'formal',
                        data_quality VARCHAR(32) NOT NULL DEFAULT 'confirmed',
                        refreshing_until BIGINT NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL,
                        rule_version VARCHAR(40) NOT NULL DEFAULT ''
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS prediction_dataset (
                        dataset_role VARCHAR(16) NOT NULL,
                        target_trade_date CHAR(8) NOT NULL,
                        base_trade_date CHAR(8) NOT NULL DEFAULT '',
                        updated_at DATETIME NULL,
                        items JSON NOT NULL,
                        data_stage VARCHAR(16) NOT NULL DEFAULT 'formal',
                        data_quality VARCHAR(32) NOT NULL DEFAULT 'confirmed',
                        dataset_quote_updated_at DATETIME NULL,
                        dataset_quote_as_of CHAR(8) NOT NULL DEFAULT '',
                        dataset_quote_source VARCHAR(64) NOT NULL DEFAULT '',
                        dataset_quote_complete TINYINT NOT NULL DEFAULT 0,
                        item_count INT NOT NULL DEFAULT 0,
                        exclusion_stats JSON NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'ready',
                        last_error TEXT NOT NULL,
                        rule_version VARCHAR(40) NOT NULL DEFAULT '',
                        PRIMARY KEY (dataset_role, target_trade_date)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS prediction_job (
                        job_name VARCHAR(40) PRIMARY KEY,
                        target_trade_date CHAR(8) NOT NULL DEFAULT '',
                        data_stage VARCHAR(16) NOT NULL DEFAULT 'formal',
                        status VARCHAR(16) NOT NULL DEFAULT 'idle',
                        lease_until BIGINT NOT NULL DEFAULT 0,
                        started_at DATETIME NULL,
                        finished_at DATETIME NULL,
                        attempt INT NOT NULL DEFAULT 0,
                        input_watermark VARCHAR(64) NOT NULL DEFAULT '',
                        output_dataset_key VARCHAR(80) NOT NULL DEFAULT '',
                        last_error TEXT NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                # 兼容先前已经创建的本地表；新增字段只保存调度状态，不保存行情明细。
                for statement in (
                    "ALTER TABLE market_calc_job ADD COLUMN last_failure_date CHAR(10) NOT NULL DEFAULT ''",
                    "ALTER TABLE market_calc_job ADD COLUMN retry_marker VARCHAR(32) NOT NULL DEFAULT ''",
                    "ALTER TABLE stock_calc_base ADD COLUMN data_stage VARCHAR(16) NOT NULL DEFAULT 'formal'",
                    "ALTER TABLE index_calc_base ADD COLUMN data_stage VARCHAR(16) NOT NULL DEFAULT 'formal'",
                    "ALTER TABLE stock_calc_base ADD COLUMN quote_updated_at DATETIME NULL",
                    "ALTER TABLE index_calc_base ADD COLUMN quote_updated_at DATETIME NULL",
                    "ALTER TABLE prediction_cache ADD COLUMN data_stage VARCHAR(16) NOT NULL DEFAULT 'formal'",
                    "ALTER TABLE prediction_cache ADD COLUMN data_quality VARCHAR(32) NOT NULL DEFAULT 'confirmed'",
                    "ALTER TABLE prediction_cache ADD COLUMN rule_version VARCHAR(40) NOT NULL DEFAULT ''",
                    "ALTER TABLE prediction_dataset ADD COLUMN dataset_quote_updated_at DATETIME NULL",
                    "ALTER TABLE prediction_dataset ADD COLUMN dataset_quote_as_of CHAR(8) NOT NULL DEFAULT ''",
                    "ALTER TABLE prediction_dataset ADD COLUMN dataset_quote_source VARCHAR(64) NOT NULL DEFAULT ''",
                    "ALTER TABLE prediction_dataset ADD COLUMN dataset_quote_complete TINYINT NOT NULL DEFAULT 0",
                    "ALTER TABLE prediction_dataset ADD COLUMN item_count INT NOT NULL DEFAULT 0",
                    "ALTER TABLE prediction_dataset ADD COLUMN exclusion_stats JSON NULL",
                    "ALTER TABLE prediction_dataset ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'ready'",
                    "ALTER TABLE stock_basic ADD COLUMN list_date CHAR(8) NOT NULL DEFAULT ''",
                    "ALTER TABLE stock_basic ADD COLUMN delist_date CHAR(8) NOT NULL DEFAULT ''",
                    "ALTER TABLE stock_basic ADD COLUMN price_limit_enabled TINYINT NULL",
                ):
                    try:
                        cursor.execute(statement)
                    except Exception:
                        pass
            db.commit()
        self._initialised = True

    def search(self, query: str, limit: int = 5) -> Optional[List[Dict[str, Any]]]:
        """查询已同步的基础资料；排序仍由业务层统一完成。"""

        if not self.available:
            return None
        self.ensure_schema()
        value = str(query or "").strip().lower()
        if not value:
            return []
        pattern = f"%{value}%"
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT ts_code, symbol, name, name_initials, market, board,
                           is_st AS isST, list_status, list_date, delist_date,
                           price_limit_enabled AS priceLimitEnabled
                    FROM stock_basic
                    WHERE LOWER(symbol) LIKE %s OR LOWER(ts_code) LIKE %s
                       OR LOWER(name) LIKE %s OR LOWER(name_initials) LIKE %s
                    LIMIT %s
                """, (pattern, pattern, pattern, pattern, int(limit) * 20))
                rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def list_stocks(self) -> Optional[List[Dict[str, Any]]]:
        """读取当前股票基础池；空表返回空列表，仓库不可用返回 None。"""

        if not self.available:
            return None
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT ts_code, symbol, name, name_initials, market, board,
                           is_st AS isST, list_status, list_date, delist_date,
                           price_limit_enabled AS priceLimitEnabled
                    FROM stock_basic
                    WHERE list_status = 'L'
                    ORDER BY symbol
                """)
                rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_calc(self, ts_code: str) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT b.*, c.as_of_trade_date, c.latest_close,
                           c.stock_return_vector, c.deviation_3, c.deviation_10,
                           c.deviation_30, c.same_direction_up, c.same_direction_down
                    FROM stock_basic b
                    LEFT JOIN stock_calc_base c ON c.ts_code = b.ts_code
                    WHERE b.ts_code = %s
                """, (ts_code,))
                row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["isST"] = bool(result.get("isST"))
        result["stock_return_vector"] = self._json_value(result.get("stock_return_vector"), [])
        return result

    def get_index(self, index_code: str) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                cursor.execute("SELECT * FROM index_calc_base WHERE index_code = %s", (index_code,))
                row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["index_return_vector"] = self._json_value(result.get("index_return_vector"), [])
        return result

    def all_calculations(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """读取当前股票计算数据，供每日增量更新使用。"""

        if not self.available:
            return None
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                cursor.execute("SELECT * FROM stock_calc_base")
                rows = cursor.fetchall()
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            value = dict(row)
            value["stock_return_vector"] = self._json_value(value.get("stock_return_vector"), [])
            result[str(value["ts_code"])] = value
        return result

    def all_indexes(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """读取当前指数计算数据，供每日增量更新使用。"""

        if not self.available:
            return None
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                cursor.execute("SELECT * FROM index_calc_base")
                rows = cursor.fetchall()
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            value = dict(row)
            value["index_return_vector"] = self._json_value(value.get("index_return_vector"), [])
            result[str(value["index_code"])] = value
        return result

    def job(self, job_name: str = "daily") -> Optional[Dict[str, Any]]:
        """读取后台更新状态。"""

        if not self.available:
            return None
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                cursor.execute("SELECT * FROM market_calc_job WHERE job_name = %s", (job_name,))
                row = cursor.fetchone()
        return dict(row) if row else None

    def mark_retry(self, marker: str) -> None:
        if not self.available:
            return
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO market_calc_job (job_name, retry_marker, last_error)
                    VALUES ('daily', %s, '')
                    ON DUPLICATE KEY UPDATE retry_marker=VALUES(retry_marker)
                """, (str(marker),))
            db.commit()

    def upsert_market(self, stocks: Iterable[Dict[str, Any]], calculations: Iterable[Dict[str, Any]], indexes: Iterable[Dict[str, Any]], trade_date: str, data_stage: str = "formal") -> None:
        """在一个事务内更新当前计算数据，不保留旧版本。"""

        if not self.available:
            raise RuntimeError("MySQL 计算数据仓库未配置")
        self.ensure_schema()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.connection() as db:
            with db.cursor() as cursor:
                for item in stocks:
                    cursor.execute("""
                        INSERT INTO stock_basic
                        (ts_code, symbol, name, name_initials, market, board, is_st, list_status, list_date, delist_date, price_limit_enabled, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                        symbol=VALUES(symbol), name=VALUES(name), name_initials=VALUES(name_initials),
                        market=VALUES(market), board=VALUES(board), is_st=VALUES(is_st),
                        list_status=VALUES(list_status), list_date=VALUES(list_date), delist_date=VALUES(delist_date),
                        price_limit_enabled=VALUES(price_limit_enabled), updated_at=VALUES(updated_at)
                    """, (item["ts_code"], item["symbol"], item["name"], item["nameInitials"], item["market"], item["board"], int(item.get("isST", False)), item.get("list_status", "L"), str(item.get("list_date") or ""), str(item.get("delist_date") or ""), None if item.get("priceLimitEnabled") is None else int(bool(item.get("priceLimitEnabled"))), now))
                active_codes = [str(item["ts_code"]) for item in stocks if item.get("list_status", "L") == "L"]
                if active_codes:
                    placeholders = ",".join("%s" for _ in active_codes)
                    cursor.execute(
                        f"UPDATE stock_basic SET list_status = 'D', updated_at = %s WHERE list_status = 'L' AND ts_code NOT IN ({placeholders})",
                        [now, *active_codes],
                    )
                for item in calculations:
                    cursor.execute("""
                        INSERT INTO stock_calc_base
                        (ts_code, as_of_trade_date, latest_close, stock_return_vector, deviation_3,
                         deviation_10, deviation_30, same_direction_up, same_direction_down, data_stage, quote_updated_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                        as_of_trade_date=VALUES(as_of_trade_date), latest_close=VALUES(latest_close),
                        stock_return_vector=VALUES(stock_return_vector), deviation_3=VALUES(deviation_3),
                        deviation_10=VALUES(deviation_10), deviation_30=VALUES(deviation_30),
                        same_direction_up=VALUES(same_direction_up), same_direction_down=VALUES(same_direction_down), data_stage=VALUES(data_stage), quote_updated_at=VALUES(quote_updated_at),
                        updated_at=VALUES(updated_at)
                    """, (
                        item["ts_code"], item.get("as_of_trade_date") or trade_date,
                        item.get("latestClose", item.get("latest_close")),
                        json.dumps(item.get("stockReturns", item.get("stock_return_vector", [])), ensure_ascii=False),
                        item.get("deviation3"), item.get("deviation10"), item.get("deviation30"),
                        item.get("sameDirectionUp", item.get("same_direction_up", 0)),
                        item.get("sameDirectionDown", item.get("same_direction_down", 0)), item.get("data_stage", data_stage), item.get("quote_updated_at"), now,
                    ))
                for item in indexes:
                    cursor.execute("""
                        INSERT INTO index_calc_base
                        (index_code, index_name, as_of_trade_date, latest_close, index_return_vector, data_stage, quote_updated_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                        index_name=VALUES(index_name), as_of_trade_date=VALUES(as_of_trade_date),
                        latest_close=VALUES(latest_close), index_return_vector=VALUES(index_return_vector), data_stage=VALUES(data_stage), quote_updated_at=VALUES(quote_updated_at),
                        updated_at=VALUES(updated_at)
                    """, (
                        item.get("indexCode", item.get("index_code")),
                        item.get("indexName", item.get("index_name", item.get("indexCode", item.get("index_code")))),
                        item.get("as_of_trade_date") or trade_date,
                        item.get("latestClose", item.get("latest_close")),
                        json.dumps(item.get("indexReturns", item.get("index_return_vector", [])), ensure_ascii=False), item.get("data_stage", data_stage), item.get("quote_updated_at"), now,
                    ))
                cursor.execute("""
                    INSERT INTO market_calc_job (job_name, last_success_date, last_attempt_at, last_error)
                    VALUES ('daily', %s, %s, '')
                    ON DUPLICATE KEY UPDATE last_success_date=VALUES(last_success_date), last_attempt_at=VALUES(last_attempt_at), last_error=''
                """, (trade_date, now))
                cursor.execute("""
                    UPDATE market_calc_job
                    SET last_failure_date = '', retry_marker = ''
                    WHERE job_name = 'daily'
                """)
            db.commit()

    def record_failure(self, error: str) -> None:
        if not self.available:
            return
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO market_calc_job (job_name, last_attempt_at, last_error, last_failure_date)
                    VALUES ('daily', %s, %s, %s)
                    ON DUPLICATE KEY UPDATE last_attempt_at=VALUES(last_attempt_at), last_error=VALUES(last_error), last_failure_date=VALUES(last_failure_date)
                """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(error)[:1000], datetime.now().strftime("%Y-%m-%d")))
            db.commit()

    def get_prediction(self, scope: str, target_trade_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """读取指定角色的数据集；传入目标交易日时必须精确匹配。"""

        if not self.available:
            return None
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                where = "dataset_role = %s"
                params: List[Any] = [scope]
                if target_trade_date:
                    where += " AND target_trade_date = %s"
                    params.append(str(target_trade_date))
                cursor.execute(
                    "SELECT dataset_role AS scope, target_trade_date, base_trade_date, updated_at, items, "
                    "data_stage, data_quality, dataset_quote_updated_at, dataset_quote_as_of, "
                    "dataset_quote_source, dataset_quote_complete, item_count, exclusion_stats, status, "
                    "last_error, rule_version FROM prediction_dataset WHERE " + where +
                    " ORDER BY target_trade_date DESC, updated_at DESC LIMIT 1",
                    tuple(params),
                )
                row = cursor.fetchone()
                if not row and not target_trade_date:
                    # 迁移窗口内允许读取旧表，写入和任务锁只使用 prediction_job。
                    cursor.execute(
                        "SELECT scope, trade_date AS target_trade_date, trade_date AS base_trade_date, updated_at, items, "
                        "data_stage, data_quality, last_error, rule_version FROM prediction_cache WHERE scope=%s",
                        (scope,),
                    )
                    row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["items"] = self._json_value(result.get("items"), [])
        result["exclusion_stats"] = self._json_value(result.get("exclusion_stats"), {})
        result["dataset_quote_complete"] = bool(result.get("dataset_quote_complete"))
        return result

    def claim_prediction(self, scope: str, lease_seconds: int = 300) -> bool:
        """用 MySQL 行锁领取预测刷新任务，避免多实例重复扫描。"""

        if not self.available:
            return True
        self.ensure_schema()
        import time
        now = int(time.time())
        until = now + int(lease_seconds)
        with self.connection() as db:
            with db.cursor(self._driver().cursors.DictCursor) as cursor:
                cursor.execute("SELECT lease_until, status FROM prediction_job WHERE job_name = %s FOR UPDATE", (scope,))
                row = cursor.fetchone()
                if row and int(row.get("lease_until") or 0) > now:
                    db.rollback()
                    return False
                if row:
                    cursor.execute("UPDATE prediction_job SET lease_until=%s, status='running', attempt=attempt+1, started_at=NOW() WHERE job_name=%s", (until, scope))
                else:
                    cursor.execute(
                        "INSERT INTO prediction_job (job_name, lease_until, status, attempt, started_at, last_error) VALUES (%s,%s,'running',1,NOW(),'')",
                        (scope, until),
                    )
            db.commit()
        return True

    def save_prediction(self, scope: str, trade_date: str, items: Iterable[Dict[str, Any]], updated_at: datetime, data_stage: str = "formal", data_quality: str = "confirmed", rule_version: str = "", target_trade_date: str = "", base_trade_date: str = "", dataset_quote_updated_at: str = "", dataset_quote_as_of: str = "", dataset_quote_source: str = "", dataset_quote_complete: bool = False, exclusion_stats: Optional[Dict[str, Any]] = None, status: str = "ready") -> None:
        if not self.available:
            return
        self.ensure_schema()
        target_trade_date = str(target_trade_date or trade_date or "")
        base_trade_date = str(base_trade_date or trade_date or "")
        item_list = list(items)
        with self.connection() as db:
            with db.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO prediction_dataset
                    (dataset_role, target_trade_date, base_trade_date, updated_at, items, data_stage, data_quality,
                     dataset_quote_updated_at, dataset_quote_as_of, dataset_quote_source, dataset_quote_complete,
                     item_count, exclusion_stats, status, last_error, rule_version)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'',%s)
                    ON DUPLICATE KEY UPDATE base_trade_date=VALUES(base_trade_date), updated_at=VALUES(updated_at),
                    items=VALUES(items), data_stage=VALUES(data_stage), data_quality=VALUES(data_quality),
                    dataset_quote_updated_at=VALUES(dataset_quote_updated_at), dataset_quote_as_of=VALUES(dataset_quote_as_of),
                    dataset_quote_source=VALUES(dataset_quote_source), dataset_quote_complete=VALUES(dataset_quote_complete),
                    item_count=VALUES(item_count), exclusion_stats=VALUES(exclusion_stats), status=VALUES(status),
                    last_error='', rule_version=VALUES(rule_version)
                """, (scope, target_trade_date, base_trade_date, updated_at.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(item_list, ensure_ascii=False), data_stage, data_quality, dataset_quote_updated_at or None, dataset_quote_as_of or "", dataset_quote_source or "", int(dataset_quote_complete), len(item_list), json.dumps(exclusion_stats or {}, ensure_ascii=False), status, str(rule_version or "")))
                cursor.execute("UPDATE prediction_job SET status='succeeded', lease_until=0, finished_at=NOW(), output_dataset_key=%s, last_error='' WHERE job_name=%s", (f"{scope}:{target_trade_date}", scope))
            db.commit()

    def fail_prediction(self, scope: str, error: str) -> None:
        if not self.available:
            return
        self.ensure_schema()
        with self.connection() as db:
            with db.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO prediction_job (job_name, status, lease_until, finished_at, last_error)
                    VALUES (%s,'failed',0,NOW(),%s)
                    ON DUPLICATE KEY UPDATE status='failed', lease_until=0, finished_at=NOW(), last_error=VALUES(last_error)
                """, (scope, str(error)[:1000]))
            db.commit()

    @staticmethod
    def _json_value(value: Any, fallback: Any) -> Any:
        if value is None:
            return fallback
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback
