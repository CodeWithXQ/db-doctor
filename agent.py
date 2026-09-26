"""主流程编排：采集 → EXPLAIN 分析 → 规则诊断 → LLM 兜底 → 优化建议。

诊断链路（按 token 成本分层）：
- 规则命中（confidence >= 阈值）→ 根因由规则确定，token=0；
- 规则未命中 / 需要具体建议 → 调 LLM 语义兜底 + 生成具体 DDL / 改写。
"""
from __future__ import annotations

import pymysql

from config import get_mysql_config
from explain_analyzer import analyze as analyze_explain
from llm_diagnoser import DiagnosisResult, LLMDiagnoser, Suggestion
from rule_engine import diagnose as rule_diagnose
from rule_engine import template_suggestion
from slow_log_parser import SlowQuery, parse_slow_log, wrap_single_sql

# 规则命中即可直接采信的最低置信度（低于则交给 LLM 复核）
_RULE_CONFIDENCE_THRESHOLD = 0.8


def get_conn() -> pymysql.connections.Connection:
    cfg = get_mysql_config()
    return pymysql.connect(**cfg)


def diagnose_sql(sql: str, conn=None, allow_llm: bool = True) -> DiagnosisResult:
    """诊断单条 SQL。"""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        sq = wrap_single_sql(sql)
        return _diagnose_one(conn, sq, allow_llm)
    finally:
        if own_conn:
            conn.close()


def diagnose_slow_log(path: str, allow_llm: bool = True) -> list[DiagnosisResult]:
    """诊断 slow log 文件中的所有慢查询。"""
    text = open(path, encoding="utf-8").read()
    queries = [q for q in parse_slow_log(text) if q.is_slow]
    conn = get_conn()
    try:
        return [_diagnose_one(conn, q, allow_llm) for q in queries]
    finally:
        conn.close()


def _diagnose_one(conn, sq: SlowQuery, allow_llm: bool) -> DiagnosisResult:
    expl = analyze_explain(conn, sq.sql)
    if "error" in expl:
        return DiagnosisResult(root_cause=expl["error"], confidence=0.0, llm_used=False)

    signals = expl["signals"]
    text_hints = expl["text_hints"]
    tables = expl["tables"]

    rd = rule_diagnose(signals, text_hints, sq.sql)

    # 规则命中且置信度足够：直接采信，无需 LLM（token=0）
    if rd is not None and rd.confidence >= _RULE_CONFIDENCE_THRESHOLD:
        return _from_rule(rd)

    # 不允许 LLM：规则未命中或低置信，返回规则结论或"未命中"
    if not allow_llm:
        if rd is not None:
            return _from_rule(rd)
        return DiagnosisResult(root_cause="规则未命中", confidence=0.0, llm_used=False)

    # 允许 LLM：语义兜底 + 生成具体优化建议
    schema_info = _get_schema(conn, tables)
    diagnoser = LLMDiagnoser()
    return diagnoser.diagnose(sq.sql, signals, text_hints, schema_info, rd)


def _from_rule(rd) -> DiagnosisResult:
    """规则命中路径：根因确定，建议用模板（token=0）。"""
    suggestion = Suggestion(
        type=rd.suggestion_type,
        reason=template_suggestion(rd.suggestion_type),
    )
    return DiagnosisResult(
        root_cause=rd.root_cause,
        confidence=rd.confidence,
        evidence=rd.evidence,
        matched_rules=rd.matched_rules,
        suggestions=[suggestion],
        llm_used=False,
    )


def _get_schema(conn, tables: list[str]) -> str:
    """取涉及表的 SHOW CREATE TABLE 摘要，供 LLM 参考真实列名。"""
    if not tables:
        return ""
    parts = []
    cur = conn.cursor()
    try:
        for t in tables:
            if not t or t in ("<derived", "<subquery"):  # 派生表名不是真实表
                continue
            try:
                cur.execute(f"SHOW CREATE TABLE `{t}`")
                row = cur.fetchone()
                if row:
                    parts.append(row[1])
            except Exception:  # noqa: BLE001
                continue
    finally:
        cur.close()
    return "\n\n".join(parts)
