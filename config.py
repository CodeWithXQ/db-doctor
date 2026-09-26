"""全局配置：路径、MySQL 连接、LLM 接入，均从环境变量 / .env 读取。

核心链路（slow log 解析 / EXPLAIN 分析 / 规则诊断 / 优化建议）全部自己实现，
不依赖 LangChain 等重框架，便于讲清每一步原理。
"""
from __future__ import annotations

import os
from pathlib import Path

# --- 项目路径 ---
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
SLOWLOG_DIR = DATA_DIR / "slowlog"
TESTSET_DIR = DATA_DIR / "testset"


def load_env(path: Path | None = None) -> None:
    """从 .env 读取 key=value 到 os.environ（已存在的变量不覆盖）。"""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_mysql_config() -> dict:
    """返回 MySQL 连接配置。"""
    load_env()
    return {
        "host": os.environ.get("MYSQL_HOST", "localhost"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "charset": "utf8mb4",
        "database": os.environ.get("MYSQL_DATABASE", ""),
    }


def get_llm_config() -> dict:
    """返回 LLM 接入配置（OpenAI 兼容协议，DeepSeek / 通义 / 智谱通用）。"""
    load_env()
    return {
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "model": os.environ.get("LLM_MODEL", "deepseek-chat"),
        "timeout": int(os.environ.get("LLM_TIMEOUT", "60")),
        "temperature": float(os.environ.get("LLM_TEMPERATURE", "0.1")),
        "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "1024")),
    }
