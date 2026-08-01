#!/usr/bin/env python3
"""修复 AI 转工单 created_by 异常（空 / "system" / "unknown"）的存量工单。

根因（已根治，见 git 历史）：
  AI 服务 token 解析失败（SECRET_KEY 缺失 / token 过期）时 _current_user 返回空，
  历史工单 created_by 写入空串或旧兜底 "system"/"unknown"，
  导致历史工单列表按用户过滤（Task.created_by == username）查不到、提单人显示异常。

修复链路（谁创建的会话，工单就归谁）：
  tasks.metadata_info.session_id            （AI 写入的完整 session_id）
    → conversations.metadata_.ai_session_id （前端建会话时写入，JSON 可能双重编码）
    → conversations.user_id                 （= users.id，token 里的用户主键）
    → users.username
    → 回填 tasks.created_by = username

  显示名无需单独修：列表接口 created_by_name = user_map.get(created_by)，
  只要 created_by 是正确 username，展示名自动正确。

用法（生产服务器项目根目录执行，需能连生产 MySQL，读 backend/.env 的 DATABASE_URL）：
  python scripts/fix_task_created_by.py                            # dry-run：只打印，不写库
  python scripts/fix_task_created_by.py --apply                    # 写库（仅自动可定位的）
  python scripts/fix_task_created_by.py --apply --default-user=jqh # 写库，无法定位的统一归给指定用户

无法自动定位的工单（无 session_id / 会话已删 / 用户已删）：
  - 用 --default-user=<username> 统一归给指定用户（生产使用者少时可人工确认后批量指定）；
  - 或单条人工修复：UPDATE tasks SET created_by='<username>' WHERE id=<task_id>;
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.db import SessionLocal  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.identity import UserDB  # noqa: E402

# 历史异常值：空串（token 失效兜底）/ system（旧 task_adapter 兜底）/ unknown（旧 submit 兜底）
BAD_CREATED_BY = ("", "system", "unknown")


def parse_ai_session_id(metadata_: str | None) -> str:
    """conversations.metadata_ 可能是双重 JSON 编码（safe_json_dumps 对已序列化字符串再次 dumps），
    parse 一次后若仍是字符串需再 parse 一次。"""
    if not metadata_:
        return ""
    try:
        obj = json.loads(metadata_)
        if isinstance(obj, str):
            obj = json.loads(obj)
        return (obj or {}).get("ai_session_id", "") or ""
    except Exception:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="修复 AI 工单 created_by 异常（空/system/unknown）")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run 只打印）")
    parser.add_argument("--default-user", metavar="USERNAME",
                        help="无法自动定位归属的工单统一归给该 username（需在 users 表存在）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # 1. users.id → username（conversations.user_id 存的是 users.id，tasks.created_by 要 username）
        users = {u.id: u.username for u in db.query(UserDB.id, UserDB.username).all()}
        print(f"users 表:{len(users)} 个用户")

        # --default-user 校验：必须是存在的 username
        default_user = None
        if args.default_user:
            if args.default_user not in users.values():
                print(f"错误:--default-user={args.default_user!r} 在 users 表不存在,可选:{sorted(users.values())}")
                sys.exit(1)
            default_user = args.default_user

        # 2. ai_session_id → (user_id, updated_at)。同一 session 可能有多个会话（用户重建过），取最新的。
        session_owner: dict[str, tuple[str, object]] = {}
        for conv in db.query(Conversation).all():
            sid = parse_ai_session_id(conv.metadata_)
            if not sid or not conv.user_id:
                continue
            prev = session_owner.get(sid)
            if prev is None or (conv.updated_at and (prev[1] is None or conv.updated_at > prev[1])):
                session_owner[sid] = (conv.user_id, conv.updated_at)
        print(f"conversations 表:可定位 {len(session_owner)} 个 AI 会话的归属用户")

        # 3. 异常工单（AI 来源 + created_by 异常）
        bad_tasks = (
            db.query(Task)
            .filter(Task.source == "ai", Task.created_by.in_(BAD_CREATED_BY))
            .all()
        )
        print(f"异常工单（source=ai 且 created_by in {BAD_CREATED_BY}）:{len(bad_tasks)} 条\n")

        fixed, failed = 0, []

        def do_fix(t, username: str, via: str) -> None:
            nonlocal fixed
            print(f"[{'APPLY' if args.apply else 'DRY '}] task#{t.id} {(t.title or '')[:30]!r}: "
                  f"{t.created_by!r} → {username!r}  ({via})")
            if args.apply:
                t.created_by = username
            fixed += 1

        for t in bad_tasks:
            meta = t.metadata_info or {}
            # 完整 session_id 优先取 metadata_info.session_id；退化用 external_id 去掉 #seq 后缀
            sid = meta.get("session_id", "") or (t.external_id or "").split("#")[0]
            reason = None
            username = None
            if not sid:
                reason = "无 session_id"
            else:
                owner = session_owner.get(sid)
                if not owner:
                    reason = f"会话 {sid} 在 conversations 表无匹配（可能已删除）"
                else:
                    username = users.get(owner[0])
                    if not username:
                        reason = f"user_id={owner[0]} 在 users 表不存在（用户已删？）"
            if username:
                do_fix(t, username, f"session={sid}")
            elif default_user:
                do_fix(t, default_user, f"default-user;原因:{reason}")
            else:
                failed.append((t.id, t.title, reason))

        if args.apply:
            db.commit()
            print(f"\n已写库修复 {fixed} 条。")
        else:
            print(f"\ndry-run:可修复 {fixed} 条；确认无误后加 --apply 实际写库。")

        if failed:
            print(f"\n无法自动修复 {len(failed)} 条（需人工核对，或加 --default-user 统一归属）:")
            for tid, title, reason in failed:
                print(f"  task#{tid} {(title or '')[:40]!r}: {reason}")
            print("人工修复 SQL: UPDATE tasks SET created_by='<username>' WHERE id=<task_id>;")
    finally:
        db.close()


if __name__ == "__main__":
    main()
