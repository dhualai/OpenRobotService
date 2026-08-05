"""
从服务器导出的三表（tickets / ticket_comments / users）合成评估数据集。

用法：
    1. 把三表导出为 JSON，放到 DATA_DIR 下
    2. python build_dataset.py   # → 输出 eval_dataset.json

合成逻辑：
    - 服务器 tickets.assigned_to (OpenID) → 服务器 users 查姓名
    - 姓名 → 本地 users 查 username
    - 最终 ground truth 存储为本地 username，可直接和 assigner 比对
"""

import json
import pymysql
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = DATA_DIR / "eval_dataset.json"


def load_json(name: str) -> list:
    path = DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"缺少文件: {path}，请把服务器导出的 {name} 放到 {DATA_DIR}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 兼容导出时被包在 dict 里的格式: {"users": [...]}
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
    if isinstance(data, list):
        return data
    return []


def build() -> List[dict]:
    tickets = load_json("tickets.json")
    comments = load_json("ticket_comments.json")
    users = load_json("users.json")

    # ── 服务器 users: OpenID → 姓名 ──
    server_user_name: Dict[str, str] = {}
    for u in users:
        uid = u.get("id", "").strip()
        username = u.get("username", "").strip()
        name = u.get("name") or ""
        if uid and name:
            server_user_name[uid] = name
        if username and name and username != uid:
            server_user_name[username] = name

    # ── 本地 users: 姓名 → username ──
    local_name_to_username: Dict[str, str] = {}
    try:
        conn = pymysql.connect(
            host="127.0.0.1", port=3306, user="root", password="123456",
            database="helpdesk_7_16", charset="utf8mb4",
        )
        cursor = conn.cursor()
        cursor.execute("SELECT username, name FROM users WHERE id != 'user_admin' AND name IS NOT NULL AND name != ''")
        for u, n in cursor.fetchall():
            if n:
                local_name_to_username[n] = u
        conn.close()
    except Exception as e:
        print(f"  警告: 无法连接本地数据库做姓名映射: {e}")

    # ── 构建评论索引: ticket_id → [comments] ──
    comment_index: Dict[int, list] = {}
    for c in comments:
        tid = c.get("ticket_id")
        if tid is None:
            continue
        comment_index.setdefault(int(tid), []).append({
            "content": c.get("content", ""),
            "created_by": c.get("created_by", ""),
            "created_at": c.get("created_at"),
        })

    # ── 合成 ──
    dataset = []
    skipped_no_assignee = 0
    skipped_no_info = 0

    for t in tickets:
        tid = t.get("id")
        assigned_to = (t.get("assigned_to") or "").strip()
        created_by = (t.get("created_by") or "").strip()

        if not assigned_to:
            skipped_no_assignee += 1
            continue
        if not t.get("title") and not t.get("description"):
            skipped_no_info += 1
            continue

        assigned_user = user_map.get(assigned_to, {})
        created_user = user_map.get(created_by, {})

        row = {
            "ticket_id": tid,
            "title": t.get("title", ""),
            "description": t.get("description", ""),
            "status": t.get("status", ""),
            "priority": t.get("priority", ""),
            "project_name": t.get("project_name"),
            "metadata_info": _parse_meta(t.get("metadata_info")),
            "assigned_to": assigned_to,
            "assignee_name": assigned_user.get("name", ""),
            "assignee_dept": assigned_user.get("department"),
            "assignee_modules": assigned_user.get("responsibility_modules"),
            "assignee_level": assigned_user.get("job_level", 1),
            "assignee_duty": assigned_user.get("duty_text"),
            "created_by": created_by,
            "creator_name": created_user.get("name", ""),
            "created_at": t.get("created_at"),
            "comments": comment_index.get(int(tid), []),
        }
        dataset.append(row)

    # ── 统计 ──
    print(f"合成完成:")
    print(f"  tickets 总数: {len(tickets)}")
    print(f"  有效工单: {len(dataset)}")
    print(f"  跳过(无指派人): {skipped_no_assignee}")
    print(f"  跳过(无信息): {skipped_no_info}")
    print(f"  评论条数: {len(comments)}")
    print(f"  用户数: {len(user_map)}")

    # ── 保存 ──
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    print(f"\n已保存: {OUTPUT_FILE}")

    return dataset


def _parse_meta(meta) -> dict:
    """安全解析 metadata_info（可能是 JSON 字符串或已解析的 dict）"""
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str):
        try:
            return json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


if __name__ == "__main__":
    build()
