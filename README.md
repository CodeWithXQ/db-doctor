# MySQL 慢查询 AI 诊断与优化 Agent（DB Doctor）

> 给数据库做一套「AI 医生」：读懂慢查询的体检报告（EXPLAIN 执行计划），说出为什么慢（根因诊断），开出能实测见效的药方（建索引 / 改写 SQL / 调参），再用自建 100 万行大表量化「诊断命中率」与「优化前后性能差」。
>
> 已在本机跑通：**诊断命中率 56 条命中 50（89.3%）**、**3 条慢查询优化后 `全表扫描 100 万行 → 索引扫描个位数行`**。

## 为什么做（抗「假需求」质疑）

数据库性能调优是**真金白银的付费刚需**，不是编造的场景：

| 付费参照 | 云厂商产品 |
|---|---|
| 阿里云 | DAS 数据库自治服务（按实例收费） |
| 腾讯云 | DBBrain 智能管家 |
| AWS | DevOps Guru for RDS |

慢查询定位是 DBA 日常最耗时的活，一条慢 SQL 能拖垮整个库，云厂商在收钱解决的就是这件事。

## 核心亮点

1. **底层自主实现**：slow log 解析、EXPLAIN 信号提取、根因规则引擎全部自己写，不依赖 LangChain / sqlglot 等重框架，能讲清每一步原理。
2. **规则 + LLM 双层诊断**：规则引擎覆盖 8 类确定性根因（token=0、可解释），LLM 只兜语义级复杂 case（如「子查询改写 join」）。
3. **优化建议可实测**：LLM 生成的索引 DDL 基于真实表结构，执行后用 EXPLAIN 验证真的生效（`type=ALL → ref`、`rows 100万 → 8`）。

## 架构

```
slow log / 单条 SQL ─▶ ① 慢查询采集 slow_log_parser.py
                         提取 SQL / query_time / rows_examined
                              │
                              ▼
                      ② 执行计划分析 explain_analyzer.py  ◀── MySQL 8.0（真机 EXPLAIN）
                         提取 type / key / rows / Extra 信号
                              │
                              ▼
                      ③ 根因诊断  rule_engine.py（自写 8 条规则，token=0）
                              +  llm_diagnoser.py（LLM 语义兜底）◀── DeepSeek
                              │
                              ▼
                      ④ 优化建议 llm_diagnoser.py
                         结构化 JSON（索引 DDL / SQL 改写 / 调参）
                              │
                              ▼
                      ⑤ 评测验证 evaluate.py
                         诊断命中率 + 优化前后 EXPLAIN 对比
```

## 环境要求

| 组件 | 要求 | 本机参考 |
|---|---|---|
| Python | 3.10+ | 3.13.9（Anaconda） |
| MySQL | 8.0+（真机） | 8.0.44，root 连接 |
| LLM | DeepSeek（OpenAI 兼容） | .env 配置 key |

## 快速开始

### Step 1：配置 .env

复制 `.env.example` 为 `.env`，填入 MySQL 密码和 LLM key（`.env` 已 gitignore，不会提交）。

### Step 2：造数（100 万行，约 1-2 分钟）

```bash
python seed_data.py 1000000
```

> 会在 MySQL 建 `db_doctor` 库 + `orders` 表（含主键 id、索引 idx_create_time / idx_status，其余列故意不建索引以构造缺索引场景）。

### Step 3：跑评测

```bash
python evaluate.py
```

期望输出：
- 诊断命中率 `56 条命中 50（89.3%）`
- 3 条慢查询优化前后对比：`type=ALL, rows=1000566 → type=ref, rows=8/1/1`

### Step 4：命令行诊断单条 SQL

```bash
# 纯规则（token=0，秒回）
python cli.py --sql "SELECT * FROM orders WHERE user_id=12345"

# 允许 LLM 生成具体索引 DDL / 改写建议
python cli.py --sql "SELECT * FROM orders WHERE user_id=12345" --llm
```

### Step 5（可选）：FastAPI 接口

```bash
uvicorn app:app --port 9091
# POST http://localhost:9091/diagnose  body: {"sql": "...", "allow_llm": true}
```

## 评测结果（2026-09-26 实测，可复现）

### 诊断命中率

- 56 条人工标注根因测试集，覆盖 8 类根因（50 条常见慢查询规则全中，6 条组合缺索引规则漏判、需 LLM 兜底）
- **命中率 89.3%（56 条命中 50）**，纯规则引擎，token=0

### 优化前后对比（EXPLAIN）

| 慢查询 | 优化前 | LLM 建议索引 | 优化后 |
|---|---|---|---|
| `WHERE user_id=12345` | type=ALL, rows=1000566 | `idx_user_id(user_id)` | type=ref, rows=8 |
| `WHERE remark='helloworld'` | type=ALL, rows=1000566 | `idx_remark(remark)` | type=ref, rows=1 |
| `WHERE amount=8888` | type=ALL, rows=1000566 | `idx_amount(amount)` | type=ref, rows=1 |

### 一个诚实的反面发现

对低选择性**范围查询** `WHERE amount>5000`（约过滤掉 50% 数据），LLM 建议建索引后，MySQL 优化器**依然选择全表扫描**（建了索引也不用）。这印证了「不是所有列都该建索引」——低区分度列建索引无益，是真实观察到的现象，不是编的结论。

## 诚实边界

- 数据是**自建测试数据**（100 万行 orders），**不是**真实生产慢查询。
- 命中率基于**自构造标注集**（8 类根因，含 6 条组合缺索引盲区），非生产验证；89.3% 是因为 6 条组合缺索引规则漏判、需 LLM 兜底。
- 无真实 DBA 对照，规则引擎参考了阿里云 DAS / 腾讯 DBBrain 公开诊断思路。

## 项目结构

```
db-doctor/
├─ DESIGN.md              # 设计开发文档
├─ config.py              # .env 读取 + MySQL/LLM 连接配置
├─ slow_log_parser.py     # ① 慢查询采集
├─ explain_analyzer.py    # ② EXPLAIN 信号提取
├─ rule_engine.py         # ③ 根因规则引擎（8 条规则）
├─ llm_diagnoser.py       # ③④ LLM 诊断 + 优化建议
├─ agent.py               # 主流程编排
├─ evaluate.py            # ⑤ 评测
├─ seed_data.py           # 造数脚本
├─ cli.py                 # 命令行入口
├─ app.py                 # FastAPI 接口
└─ tests/                 # 单元测试（12 个）
```
