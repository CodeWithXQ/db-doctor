"""造数脚本：创建 db_doctor 库 + orders 大表 + 插入 100 万行测试数据。

索引策略（用于构造不同根因场景）：
- 主键 id
- idx_create_time(create_time)  —— 用于测 R7（函数包裹致索引失效）
- idx_status(status)            —— 用于测 R2/R5（低区分度）
- 无索引列：user_id / amount / remark —— 用于测 R1（缺索引）、R3/R4（filesort/temporary）
"""
from __future__ import annotations

import random
import string
from datetime import datetime, timedelta

import pymysql

from config import get_mysql_config

STATUSES = ["pending", "paid", "shipped", "done"]


def setup(rows: int = 1_000_000, batch: int = 5000) -> None:
    cfg = get_mysql_config()
    db = cfg["database"]

    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        charset=cfg["charset"],
    )
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}` DEFAULT CHARACTER SET utf8mb4")
    cur.execute(f"USE `{db}`")
    cur.execute("DROP TABLE IF EXISTS orders")
    cur.execute(
        """
        CREATE TABLE orders (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            user_id BIGINT NOT NULL,
            status VARCHAR(20) NOT NULL,
            amount DECIMAL(10,2) NOT NULL,
            create_time DATETIME NOT NULL,
            remark VARCHAR(200) DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    # 建两个索引，其余列保持无索引
    cur.execute("CREATE INDEX idx_create_time ON orders (create_time)")
    cur.execute("CREATE INDEX idx_status ON orders (status)")
    conn.commit()

    base = datetime(2025, 9, 26)
    total_batches = rows // batch
    print(f"开始插入 {rows} 行（每批 {batch}）...")
    for i in range(total_batches):
        data = [
            (
                random.randint(1, 100000),                              # user_id
                random.choice(STATUSES),                                 # status
                round(random.uniform(1, 10000), 2),                      # amount
                base - timedelta(days=random.randint(0, 365), seconds=random.randint(0, 86400)),
                "".join(random.choices(string.ascii_letters, k=random.randint(5, 30))),
            )
            for _ in range(batch)
        ]
        cur.executemany(
            "INSERT INTO orders (user_id, status, amount, create_time, remark) "
            "VALUES (%s, %s, %s, %s, %s)",
            data,
        )
        conn.commit()
        if (i + 1) % 40 == 0:
            print(f"  已插入 {(i + 1) * batch:,} 行")

    cur.close()
    conn.close()
    print("造数完成")


if __name__ == "__main__":
    import sys

    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    setup(rows)
