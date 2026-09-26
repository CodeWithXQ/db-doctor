"""命令行入口：诊断单条 SQL 或 slow log 文件。

用法：
    python cli.py --sql "SELECT * FROM orders WHERE user_id=12345"
    python cli.py --sql "..." --llm     # 允许 LLM 生成具体索引 DDL / 改写建议
    python cli.py --slowlog data/slowlog/sample.log
"""
from __future__ import annotations

import argparse

from agent import diagnose_slow_log, diagnose_sql
from llm_diagnoser import DiagnosisResult


def main() -> None:
    parser = argparse.ArgumentParser(description="MySQL 慢查询 AI 诊断 Agent")
    parser.add_argument("--sql", help="诊断单条 SQL")
    parser.add_argument("--slowlog", help="诊断 slow log 文件路径")
    parser.add_argument("--llm", action="store_true", help="允许调用 LLM 生成具体优化建议")
    args = parser.parse_args()

    if args.sql:
        _print_result(diagnose_sql(args.sql, allow_llm=args.llm))
    elif args.slowlog:
        for r in diagnose_slow_log(args.slowlog, allow_llm=args.llm):
            _print_result(r)
    else:
        parser.print_help()


def _print_result(r: DiagnosisResult) -> None:
    print(f"根因: {r.root_cause}")
    print(
        f"置信度: {r.confidence:.2f} | 命中规则: {r.matched_rules or '无'} | "
        f"LLM: {r.llm_used} | token: {r.token_used}"
    )
    for e in r.evidence:
        print(f"  证据: {e}")
    for s in r.suggestions:
        detail = s.ddl or s.sql or s.reason
        print(f"  建议[{s.type}]: {detail}")
    print()


if __name__ == "__main__":
    main()
