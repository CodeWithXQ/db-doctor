"""慢查询日志解析器：把 MySQL slow log 文本解析为结构化 SlowQuery 对象。

支持两种来源：
1. 标准 slow log 文件（# Time / # Query_time 注释块）
2. 单条 SQL 文本（直接包装为 SlowQuery，指标字段为 0/空）

纯正则解析，不依赖第三方库，便于讲清原理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SlowQuery:
    sql: str
    query_time: float = 0.0
    lock_time: float = 0.0
    rows_sent: int = 0
    rows_examined: int = 0
    timestamp: str = ""

    @property
    def is_slow(self) -> bool:
        """是否为值得诊断的慢查询（耗时 >=1s，或扫描行数远大于返回行数）。"""
        return self.query_time >= 1.0 or self.rows_examined > max(self.rows_sent * 10, 100)


# 管理语句前缀，解析时跳过（不是业务 SQL）
_ADMIN_PREFIXES = ("set ", "use ", "administrator command", "init db", "quit", "tcp port")

_TIME_RE = re.compile(r"^#\s*Time:\s*(.+)$")
_METRIC_RE = re.compile(
    r"Query_time:\s*([0-9.]+)\s+Lock_time:\s*([0-9.]+)\s+"
    r"Rows_sent:\s*(\d+)\s+Rows_examined:\s*(\d+)"
)
_SET_TS_RE = re.compile(r"set\s+timestamp\s*=\s*\d+\s*;?", re.IGNORECASE)


def parse_slow_log(text: str) -> list[SlowQuery]:
    """解析 slow log 文本，返回 SlowQuery 列表。"""
    queries: list[SlowQuery] = []
    cur_metrics: dict = {}
    cur_timestamp = ""
    cur_sql_lines: list[str] = []

    def flush() -> None:
        nonlocal cur_metrics, cur_timestamp, cur_sql_lines
        sql = _join_sql(cur_sql_lines)
        if sql:
            queries.append(
                SlowQuery(
                    sql=sql,
                    query_time=cur_metrics.get("query_time", 0.0),
                    lock_time=cur_metrics.get("lock_time", 0.0),
                    rows_sent=cur_metrics.get("rows_sent", 0),
                    rows_examined=cur_metrics.get("rows_examined", 0),
                    timestamp=cur_timestamp,
                )
            )
        cur_metrics = {}
        cur_timestamp = ""
        cur_sql_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        # 新查询开始标记
        m_time = _TIME_RE.match(line)
        if m_time:
            flush()
            cur_timestamp = m_time.group(1).strip()
            continue
        # 指标行
        m_metric = _METRIC_RE.search(stripped)
        if stripped.startswith("#") and m_metric:
            cur_metrics["query_time"] = float(m_metric.group(1))
            cur_metrics["lock_time"] = float(m_metric.group(2))
            cur_metrics["rows_sent"] = int(m_metric.group(3))
            cur_metrics["rows_examined"] = int(m_metric.group(4))
            continue
        # 跳过其他注释行
        if stripped.startswith("#"):
            continue
        # 跳过管理语句
        if stripped.lower().startswith(_ADMIN_PREFIXES):
            continue
        # SQL 行
        if stripped:
            cur_sql_lines.append(stripped)

    flush()
    return queries


def _join_sql(lines: list[str]) -> str:
    """把多行 SQL 拼成一条，去掉 SET timestamp 残留。"""
    if not lines:
        return ""
    text = " ".join(lines).strip()
    text = _SET_TS_RE.sub("", text)
    return text.strip()


def wrap_single_sql(sql: str) -> SlowQuery:
    """把单条 SQL 包装为 SlowQuery（无指标，用于直接诊断）。"""
    return SlowQuery(sql=sql.strip().rstrip(";"))
