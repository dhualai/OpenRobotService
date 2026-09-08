"""dataqa 数据助手会话/消息表初始化命令（幂等，可重复运行）。

背景：dataqa 模块（AI 数据助手会话管理）使用专属表
dataqa_conversations / dataqa_messages，不复用摇人对话的 conversations/messages。
存量库需要本脚本补建；全新库可由 init_fqa_db 的 create_all 自动创建
（模型已注册进 Base.metadata）。

用法（在 backend/ 目录下，用项目 venv）：
    .venv/Scripts/python.exe scripts/init_dataqa_tables.py
或：
    uv run python scripts/init_dataqa_tables.py

连接参数默认同 app/core/config.py 的 DB_CONFIG（root/123456@127.0.0.1:3306/helpdesk），
可用 DATABASE_URL 环境变量覆盖：mysql+pymysql://user:pass@host:port/db
"""
import os
import re
import sys

import pymysql

# 与 app/core/config.py DB_CONFIG 保持一致；DATABASE_URL 可覆盖
def _db_config() -> dict:
    url = os.environ.get("DATABASE_URL")
    if url:
        m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)", url)
        if m:
            return {
                "user": m.group(1), "password": m.group(2),
                "host": m.group(3), "port": int(m.group(4)),
                "database": m.group(5),
            }
    return {"user": "root", "password": "123456", "host": "127.0.0.1",
            "port": 3306, "database": "helpdesk"}

# 建表 DDL（与 app/models/dataqa.py 的 ORM 定义一致；
# 原生 ENUM 存枚举成员名：TEXT/IMAGE/...、USER/ASSISTANT/SYSTEM）
DDL = [
    """
    CREATE TABLE IF NOT EXISTS `dataqa_conversations` (
      `id` INT NOT NULL AUTO_INCREMENT,
      `title` VARCHAR(255) NOT NULL COMMENT '会话标题（首问截断）',
      `user_id` VARCHAR(255) NOT NULL COMMENT '归属用户ID（后端按 token 覆盖）',
      `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      `metadata_` TEXT NULL,
      PRIMARY KEY (`id`),
      KEY `ix_dataqa_conversations_id` (`id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='AI 数据助手会话（独立于摇人 conversations）'
    """,
    """
    CREATE TABLE IF NOT EXISTS `dataqa_messages` (
      `id` INT NOT NULL AUTO_INCREMENT,
      `message_type` ENUM('TEXT','IMAGE','FILE','AUDIO','MULTIMODAL') NOT NULL DEFAULT 'TEXT',
      `conversation_id` INT NOT NULL COMMENT '所属会话ID',
      `role` ENUM('USER','ASSISTANT','SYSTEM') NOT NULL COMMENT '消息角色',
      `content` TEXT NOT NULL COMMENT '消息内容',
      `file_urls` TEXT NULL,
      `parent_message_id` INT NULL,
      `sequence` INT NOT NULL DEFAULT 0 COMMENT '会话内序号（自增）',
      `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `metadata_` TEXT NULL,
      PRIMARY KEY (`id`),
      KEY `ix_dataqa_messages_id` (`id`),
      KEY `ix_dataqa_messages_conversation_id` (`conversation_id`),
      CONSTRAINT `fk_dataqa_messages_conversation` FOREIGN KEY (`conversation_id`)
        REFERENCES `dataqa_conversations` (`id`) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='AI 数据助手消息（独立于摇人 messages）'
    """,
]


def main() -> int:
    cfg = _db_config()
    conn = pymysql.connect(
        host=cfg["host"], user=cfg["user"], password=cfg["password"],
        port=cfg["port"], database=cfg["database"], charset="utf8mb4",
    )
    cur = conn.cursor()
    try:
        for ddl in DDL:
            # 从 DDL 里抠表名打日志
            m = re.search(r"CREATE TABLE IF NOT EXISTS `([^`]+)`", ddl)
            table = m.group(1) if m else "?"
            cur.execute(f"SHOW TABLES LIKE '{table}'")
            if cur.fetchone():
                print(f"[SKIP] {table} 已存在")
                continue
            cur.execute(ddl)
            print(f"[ADD] {table} 创建成功")
        conn.commit()
    finally:
        conn.close()
    print("完成：dataqa 会话/消息表已就绪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
