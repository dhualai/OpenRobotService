"""把双树试做的 JSON 快照灌进责任双树三张表（一次性迁移，可重复跑）。

逻辑在 `resp_tree_service.import_trial_snapshot`；本脚本只负责读文件与 CLI。

用法（在 backend/ 目录下，用项目 venv）：
    ../.venv/Scripts/python.exe scripts/migrate_dual_tree_snapshot.py --dry-run
    ../.venv/Scripts/python.exe scripts/migrate_dual_tree_snapshot.py --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.modules.admin.services import resp_tree_service as svc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / ".local" / "责任双树" / "dual-tree-trial.snapshot.json"


def _read_snapshot(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise SystemExit(f"快照格式不对：{path}")
    return {"nodes": data.get("nodes") or [], "mounts": data.get("mounts") or []}


async def migrate(snapshot: Dict[str, Any], *, force: bool, dry_run: bool) -> int:
    nodes = snapshot["nodes"]
    mounts = snapshot["mounts"]
    domains = [n for n in nodes if n.get("kind") == "domain"]
    skills = [n for n in nodes if n.get("kind") == "skill"]
    orgs = [n for n in nodes if n.get("kind") == "org"]

    if dry_run:
        print(f"[DRY] 域 {len(domains)} / 组织 {len(orgs)} / 技能 {len(skills)} / 挂载 {len(mounts)}")
        return 0

    async with AsyncSessionLocal() as db:
        try:
            result = await svc.import_trial_snapshot(db, snapshot, force=force)
        except ValueError as exc:
            print(f"[STOP] {exc}")
            return 1
        print(
            f"[OK] skill 树 {result['domains']} 域 / {result['skills']} 技能；"
            f"org 树 {result['domains']} 域 / {result['orgs']} 组织；"
            f"挂载 {result['mounts']}（跳过 {result.get('skipped_mounts', 0)}）"
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把双树试做快照灌进 resp_* 三张表")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT,
                        help=f"快照 JSON 路径（默认 {DEFAULT_SNAPSHOT}）")
    parser.add_argument("--force", action="store_true", help="先清空两棵树的节点与挂载再灌")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    if not args.snapshot.exists():
        print(f"[ERR] 找不到快照：{args.snapshot}")
        return 1
    snapshot = _read_snapshot(args.snapshot)
    return asyncio.run(migrate(snapshot, force=args.force, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
