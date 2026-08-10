"""生产环境一键创建 task_comment_read 表（实时已读回执，轻量 IM）。

用法（在 backend/ 目录下执行）：
    python apply_task_comment_read_migration.py

说明：
- 复用项目自身的 SQLAlchemy engine 幂等建表（checkfirst=True），
  表已存在则安全跳过，不破坏任何现有数据。
- 建表后 best-effort 把 Alembic 版本标记为 9f3b7c2a1d40，
  避免后续 `alembic upgrade` 重复执行该迁移。
- 不依赖 `alembic upgrade head`：当前仓库迁移历史存在多个未合并 head 分支，
  head 升级会报错。如要用 Alembic 统一管理，请先补一个 merge 迁移收口分支。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

REVISION = "9f3b7c2a1d40"


def ensure_table() -> None:
    from sqlalchemy import inspect
    from app.core.db import engine
    from app.models.task import TaskCommentRead

    inspector = inspect(engine)
    if inspector.has_table("task_comment_read"):
        print("[OK] task_comment_read 表已存在，跳过创建。")
        return

    TaskCommentRead.__table__.create(engine, checkfirst=True)
    print("[OK] task_comment_read 表创建成功。")


def stamp_alembic() -> None:
    try:
        from alembic.config import Config
        from alembic import command

        ini_path = os.path.join(HERE, "alembic.ini")
        if not os.path.exists(ini_path):
            print("[SKIP] 未找到 alembic.ini，跳过 Alembic stamp。")
            return
        cfg = Config(ini_path)
        if not cfg.get_main_option("script_location"):
            cfg.set_main_option("script_location", os.path.join(HERE, "alembic"))
        command.stamp(cfg, REVISION)
        print(f"[OK] Alembic 版本已标记为 {REVISION}。")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Alembic stamp 失败（不影响建表）: {e}")


def main() -> None:
    ensure_table()
    stamp_alembic()
    print("完成。")


if __name__ == "__main__":
    main()
