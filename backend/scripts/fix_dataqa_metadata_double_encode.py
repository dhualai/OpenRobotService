"""dataqa 表 metadata_ 双重编码修复脚本（幂等，可重复运行）。

背景：前端 AdminDataAssistant 持久化 assistant 消息时传 JSON.stringify 后的字符串，
后端 DataqaMessageService.create_message 曾无条件 safe_json_dumps（json.dumps），
导致 metadata_ 双重编码：'{"mode":...}' → '"{\\"mode\\":...}"'。
历史恢复时前端 JSON.parse 一次只得到字符串而非对象，mode/charts/cards 全部丢失，
表现为「历史会话中图表/卡片/口径标签消失」。

本脚本把已双重编码的 metadata_ 解回单次编码；已正常的行跳过，可安全重跑。
（前端 AdminDataAssistant 已加解析两次的兼容逻辑，本脚本用于清理存量数据。）

用法（在 backend/ 目录下，用项目 venv）：
    .venv/Scripts/python.exe scripts/fix_dataqa_metadata_double_encode.py
或：
    uv run python scripts/fix_dataqa_metadata_double_encode.py

连接参数默认同 app/core/config.py 的 DB_CONFIG（root/123456@127.0.0.1:3306/helpdesk），
可用 DATABASE_URL 环境变量覆盖：mysql+pymysql://user:pass@host:port/db
"""
import json
import os
import re
import sys

import pymysql

# 待修复的表（dataqa 会话与消息表；白名单固定，无注入风险）
TABLES = ("dataqa_messages", "dataqa_conversations")


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


def _fix_metadata(raw):
    """双重编码检测与修复：返回 (新值, 是否变更)。

    - 双重编码（外层是 JSON 字符串，内层是 JSON 对象/数组）→ 解一层写回单次编码；
    - 已正常（单次编码对象/数组）或不可解析 → 原样返回。
    """
    if raw is None or not str(raw).strip():
        return raw, False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw, False

    if isinstance(parsed, str):
        # 外层解析出字符串：疑似双重编码，验证内层是否为合法 JSON 结构
        try:
            inner = json.loads(parsed)
        except (json.JSONDecodeError, TypeError):
            return raw, False
        if isinstance(inner, (dict, list)):
            return parsed, True  # 解一层：写回单次编码字符串
    return raw, False


def main() -> int:
    cfg = _db_config()
    conn = pymysql.connect(
        host=cfg["host"], user=cfg["user"], password=cfg["password"],
        port=cfg["port"], database=cfg["database"], charset="utf8mb4",
    )
    cur = conn.cursor()
    total_fixed = 0
    try:
        for table in TABLES:
            cur.execute(f"SELECT id, metadata_ FROM `{table}` WHERE metadata_ IS NOT NULL")
            rows = cur.fetchall()
            fixed = 0
            for rid, raw in rows:
                new_val, changed = _fix_metadata(raw)
                if changed:
                    cur.execute(
                        f"UPDATE `{table}` SET metadata_ = %s WHERE id = %s",
                        (new_val, rid),
                    )
                    fixed += 1
            conn.commit()
            total_fixed += fixed
            print(f"[{table}] 扫描 {len(rows)} 行，修复双重编码 {fixed} 行")
    finally:
        conn.close()
    print(f"完成：共修复 {total_fixed} 行 metadata_ 双重编码")
    return 0


if __name__ == "__main__":
    sys.exit(main())
