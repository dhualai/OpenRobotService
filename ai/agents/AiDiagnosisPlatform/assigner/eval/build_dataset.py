"""
从服务器导出的三表（tickets / ticket_comments / users）合成评估数据集。

用法：
    1. 把三表导出为 JSON，放到 DATA_DIR 下
    2. python build_dataset.py   # → 输出 eval_dataset.json

合成逻辑（只做标识映射 + 姓名反查，工单内容原样保留）：
    - 服务器 tickets.assigned_to / created_by（历史可能是 username 或 id）→ 服务器 users 反查姓名
    - 姓名 → 本地 users 查 id
    - 最终 ground truth 存储为本地 users.id，可直接和 assigner 比对
"""

import json
import pymysql
from pathlib import Path
from typing import Dict, List, Tuple

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


def _strip(value) -> str:
    return (value or "").strip() if isinstance(value, str) else str(value or "").strip()


def build() -> List[dict]:
    tickets = load_json("tickets.json")
    comments = load_json("ticket_comments.json")
    users = load_json("users.json")

    # ── 服务器 users: id / username → 姓名（反查）──
    # 历史导出里 assigned_to 可能是 username，新数据是 users.id，两边都建索引。
    server_id_to_name: Dict[str, str] = {}
    for u in users:
        uid = _strip(u.get("id"))
        username = _strip(u.get("username"))
        name = _strip(u.get("name"))
        if not name:
            continue
        if uid:
            server_id_to_name[uid] = name
        if username and username != uid:
            server_id_to_name[username] = name

    # ── 本地 users: 姓名 → id；id → 姓名 ──
    local_name_to_id: Dict[str, str] = {}
    local_id_to_name: Dict[str, str] = {}
    try:
        conn = pymysql.connect(
            host="127.0.0.1", port=3306, user="root", password="123456",
            database="helpdesk_7_16", charset="utf8mb4",
        )
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name FROM users WHERE id != 'user_admin' AND name IS NOT NULL AND name != ''"
        )
        for uid, n in cursor.fetchall():
            name = _strip(n)
            uid = _strip(uid)
            if name and uid:
                local_name_to_id[name] = uid
                local_id_to_name[uid] = name
        conn.close()
    except Exception as e:
        print(f"  警告: 无法连接本地数据库做姓名映射: {e}")

    def resolve_user(raw_id: str) -> Tuple[str, str]:
        """raw 标识 → (本地 users.id, 姓名)。映射不到时保留原标识。"""
        if not raw_id:
            return "", ""
        name = server_id_to_name.get(raw_id) or local_id_to_name.get(raw_id) or ""
        local_id = local_name_to_id.get(name) if name else ""
        if not local_id and raw_id in local_id_to_name:
            local_id = raw_id
            name = name or local_id_to_name[raw_id]
        return local_id or raw_id, name

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
        assigned_to_raw = _strip(t.get("assigned_to"))
        created_by_raw = _strip(t.get("created_by"))

        if not assigned_to_raw:
            skipped_no_assignee += 1
            continue
        if not t.get("title") and not t.get("description"):
            skipped_no_info += 1
            continue

        assigned_to, assignee_name = resolve_user(assigned_to_raw)
        created_by, creator_name = resolve_user(created_by_raw)

        row = {
            "ticket_id": tid,
            "title": t.get("title", ""),
            "description": t.get("description", ""),
            "status": t.get("status", ""),
            "priority": t.get("priority", ""),
            "project_name": t.get("project_name"),
            "metadata_info": _parse_meta(t.get("metadata_info")),
            "assigned_to": assigned_to,
            "assignee_name": assignee_name,
            "assignee_dept": None,
            "assignee_modules": None,
            "assignee_level": 1,
            "assignee_duty": None,
            "created_by": created_by,
            "creator_name": creator_name,
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
    print(f"  服务器用户反查: {len(server_id_to_name)}")
    print(f"  本地姓名→id: {len(local_name_to_id)}")

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
