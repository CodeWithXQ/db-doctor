"""根因诊断规则引擎：把 EXPLAIN 信号 + SQL 文本提示确定性映射到已知根因。

规则优先，LLM 兜底。每一条规则都可解释、可单测，是项目的"有底层"部分。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Diagnosis:
    root_cause: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)
    suggestion_type: str = "other"  # add_index / rewrite_sql / tune_param / other


# 建议类型 → 通用模板建议（规则命中时无需 LLM，token=0）
_SUGGESTION_TEMPLATES = {
    "add_index": "在 WHERE/JOIN 过滤列上建立合适索引，可联合覆盖 ORDER BY/GROUP BY 列",
    "rewrite_sql": "改写 SQL 以利用索引或减少扫描范围",
    "tune_param": "ANALYZE TABLE 更新统计信息，或检查优化器行为",
    "other": "需结合业务进一步分析",
}

# 规则优先级：文本级强信号 > 明确性能信号(filesort/temporary) > 通用缺索引
_RULE_PRIORITY = {"R6": 0, "R7": 0, "R8": 0, "R3": 1, "R4": 1, "R1": 2, "R2": 2, "R5": 3}


def diagnose(signals: dict, text_hints: dict, sql: str = "") -> Diagnosis | None:
    """规则引擎主入口：命中则返回 Diagnosis，未命中返回 None（交给 LLM 兜底）。"""
    matched: list[Diagnosis] = []

    # R6 前导通配符（文本级，最高优先）
    if text_hints.get("leading_wildcard"):
        matched.append(
            Diagnosis(
                root_cause="LIKE '%...%' 前导通配符导致索引失效",
                confidence=0.95,
                evidence=["SQL 使用 LIKE '%x%'，B+Tree 索引无法利用前导通配符"],
                matched_rules=["R6"],
                suggestion_type="rewrite_sql",
            )
        )

    # R7 列被函数包裹
    if text_hints.get("column_in_function"):
        matched.append(
            Diagnosis(
                root_cause="WHERE 条件中索引列被函数包裹，导致索引失效",
                confidence=0.90,
                evidence=["谓词列被函数包裹（如 DATE(col)），优化器无法使用该列索引"],
                matched_rules=["R7"],
                suggestion_type="rewrite_sql",
            )
        )

    # R8 深分页
    if text_hints.get("deep_pagination"):
        matched.append(
            Diagnosis(
                root_cause="深分页 LIMIT 大偏移，扫描并丢弃大量无用行",
                confidence=0.90,
                evidence=[
                    f"LIMIT 偏移 {text_hints.get('limit_offset')}，需扫描前序所有行后丢弃"
                ],
                matched_rules=["R8"],
                suggestion_type="rewrite_sql",
            )
        )

    # R1 缺索引全表扫描
    if signals.get("no_available_index") and signals.get("full_scan"):
        matched.append(
            Diagnosis(
                root_cause="缺索引导致全表扫描",
                confidence=0.90,
                evidence=[
                    f"EXPLAIN type=ALL，扫描行数约 {signals.get('max_rows')}，且无可用索引"
                ],
                matched_rules=["R1"],
                suggestion_type="add_index",
            )
        )

    # R2 有索引但没走
    if signals.get("no_index_used"):
        matched.append(
            Diagnosis(
                root_cause="存在可用索引但未被使用（优化器放弃 / 统计信息偏差）",
                confidence=0.75,
                evidence=["possible_keys 非空但 key 为空"],
                matched_rules=["R2"],
                suggestion_type="tune_param",
            )
        )

    # R3 文件排序
    if signals.get("using_filesort"):
        matched.append(
            Diagnosis(
                root_cause="ORDER BY 无法利用索引，产生文件排序",
                confidence=0.85,
                evidence=["EXPLAIN Extra 含 Using filesort"],
                matched_rules=["R3"],
                suggestion_type="add_index",
            )
        )

    # R4 临时表
    if signals.get("using_temporary"):
        matched.append(
            Diagnosis(
                root_cause="GROUP BY / DISTINCT 产生临时表",
                confidence=0.85,
                evidence=["EXPLAIN Extra 含 Using temporary"],
                matched_rules=["R4"],
                suggestion_type="add_index",
            )
        )

    # R5 索引选择性差（有走索引但过滤性极低）
    if signals.get("low_selectivity") and not signals.get("full_scan"):
        matched.append(
            Diagnosis(
                root_cause="索引选择性差（过滤后行占比过低）",
                confidence=0.70,
                evidence=["EXPLAIN filtered 过低（<10%），索引区分度不足"],
                matched_rules=["R5"],
                suggestion_type="rewrite_sql",
            )
        )

    if not matched:
        return None

    # 多规则命中：按优先级 + 置信度选主根因，证据合并
    matched.sort(
        key=lambda d: (_RULE_PRIORITY.get(d.matched_rules[0], 3), -d.confidence)
    )
    primary = matched[0]
    return Diagnosis(
        root_cause=primary.root_cause,
        confidence=primary.confidence,
        evidence=[e for d in matched for e in d.evidence],
        matched_rules=[d.matched_rules[0] for d in matched],
        suggestion_type=primary.suggestion_type,
    )


def template_suggestion(suggestion_type: str) -> str:
    """规则命中时给出的通用建议描述（无需 LLM）。"""
    return _SUGGESTION_TEMPLATES.get(suggestion_type, _SUGGESTION_TEMPLATES["other"])
