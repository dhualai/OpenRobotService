"""一次性迁移脚本：把 Assigner config.yaml 的部门画像(departments)写入 DB departments 表。

背景：DB departments 表原本没有部门职责描述字段；AI 派单 R2 部门分类此前只能读
config.yaml 手写的 profile_text。本脚本将 config.yaml 的部门画像（profile_text + examples）
按部门名写入 DB 对应 approved 部门行，之后 AI 便能从 DB 加载（可热更新）。

幂等：仅当目标部门当前 profile_text 为空时才写入；已存在则跳过（避免覆盖后续手工维护）。

运行：python scripts/migrate_department_profiles.py
需要已执行 alembic upgrade head（departments 表已有 profile_text/examples 列）。
"""
import sys
from pathlib import Path

import yaml

# 路径
_PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT))

from app.core.db import SessionLocal  # noqa: E402
from app.models.organization import Department  # noqa: E402

_ASSIGNER_CONFIG = (
    _PROJECT.parent
    / "ai" / "agents" / "AiDiagnosisPlatform" / "assigner" / "config" / "config.yaml"
)


def main() -> None:
    with open(_ASSIGNER_CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    departments = cfg.get("departments") or []
    print(f"config.yaml 部门画像数: {len(departments)}")

    db = SessionLocal()
    try:
        updated = skipped = 0
        for d in departments:
            name = (d.get("name") or "").strip()
            profile = (d.get("profile_text") or "").strip()
            if not name or not profile:
                continue
            row = db.query(Department).filter(
                Department.name == name,
                Department.status == 'approved',
            ).first()
            if not row:
                print(f"  [跳过] 未找到 approved 部门「{name}」")
                continue
            if (row.profile_text or "").strip():
                print(f"  [跳过] 「{name}」已有 profile_text，保留")
                skipped += 1
                continue
            row.profile_text = profile
            row.examples = d.get("examples") or []
            updated += 1
            print(f"  [写入] 「{name}」 profile_text + {len(row.examples)} 个示例")
        db.commit()
        print(f"完成：写入 {updated} 个，跳过 {skipped} 个。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
