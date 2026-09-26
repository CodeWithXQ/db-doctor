# MySQL 慢查询 AI 诊断与优化 Agent（DB Doctor）设计开发文档

> 项目定位：给 MySQL 数据库做一套「AI 医生」——读懂慢查询的体检报告（EXPLAIN 执行计划），说出为什么慢（根因诊断），开出能实测见效的药方（建索引 / 改写 SQL / 调参），再用自建大表 + 标注测试集量化「诊断命中率」与「优化前后性能差」。

---

## 1. 项目概述

### 1.1 一句话叙事

> 接 MySQL slow log + EXPLAIN 执行计划，规则引擎 + LLM 双层诊断慢查询根因，生成可落地的优化建议（索引 DDL / SQL 改写 / 参数调整），并用自建 100 万行大表做真实基线，量化诊断命中率与优化效果。

### 1.2 为什么做（真实需求论证 —— 抗"假需求"质疑）

数据库性能调优是**真金白银的付费刚需**，不是编造的场景：

| 付费参照 | 云厂商产品 |
|---|---|
| 阿里云 | DAS 数据库自治服务（按实例收费） |
| 腾讯云 | DBBrain 智能管家 |
| AWS | DevOps Guru for RDS |
| 华为云 | DAS |

**慢查询定位是 DBA 日常最耗时的活，一条慢 SQL 能拖垮整个库。** 这是云厂商在收钱解决的刚需。

### 1.3 与 TPC-H MonetDB Agent 的技术主线

本项目与已有「TPC-H MonetDB Agent」串成一条 **"AI 赋能数据库全链路"** 主线：

| | TPC-H MonetDB Agent | DB Doctor（本项目） |
|---|---|---|
| 层次 | 底层执行引擎 | 上层运维诊断 |
| 做什么 | 给定 SQL 生成 C++ 执行代码 + MonetDB 基线校验 | 给定慢 SQL 诊断根因 + 给优化建议 |
| 类比 | 造引擎 | 治病 |

两者互补，不重复。

---

## 2. 需求分析

### 2.1 目标场景

- 后端工程师 / DBA 面对慢查询时，快速定位"为什么慢"并拿到可执行的优化方案。
- 应届生求职场景：用**可复现的实验数据**证明自己具备"数据库调优 + LLM 应用"的交叉能力。

### 2.2 核心功能

| 功能 | 输入 | 输出 |
|---|---|---|
| F1 慢查询采集 | slow log 文件 / 单条 SQL | 结构化慢查询（执行时间、扫描行数等） |
| F2 执行计划分析 | 一条 SQL | EXPLAIN 信号（全表扫描 / 索引失效 / 文件排序等） |
| F3 根因诊断 | EXPLAIN 信号 + SQL 文本 | 根因（缺索引 / 索引失效 / 深分页等）+ 置信度 + 证据 |
| F4 优化建议生成 | 根因 + SQL + 表结构 | 结构化建议（索引 DDL / SQL 改写 / 调参） |
| F5 评测验证 | 标注测试集 / 大表 | 诊断命中率、优化前后性能差 |

### 2.3 非功能需求

- 诊断**可解释**：每个结论必须给出 EXPLAIN 证据，不能是黑盒。
- 数字**可复现**：所有指标本地可重跑，不靠"拍脑袋"。
- 底层**自主实现**：slow log 解析、EXPLAIN 解析、规则引擎全部自己写，LLM 只做语义兜底，不依赖 LangChain 等重框架。

---

## 3. 系统架构

### 3.1 架构图

```
                 ┌─────────────────────────────────────────┐
                 │             DB Doctor Agent              │
                 │                                          │
  slow log ─────▶│ ① 慢查询采集  slow_log_parser.py         │
   或单条 SQL ──▶│     提取 SQL / query_time / rows_examined │
                 │                     │                    │
                 │                     ▼                    │
                 │ ② 执行计划分析  explain_analyzer.py       │◀── MySQL 8.0
                 │     跑 EXPLAIN，提取信号                  │    (真实连接)
                 │                     │                    │
                 │                     ▼                    │
                 │ ③ 根因诊断   rule_engine.py (自写规则)    │
                 │        +  llm_diagnoser.py (LLM 语义兜底) │◀── DeepSeek
                 │                     │                    │    (OpenAI 兼容)
                 │                     ▼                    │
                 │ ④ 优化建议   llm_diagnoser.py             │
                 │     结构化 JSON（索引 DDL / 改写 / 调参）  │
                 │                     │                    │
                 │                     ▼                    │
                 │ ⑤ 评测验证   evaluate.py                 │
                 │     诊断命中率 + 优化前后性能差            │
                 └─────────────────────────────────────────┘
```

### 3.2 模块划分

| 模块 | 文件 | 职责 | 壁垒 |
|---|---|---|---|
| 配置 | `config.py` | .env 读取、MySQL/LLM 连接配置 | —— |
| 慢查询采集 | `slow_log_parser.py` | 解析 slow log 为结构化对象 | 自写解析器 |
| 执行计划分析 | `explain_analyzer.py` | 跑 EXPLAIN，提取信号字典 | 自写信号提取 |
| 规则引擎 | `rule_engine.py` | 信号 → 已知根因的确定性映射 | 自写诊断规则 |
| LLM 诊断 | `llm_diagnoser.py` | 语义级诊断 + 结构化优化建议 | Pydantic 结构化输出 |
| 主流程 | `agent.py` | 编排 F1→F2→F3→F4 | —— |
| 评测 | `evaluate.py` | 标注测试集命中率 + 大表优化对比 | 实测可复现 |

### 3.3 数据流

```
慢 SQL ─▶ 采集(结构化) ─▶ EXPLAIN(信号) ─▶ 规则引擎(确定性根因)
                                              │
                                              ├─ 规则覆盖 ─▶ 直接出结论
                                              └─ 规则不覆盖 ─▶ LLM 语义兜底
                                                              │
                        ┌─────────────────────────────────────┘
                        ▼
               优化建议(结构化 JSON) ─▶ 评测(命中率 + 性能差)
```

---

## 4. 技术选型

| 组件 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.13 | LLM 生态最好，复用 rag 项目经验 |
| 数据库 | MySQL 8.0（本机 8.0.44） | 目标数据库本身，真机连接 |
| DB 驱动 | PyMySQL 2.2.8 | 纯 Python，无编译依赖 |
| LLM | DeepSeek（OpenAI 兼容） | 复用已有 key，成本低 |
| 结构化输出 | Pydantic 2.12 | 保证优化建议可解析 |
| Web 框架 | FastAPI + uvicorn | 对外接口（可选） |
| SQL 解析 | 不引入 sqlglot | 保持轻量，突出"自己实现"，正则 + EXPLAIN 已够用 |

> 刻意不引入 LangChain / sqlglot：保持轻量，每一步底层原理可控，避免"调库"观感。

---

## 5. 详细设计

### 5.1 慢查询采集 `slow_log_parser.py`

**目标**：解析 MySQL slow log 文本，输出 `SlowQuery` 对象列表。

MySQL slow log 样例格式：

```
# Time: 2026-09-26T10:00:00.000000+08:00
# User@Host: root[root] @ localhost [127.0.0.1]  Id: 123
# Query_time: 1.234567  Lock_time: 0.000123  Rows_sent: 10  Rows_examined: 100000
SET timestamp=1727316000;
SELECT * FROM orders WHERE status='pending' ORDER BY create_time DESC LIMIT 10;
```

**解析规则**：
- 以 `# Time:` 行作为一条慢查询的起始标记。
- 从 `# Query_time:` 行正则提取 `Query_time / Lock_time / Rows_sent / Rows_examined`。
- SQL 语句为 `SET timestamp=...;` 行之后、下一条 `# Time:` 之前的连续非注释行（可含分号拼接）。
- 忽略管理语句（`SET`、`use db`、`administrator command`）。

**输出对象**：

```python
@dataclass
class SlowQuery:
    query_time: float      # 执行耗时(秒)
    lock_time: float       # 锁等待(秒)
    rows_sent: int         # 返回行数
    rows_examined: int     # 扫描行数
    sql: str               # SQL 文本
    timestamp: str         # 时间戳(可空)
```

### 5.2 执行计划分析 `explain_analyzer.py`

**目标**：对一条 SQL 跑 `EXPLAIN`，提取可判定根因的信号。

**实现**：
- 用 PyMySQL 执行 `EXPLAIN {sql}`（SQL 需为 SELECT/UPDATE/DELETE，不能是 INSERT）。
- 解析结果集（多行，每行对应一个 join 的表），提取字段：`type / possible_keys / key / key_len / rows / filtered / Extra`。
- 聚合多行信号（多表 join 取"最差 type"等）。

**信号字典**（这是规则引擎的输入）：

```python
{
    "worst_type": "ALL",            # 最差的 join type
    "full_scan": True,              # 是否存在 type=ALL
    "no_index_used": False,         # key 为空但 possible_keys 非空
    "no_available_index": True,     # possible_keys 和 key 都为空
    "using_filesort": False,        # Extra 含 "Using filesort"
    "using_temporary": False,       # Extra 含 "Using temporary"
    "low_selectivity": False,       # filtered < 10
    "max_rows": 100000,             # 最大扫描行数
    "tables": ["orders"],           # 涉及的表
}
```

**额外 SQL 文本分析**（正则，属于"自己实现"部分）：
- 检测 `LIKE '%x%'`（前导通配符）
- 检测 `WHERE func(col) = ...`（列被函数包裹，如 `DATE(col)`、`LOWER(col)`）
- 检测隐式类型转换（`WHERE varchar_col = 数字`，需结合表结构）
- 检测 `SELECT *`、深分页 `LIMIT 大偏移`

### 5.3 根因诊断 `rule_engine.py`

**目标**：把 EXPLAIN 信号确定性映射到已知根因，**规则优先，LLM 兜底**。

**规则表**（核心壁垒）：

| 编号 | 触发条件（信号） | 根因 | 建议类型 |
|---|---|---|---|
| R1 | `full_scan` 且 `no_available_index` | 缺索引导致全表扫描 | add_index |
| R2 | `no_index_used`（有 possible_keys 但没走） | 索引未被使用/被优化器放弃 | analyze/强制索引或改写 |
| R3 | `using_filesort` | ORDER BY 无法走索引，文件排序 | 建联合索引 / 改写 |
| R4 | `using_temporary` | GROUP BY/DISTINCT 产生临时表 | 建联合索引 / 改写 |
| R5 | `low_selectivity`（filtered<10） | 索引选择性差 | 换索引列 / 改写 |
| R6 | SQL 含 `LIKE '%x%'` | 前导通配符致索引失效 | 改写（全文索引/ES） |
| R7 | SQL 含 `func(col)` 谓词 | 列被函数包裹致索引失效 | 改写（列裸用） |
| R8 | `rows_examined >> rows_sent` 且 `LIMIT 大偏移` | 深分页 | 游标/延迟关联 |

**输出**：`Diagnosis` 对象（根因、置信度、证据列表、命中规则编号）。

### 5.4 LLM 诊断与优化建议 `llm_diagnoser.py`

**目标**：
1. 规则引擎未命中（或需要语义增强）时，用 LLM 做语义级根因诊断。
2. 无论规则是否命中，都生成结构化优化建议。

**Prompt 设计要点**：
- 输入：SQL 文本 + EXPLAIN 信号 + 表结构（`SHOW CREATE TABLE` 摘要）+ 规则引擎已有结论。
- 要求：给出根因 + 证据 + 优化建议，**必须以 JSON 输出**（Pydantic 约束）。
- 防幻觉：要求建议的索引必须基于真实表列名；不允许编造不存在的列。

**Pydantic 输出模型**：

```python
class Suggestion(BaseModel):
    type: Literal["add_index", "rewrite_sql", "tune_param", "other"]
    ddl: str | None          # add_index 时的 CREATE INDEX 语句
    sql: str | None          # rewrite_sql 时的改写后 SQL
    reason: str              # 为什么这么做

class DiagnosisResult(BaseModel):
    root_cause: str
    confidence: float        # 0~1
    evidence: list[str]      # EXPLAIN 证据
    matched_rules: list[str] # 命中的规则编号
    suggestions: list[Suggestion]
    llm_used: bool           # 是否走了 LLM（成本/可观测）
```

**成本控制**（呼应 TPC-H Agent）：统计每次 LLM 调用 token 数，规则命中的简单 case 可跳过 LLM（只走规则），降低 token 成本。

### 5.5 主流程 `agent.py`

```python
def diagnose(sql_or_file, db=None) -> DiagnosisResult:
    1. 若输入是文件 → slow_log_parser 解析出多条 SlowQuery；否则单条
    2. 对每条 SQL → explain_analyzer 取信号
    3. rule_engine 先跑规则
    4. 规则置信度不足 → llm_diagnoser 语义兜底 + 生成建议
    5. 返回 DiagnosisResult（含证据 + 建议）
```

### 5.6 评测 `evaluate.py`

**评测一：诊断命中率（核心指标）**

- 在 MySQL 造一张大表（`orders`，100 万行），字段含 `id / user_id / status / amount / create_time / remark`。
- 构造 **50 条已知根因**的慢查询（人工标注），覆盖 8 类根因：
  - 缺索引全表扫描（无索引的过滤列）
  - 前导通配符 `LIKE '%x%'`
  - 列被函数包裹 `WHERE DATE(create_time)=...`
  - ORDER BY 文件排序
  - GROUP BY 临时表
  - 深分页 `LIMIT 900000, 10`
  - 隐式类型转换
  - 索引选择性差（低区分度列）
- 跑 `diagnose()`，对比标注根因，计算 **命中率**（目标 ≥ 80%）。

**评测二：优化效果（证明"药方有效"）**

- 取若干条慢查询，执行 Agent 给出的索引 DDL（`CREATE INDEX`），对比：
  - EXPLAIN 的 `type / rows` 变化（如 `ALL → ref`、`rows 100万 → 10`）
  - 实际执行时间（`SET profiling` 或 `EXPLAIN ANALYZE`）
- 输出"优化前后"对照表，量化扫描行数下降数量级。

**指标汇总（写入 README）**：

| 指标 | 定义 | 目标 |
|---|---|---|
| 诊断命中率 | 标注测试集根因命中比例 | ≥ 80% |
| 扫描行数下降 | 加索引后 rows 变化 | 数量级下降 |
| 单条诊断 token | LLM 调用成本 | 规则命中时 ≈ 0 |

---

## 6. 数据模型与测试数据

### 6.1 大表设计（评测用）

```sql
CREATE TABLE orders (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL,       -- pending/paid/shipped/done
    amount DECIMAL(10,2) NOT NULL,
    create_time DATETIME NOT NULL,
    remark VARCHAR(200) DEFAULT NULL
);
```

- 造数：`INSERT` 100 万行，`status` 取 4 个离散值（低区分度，用于 R5/R8），`create_time` 均匀分布。
- 造数方式：PyMySQL + `executemany` 分批插入，或存储过程。

### 6.2 慢查询测试集（50 条）

- 存 `data/testset/slow_queries.json`，每条含 `sql / expected_root_cause / 说明`。
- 人工标注根因对应规则编号 R1~R8。

---

## 7. 可验证指标定义

| 指标 | 计算方式 | 可复现性 |
|---|---|---|
| 诊断命中率 | 命中条数 / 测试集总数 | 跑 `evaluate.py` 即得 |
| 优化前后 rows | EXPLAIN 两次对比 | 跑 `evaluate.py` 即得 |
| token 成本 | LLM 返回 usage 累计 | 诊断日志记录 |

---

## 8. 开发里程碑

| 阶段 | 内容 | 交付物 |
|---|---|---|
| M1（Day1-3） | 工程骨架 + 慢查询采集 + EXPLAIN 分析 | `slow_log_parser.py`、`explain_analyzer.py` + 单测 |
| M2（Day4-7） | 规则引擎 + LLM 诊断 + 优化建议 | `rule_engine.py`、`llm_diagnoser.py`、`agent.py` |
| M3（Day8-10） | 造大表 + 50 条测试集 + 评测 | `evaluate.py` + 命中率/优化对照 |
| M4（Day11-12） | FastAPI 接口 + README | 收尾文档 + GitHub |

---

## 9. 测试方案

| 层 | 文件 | 覆盖 |
|---|---|---|
| 单元 | `tests/test_slow_log_parser.py` | 解析正常/多查询/缺字段/管理语句 |
| 单元 | `tests/test_explain_analyzer.py` | 信号提取（需真机 MySQL） |
| 单元 | `tests/test_rule_engine.py` | 8 条规则的触发映射 |
| 集成 | `evaluate.py` | 命中率 + 优化对照 |

---

## 10. 诚实边界

- 数据是**自建测试数据**（100 万行），**不是**真实生产慢查询。
- 诊断命中率基于**自构造标注集**，非生产验证。
- 无真实 DBA 对照，但规则引擎参考了阿里云 DAS / 腾讯 DBBrain 的公开诊断思路。

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| 造 100 万行数据慢 | 分批 executemany + 关 autocommit，几分钟可完成 |
| LLM 输出不稳定 | Pydantic 校验 + 失败重试 + 温度设 0.1 |
| slow log 格式差异 | 解析器做多格式兼容 + 单测兜底 |
| 隐式类型转换检测需表结构 | EXPLAIN 阶段拉 `SHOW CREATE TABLE` 辅助判断 |

---

*文档版本 v1.0 · 2026-09-26*
