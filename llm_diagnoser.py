"""LLM 诊断与优化建议生成：语义级兜底 + 结构化建议输出。

规则引擎未命中（或需要具体 DDL/SQL 改写建议）时调用。
使用 OpenAI 兼容协议（DeepSeek），Pydantic 约束输出结构。
"""
from __future__ import annotations

import json

from openai import OpenAI
from pydantic import BaseModel, Field

from config import get_llm_config


class Suggestion(BaseModel):
    type: str  # add_index / rewrite_sql / tune_param / other
    ddl: str | None = None
    sql: str | None = None
    reason: str


class DiagnosisResult(BaseModel):
    root_cause: str
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    llm_used: bool = False
    token_used: int = 0


class LLMDiagnoser:
    def __init__(self):
        cfg = get_llm_config()
        self._client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
        self._model = cfg["model"]
        self._temperature = cfg["temperature"]
        self._max_tokens = cfg["max_tokens"]

    def diagnose(
        self,
        sql: str,
        signals: dict,
        text_hints: dict,
        schema_info: str,
        rule_diagnosis=None,
    ) -> DiagnosisResult:
        """生成语义级诊断 + 结构化优化建议。"""
        prompt = self._build_prompt(sql, signals, text_hints, schema_info, rule_diagnosis)
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            response_format={"type": "json_object"},
        )
        token_used = resp.usage.total_tokens if resp.usage else 0
        content = resp.choices[0].message.content

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return DiagnosisResult(
                root_cause="LLM 输出解析失败", confidence=0.0, llm_used=True, token_used=token_used
            )

        suggestions = [
            Suggestion(
                type=s.get("type", "other"),
                ddl=s.get("ddl"),
                sql=s.get("sql"),
                reason=s.get("reason", ""),
            )
            for s in data.get("suggestions", [])
        ]

        rule_ids = rule_diagnosis.matched_rules if rule_diagnosis else []
        return DiagnosisResult(
            root_cause=data.get("root_cause", "未知根因"),
            confidence=float(data.get("confidence", 0.0)),
            evidence=data.get("evidence", []),
            matched_rules=rule_ids,
            suggestions=suggestions,
            llm_used=True,
            token_used=token_used,
        )

    def _build_prompt(self, sql, signals, text_hints, schema_info, rule_diagnosis) -> str:
        rule_text = (
            f"{rule_diagnosis.root_cause}（建议类型：{rule_diagnosis.suggestion_type}）"
            if rule_diagnosis
            else "无（规则未命中）"
        )
        return f"""你是一名资深 MySQL DBA。请诊断下面这条慢查询的根因，并给出可落地的优化建议。

【慢查询 SQL】
{sql}

【EXPLAIN 执行计划信号】
{signals}

【SQL 文本分析提示】
{text_hints}

【表结构摘要】
{schema_info or "（未提供）"}

【规则引擎初步判断】
{rule_text}

请以 JSON 输出，格式如下：
{{
  "root_cause": "根因描述（中文）",
  "confidence": 0.0到1.0之间的小数,
  "evidence": ["证据1", "证据2"],
  "suggestions": [
    {{"type": "add_index", "ddl": "CREATE INDEX ...", "sql": null, "reason": "为什么"}},
    {{"type": "rewrite_sql", "ddl": null, "sql": "改写后的SQL", "reason": "为什么"}}
  ]
}}

要求：
1. 建议的索引列必须来自真实表结构，禁止编造不存在的列名。
2. type 只能是 add_index / rewrite_sql / tune_param / other 之一。
3. 只输出 JSON，不要任何额外文字或代码块围栏。"""
