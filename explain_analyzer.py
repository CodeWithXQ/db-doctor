"""执行计划分析器：对 SQL 跑 EXPLAIN，提取可判定根因的信号。

EXPLAIN 结果解析 + SQL 文本正则分析全部自己实现，不依赖第三方 SQL 解析库。
"""
from __future__ import annotations

import re


# join type 严重程度排序，用于找"最差"的访问类型
_TYPE_RANK = {
    "system": 0,
    "const": 1,
    "eq_ref": 2,
    "ref": 3,
    "fulltext": 3,
    "ref_or_null": 3,
    "index_merge": 3,
    "range": 4,
    "index": 5,
    "ALL": 6,
}


def analyze(db_conn, sql: str) -> dict:
    """跑 EXPLAIN 并提取信号字典。db_conn 为已连接的 PyMySQL connection。

    返回 dict：
        signals: 从 EXPLAIN 结果提取的信号
        text_hints: 从 SQL 文本正则提取的提示
        error: 若无法 EXPLAIN
    """
    _sql = sql.strip().rstrip(";")
    if not _re_explainable(_sql):
        return {"error": "非 SELECT/UPDATE/DELETE 语句，无法 EXPLAIN"}

    cur = db_conn.cursor()
    try:
        cur.execute(f"EXPLAIN {_sql}")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    except Exception as e:  # noqa: BLE001
        return {"error": f"EXPLAIN 失败: {e}"}
    finally:
        cur.close()

    if not rows:
        return {"error": "EXPLAIN 无结果"}

    explain_rows = [dict(zip(cols, r)) for r in rows]
    return {
        "signals": _extract_signals(explain_rows),
        "text_hints": _text_hints(_sql),
        "tables": [r.get("table") for r in explain_rows if r.get("table")],
        "explain": explain_rows,
    }


def _re_explainable(sql: str) -> bool:
    s = sql.lstrip().upper()
    return s.startswith(("SELECT", "UPDATE", "DELETE"))


def _extract_signals(explain_rows: list[dict]) -> dict:
    """从 EXPLAIN 结果行提取判定根因的信号。"""
    types = [r.get("type") for r in explain_rows if r.get("type")]
    worst = max(types, key=lambda t: _TYPE_RANK.get(t, 0)) if types else ""

    full_scan = "ALL" in types
    # 有 possible_keys 但没走（索引失效 / 优化器放弃）
    no_index_used = any(
        r.get("possible_keys") and not r.get("key") for r in explain_rows
    )
    # 全表扫描且无任何可用索引（缺索引）
    no_available_index = any(
        r.get("type") == "ALL" and not r.get("possible_keys") and not r.get("key")
        for r in explain_rows
    )
    extra_text = " ".join(r.get("Extra") or "" for r in explain_rows)
    using_filesort = "Using filesort" in extra_text
    using_temporary = "Using temporary" in extra_text

    filtereds = [
        float(r["filtered"]) for r in explain_rows if r.get("filtered") is not None
    ]
    low_selectivity = any(f < 10.0 for f in filtereds)
    max_rows = max((int(r.get("rows") or 0) for r in explain_rows), default=0)

    return {
        "worst_type": worst,
        "full_scan": full_scan,
        "no_index_used": no_index_used,
        "no_available_index": no_available_index,
        "using_filesort": using_filesort,
        "using_temporary": using_temporary,
        "low_selectivity": low_selectivity,
        "max_rows": max_rows,
    }


def _text_hints(sql: str) -> dict:
    """从 SQL 文本正则提取的提示（属于"自己实现"的文本分析）。"""
    upper = sql.upper()
    leading_wildcard = bool(re.search(r"LIKE\s+['\"]%", sql, re.IGNORECASE))
    # 列被函数包裹：WHERE DATE(col)=... / LOWER(col)=...
    column_in_function = bool(
        re.search(
            r"\b(DATE|YEAR|MONTH|DAY|LOWER|UPPER|TRIM|CONCAT|SUBSTR|SUBSTRING|IFNULL|COALESCE)\s*\(\s*[a-zA-Z_][\w.]*\s*\)",
            sql,
        )
    )
    select_star = bool(re.search(r"SELECT\s+\*", sql, re.IGNORECASE))

    # 深分页：LIMIT offset, count 或 LIMIT count OFFSET offset
    offset = 0
    m = re.search(r"LIMIT\s+(\d+)\s*,\s*\d+", sql, re.IGNORECASE)
    if m:
        offset = int(m.group(1))
    else:
        m2 = re.search(r"LIMIT\s+\d+\s+OFFSET\s+(\d+)", sql, re.IGNORECASE)
        if m2:
            offset = int(m2.group(1))

    return {
        "leading_wildcard": leading_wildcard,
        "column_in_function": column_in_function,
        "select_star": select_star,
        "deep_pagination": offset >= 10000,
        "limit_offset": offset,
    }
