"""评测脚本：诊断命中率 + 优化前后对比。

用法：
    python seed_data.py 1000000   # 先造数
    python evaluate.py            # 再评测
"""
from __future__ import annotations

import time

from agent import diagnose_sql, get_conn
from explain_analyzer import analyze as analyze_explain

# 50 条标注测试集：每条 = (SQL, 期望规则编号, 说明)
# 覆盖 6 类稳定可构造根因：R1缺索引 / R3文件排序 / R4临时表 / R6前导通配符 / R7函数包裹 / R8深分页
TESTSET: list[tuple[str, str, str]] = [
    # --- R1 缺索引（WHERE 无索引列 user_id/amount/remark）---
    ("SELECT * FROM orders WHERE user_id=12345", "R1", "user_id 无索引"),
    ("SELECT * FROM orders WHERE user_id=12345 AND amount>100", "R1", "多列无索引"),
    ("SELECT * FROM orders WHERE amount>5000", "R1", "amount 无索引"),
    ("SELECT * FROM orders WHERE remark='hello'", "R1", "remark 无索引"),
    ("SELECT * FROM orders WHERE user_id IN (1,2,3,4,5)", "R1", "IN 无索引"),
    ("SELECT * FROM orders WHERE amount BETWEEN 100 AND 200", "R1", "范围无索引"),
    ("SELECT * FROM orders WHERE user_id=99999 AND amount<50", "R1", "组合无索引"),
    ("SELECT id, user_id FROM orders WHERE amount=9999", "R1", "投影+无索引"),
    ("SELECT * FROM orders WHERE user_id=88888 OR amount=123", "R1", "OR 无索引"),
    ("SELECT * FROM orders WHERE user_id<>100000", "R1", "不等值无索引"),
    # --- R3 文件排序（ORDER BY 无索引列）---
    ("SELECT * FROM orders WHERE status='paid' ORDER BY amount", "R3", "ORDER BY amount"),
    ("SELECT * FROM orders ORDER BY user_id LIMIT 100", "R3", "ORDER BY user_id"),
    ("SELECT * FROM orders WHERE amount>100 ORDER BY remark", "R3", "ORDER BY remark"),
    ("SELECT * FROM orders WHERE status='done' ORDER BY user_id DESC", "R3", "ORDER BY DESC"),
    ("SELECT id FROM orders ORDER BY amount DESC", "R3", "ORDER BY amount DESC"),
    ("SELECT * FROM orders WHERE user_id=1 ORDER BY amount", "R3", "WHERE+ORDER BY 无索引"),
    ("SELECT * FROM orders ORDER BY remark LIMIT 50", "R3", "ORDER BY remark LIMIT"),
    ("SELECT * FROM orders WHERE status='shipped' ORDER BY amount", "R3", "status有索引+amount排序"),
    # --- R4 临时表（GROUP BY / DISTINCT 无索引列）---
    ("SELECT user_id, COUNT(*) FROM orders GROUP BY user_id", "R4", "GROUP BY user_id"),
    ("SELECT remark, COUNT(*) FROM orders GROUP BY remark", "R4", "GROUP BY remark"),
    ("SELECT DISTINCT user_id FROM orders", "R4", "DISTINCT user_id"),
    ("SELECT DISTINCT remark FROM orders", "R4", "DISTINCT remark"),
    ("SELECT user_id, SUM(amount) FROM orders GROUP BY user_id", "R4", "GROUP BY 聚合"),
    ("SELECT amount, COUNT(*) FROM orders GROUP BY amount", "R4", "GROUP BY amount"),
    # --- R6 前导通配符（LIKE '%x'）---
    ("SELECT * FROM orders WHERE remark LIKE '%abc%'", "R6", "前导通配符"),
    ("SELECT * FROM orders WHERE remark LIKE '%hello%'", "R6", "前导通配符2"),
    ("SELECT * FROM orders WHERE remark LIKE '%test%'", "R6", "前导通配符3"),
    ("SELECT * FROM orders WHERE remark LIKE '%keyword%'", "R6", "前导通配符4"),
    ("SELECT * FROM orders WHERE remark LIKE '%abc'", "R6", "尾导%开头"),
    ("SELECT * FROM orders WHERE remark LIKE '%foo%'", "R6", "前导通配符5"),
    ("SELECT * FROM orders WHERE remark LIKE '%bar%'", "R6", "前导通配符6"),
    ("SELECT * FROM orders WHERE remark LIKE '%value'", "R6", "尾导%开头2"),
    # --- R7 函数包裹（func(col) 索引失效）---
    ("SELECT * FROM orders WHERE DATE(create_time)='2026-01-01'", "R7", "DATE 函数包裹"),
    ("SELECT * FROM orders WHERE YEAR(create_time)=2025", "R7", "YEAR 函数包裹"),
    ("SELECT * FROM orders WHERE LOWER(status)='paid'", "R7", "LOWER 函数包裹"),
    ("SELECT * FROM orders WHERE MONTH(create_time)=9", "R7", "MONTH 函数包裹"),
    ("SELECT * FROM orders WHERE DATE(create_time)>'2026-01-01'", "R7", "DATE 范围"),
    ("SELECT * FROM orders WHERE UPPER(remark)='ABC'", "R7", "UPPER 函数包裹"),
    ("SELECT * FROM orders WHERE DAY(create_time)=26", "R7", "DAY 函数包裹"),
    ("SELECT * FROM orders WHERE YEAR(create_time)=2026 AND status='paid'", "R7", "YEAR+其他"),
    # --- R8 深分页（LIMIT 大偏移）---
    ("SELECT * FROM orders LIMIT 900000, 10", "R8", "深分页"),
    ("SELECT * FROM orders LIMIT 100000, 20", "R8", "深分页2"),
    ("SELECT * FROM orders LIMIT 500000, 50", "R8", "深分页3"),
    ("SELECT * FROM orders LIMIT 800000, 10", "R8", "深分页4"),
    ("SELECT * FROM orders LIMIT 999000, 10", "R8", "深分页5"),
    ("SELECT * FROM orders LIMIT 10000, 5", "R8", "深分页边界"),
    ("SELECT * FROM orders LIMIT 200000, 30", "R8", "深分页6"),
    ("SELECT * FROM orders LIMIT 700000, 10", "R8", "深分页7"),
    ("SELECT * FROM orders LIMIT 300000, 40", "R8", "深分页8"),
    ("SELECT * FROM orders LIMIT 400000, 10", "R8", "深分页9"),
]

# 规则盲区样本：组合条件中某列缺索引、被另一列的索引"掩盖"，规则引擎因 EXPLAIN 未出现全表扫描而漏判。
# 这些场景真实存在，规则引擎返回 NONE（需 LLM 兜底），构成评测的"坏样本"。
BLIND_TESTSET: list[tuple[str, str, str]] = [
    ("SELECT * FROM orders WHERE status='paid' AND amount>5000", "R1", "组合缺索引：status 有索引掩盖 amount 无索引"),
    ("SELECT * FROM orders WHERE status='shipped' AND user_id=12345", "R1", "组合缺索引：status 有索引掩盖 user_id 无索引"),
    ("SELECT * FROM orders WHERE create_time > '2026-09-01' AND amount>5000", "R1", "组合缺索引：create_time 有索引掩盖 amount 无索引"),
    ("SELECT * FROM orders WHERE status='done' AND remark='hello'", "R1", "组合缺索引：status 有索引掩盖 remark 无索引"),
    ("SELECT * FROM orders WHERE status='paid' AND amount BETWEEN 100 AND 200", "R1", "组合缺索引：status 有索引掩盖 amount 范围"),
    ("SELECT * FROM orders WHERE status='pending' AND user_id IN (1,2,3)", "R1", "组合缺索引：status 有索引掩盖 user_id IN"),
]


def run_accuracy(allow_llm: bool = False) -> float:
    """跑诊断命中率（默认纯规则，token=0）。

    分两层统计：
    - 规则正面样本（TESTSET，50 条常见慢查询，规则能判定）—— 期望规则命中；
    - 规则盲区样本（BLIND_TESTSET，6 条组合缺索引，规则漏判需 LLM 兜底）—— 记录规则漏判。
    """
    conn = get_conn()
    _drop_extra_indexes(conn)  # 清理业务索引，保证评测在"缺索引"干净状态、可复现

    def _run_one(subset, label):
        hit = 0
        misses = []
        for sql, expected, note in subset:
            r = diagnose_sql(sql, conn=conn, allow_llm=allow_llm)
            predicted = r.matched_rules[0] if r.matched_rules else "NONE"
            if predicted == expected:
                hit += 1
            else:
                misses.append((sql, expected, predicted, r.root_cause))
        print(f"\n[{label}] 命中 {hit}/{len(subset)} = {hit / len(subset) * 100:.1f}%")
        for sql, expected, predicted, cause in misses:
            print(f"  [MISS] 期望 {expected} 实得 {predicted} | {sql[:55]}")
        return hit, misses

    rule_hit, _rule_miss = _run_one(TESTSET, "规则正面样本（50 条常见慢查询）")
    blind_hit, blind_miss = _run_one(BLIND_TESTSET, "规则盲区样本（6 条组合缺索引）")

    conn.close()
    total = len(TESTSET) + len(BLIND_TESTSET)
    acc = (rule_hit + blind_hit) / total
    print(f"\n总命中率：{rule_hit + blind_hit}/{total} = {acc * 100:.1f}%"
          f"（其中盲区样本 {len(blind_miss)} 条规则漏判，需 LLM 兜底）")
    return acc


def run_optimization() -> None:
    """优化前后对比：对缺索引慢查询，用 LLM 生成索引建议并实测 EXPLAIN 变化。

    为可复现，跑前先清理上次生成的三列业务索引（保留 seed 建的 idx_create_time/idx_status）。
    """
    from llm_diagnoser import LLMDiagnoser

    conn = get_conn()
    diagnoser = LLMDiagnoser()
    _drop_extra_indexes(conn)

    # 3 条独立的高选择性等值查询，各建一个不同列的索引
    targets = [
        "SELECT * FROM orders WHERE user_id=12345",
        "SELECT * FROM orders WHERE remark='helloworld'",
        "SELECT * FROM orders WHERE amount=8888",
    ]
    print("\n=== 优化前后对比（EXPLAIN type / rows）===")
    for sql in targets:
        expl_before = analyze_explain(conn, sql)
        sig = expl_before["signals"]

        result = diagnoser.diagnose(
            sql,
            expl_before["signals"],
            expl_before["text_hints"],
            _schema(conn, expl_before["tables"]),
        )

        print(f"\nSQL: {sql}")
        print(f"  诊断: {result.root_cause[:48]}（token {result.token_used}）")
        print(f"  优化前: type={sig['worst_type']}, rows={sig['max_rows']}")

        ddl = None
        for s in result.suggestions:
            if s.type == "add_index" and s.ddl:
                ddl = s.ddl
                print(f"  建议: {s.ddl}")
                break

        if ddl:
            try:
                cur = conn.cursor()
                cur.execute(ddl)
                conn.commit()
                cur.close()
                expl_after = analyze_explain(conn, sql)
                sig2 = expl_after["signals"]
                print(f"  优化后: type={sig2['worst_type']}, rows={sig2['max_rows']}")
            except Exception as e:  # noqa: BLE001
                print(f"  建索引失败: {e}")

    conn.close()


def _drop_extra_indexes(conn) -> None:
    """清理评测生成的业务索引，保证优化对比可复现。"""
    cur = conn.cursor()
    for idx in ("idx_user_id", "idx_remark", "idx_amount", "idx_user_amount"):
        try:
            cur.execute(f"ALTER TABLE orders DROP INDEX {idx}")
        except Exception:  # noqa: BLE001
            pass
    conn.commit()
    cur.close()


def _schema(conn, tables) -> str:
    parts = []
    cur = conn.cursor()
    try:
        for t in tables:
            if not t:
                continue
            cur.execute(f"SHOW CREATE TABLE `{t}`")
            row = cur.fetchone()
            if row:
                parts.append(row[1])
    finally:
        cur.close()
    return "\n\n".join(parts)


if __name__ == "__main__":
    run_accuracy(allow_llm=False)
    run_optimization()
