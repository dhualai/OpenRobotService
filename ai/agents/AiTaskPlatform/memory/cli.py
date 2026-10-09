"""查看、删除 U老师 长期记忆。

用法（在仓库根目录）::

    python -m ai.agents.AiTaskPlatform.memory.cli list
    python -m ai.agents.AiTaskPlatform.memory.cli delete <id>

要改一条记忆：先删掉，再在讨论里对 U老师 说「记住 …」。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ai.agents.AiTaskPlatform.memory.agent_memory_service import get_agent_memory_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查看或删除 U老师 长期记忆")
    parser.add_argument("--dir", default="", help="记忆目录，默认用服务内置目录")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出仍生效的记忆")
    p_del = sub.add_parser("delete", help="按 id 删除一条")
    p_del.add_argument("mem_id")
    args = parser.parse_args(argv)

    svc = get_agent_memory_service(Path(args.dir) if args.dir else None)
    if args.cmd == "list":
        rows = svc.list_entries(status="active")
        if not rows:
            print("（没有生效中的记忆）")
            return 0
        for rec in rows:
            content = (rec.get("content") or "").replace("\n", " ")
            print(f"{rec.get('id')}\t{rec.get('created_at') or ''}\t{content}")
        return 0

    mem_id = (args.mem_id or "").strip()
    if not mem_id:
        print("需要记忆 id", file=sys.stderr)
        return 1
    removed = asyncio.run(svc.delete(mem_id))
    if not removed:
        print(f"没有这条记忆：{mem_id}")
        return 1
    print(f"已删除 {mem_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
