"""
Qdrant 快照导出/导入工具

使用 Qdrant HTTP API 对活跃 collection 创建/下载/上传/恢复快照。
适用于 WSL Docker 模式——快照通过 HTTP 传输，容器内的 snapshot 目录不可直接访问。

导出：
    python -m ai.ingestion.snapshot_manager export --dir ./snapshots
    → 对 operation_docs / faq / troubleshooting 三个活跃 collection 各导出一个 .snapshot 文件

导入：
    python -m ai.ingestion.snapshot_manager import --dir ./snapshots
    → 读取目录下所有 .snapshot 文件，恢复到 Qdrant 并自动设置活跃指针

用法：
    python -m ai.ingestion.snapshot_manager export          # 导出到默认目录
    python -m ai.ingestion.snapshot_manager import --dir ./snapshots  # 从指定目录导入
    python -m ai.ingestion.snapshot_manager list            # 列出活跃 collection 及快照
    python -m ai.ingestion.snapshot_manager --help
"""
import sys
import json
import time
import argparse
import asyncio
from pathlib import Path
from typing import List, Dict, Tuple
from urllib.parse import quote

import httpx

# 确保项目根在 path 中
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

DEFAULT_SNAPSHOT_DIR = _project_root / "ai" / "snapshots"


def _qdrant_base() -> str:
    from ai.config import get_ai_config
    cfg = get_ai_config()
    return f"http://{cfg.qdrant_host}:{cfg.qdrant_port}"


def _get_active_collections() -> List[Tuple[str, str]]:
    """
    返回 [(collection_type_label, collection_name), ...]
    type_label: "op" | "faq" | "troubleshooting"
    """
    from ai.config import (
        get_active_collection,
        get_active_faq_collection,
        get_active_troubleshooting_collection,
    )
    pairs = []
    for label, name in [
        ("op", get_active_collection()),
        ("faq", get_active_faq_collection()),
        ("troubleshooting", get_active_troubleshooting_collection()),
    ]:
        if name:
            pairs.append((label, name))
    return pairs


# ============================================================
# 导出
# ============================================================

async def export_snapshots(output_dir: str) -> Dict[str, Path]:
    """
    给所有活跃 collection 创建快照并下载为 .snapshot 文件。
    返回 {collection_label: snapshot_file_path}
    """
    base = _qdrant_base()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    collections = _get_active_collections()
    if not collections:
        print("[ERR] 没有找到活跃 collection，请先入库")
        return {}

    results = {}
    async with httpx.AsyncClient(timeout=120.0) as client:
        for label, col_name in collections:
            print(f"\n{'=' * 50}")
            print(f"[{label}] {col_name}")

            # 1. 创建快照
            create_url = f"{base}/collections/{quote(col_name)}/snapshots"
            print(f"  创建快照中...")
            resp = await client.post(create_url, timeout=60.0)
            if resp.status_code != 200:
                print(f"  [ERR] 创建快照失败: {resp.status_code} {resp.text}")
                continue
            snapshot_info = resp.json()
            snapshot_name = snapshot_info.get("result", {}).get("name", "")
            if not snapshot_name:
                print(f"  [ERR] 无法获取快照名: {snapshot_info}")
                continue
            print(f"  快照已创建: {snapshot_name}")

            # 2. 下载快照文件
            download_url = f"{base}/collections/{quote(col_name)}/snapshots/{quote(snapshot_name)}"
            print(f"  下载中...")
            resp = await client.get(download_url, timeout=120.0)
            if resp.status_code != 200:
                print(f"  [ERR] 下载失败: {resp.status_code}")
                continue

            out_path = output / f"{label}_{col_name}.snapshot"
            out_path.write_bytes(resp.content)
            size_mb = len(resp.content) / (1024 * 1024)
            print(f"  [OK] 已保存: {out_path} ({size_mb:.1f} MB)")
            results[label] = out_path

    print(f"\n[OK] 导出完成: {len(results)} 个快照 -> {output}")
    return results


# ============================================================
# 导入
# ============================================================

async def import_snapshots(snapshot_dir: str) -> List[str]:
    """
    扫描目录下所有 .snapshot 文件，恢复到 Qdrant 并设置活跃指针。

    文件名格式: {label}_{collection_name}.snapshot
      例: op_operation_docs_20260716_120000.snapshot
          faq_faq_docs_20260716_120000.snapshot
          troubleshooting_troubleshooting_20260716_120000.snapshot

    恢复后自动写入对应的指针文件。
    """
    from ai.config import (
        _write_active_collection,
        _write_active_faq_collection,
        _write_active_troubleshooting_collection,
        get_active_collection,
        get_active_faq_collection,
        get_active_troubleshooting_collection,
    )

    base = _qdrant_base()
    snap_dir = Path(snapshot_dir)
    if not snap_dir.is_dir():
        print(f"[ERR] 目录不存在: {snap_dir}")
        return []

    snapshots = sorted(snap_dir.glob("*.snapshot"))
    if not snapshots:
        print(f"[ERR] 目录下没有 .snapshot 文件: {snap_dir}")
        return []

    print(f"找到 {len(snapshots)} 个快照文件:")
    for s in snapshots:
        size_mb = s.stat().st_size / (1024 * 1024)
        print(f"  - {s.name} ({size_mb:.1f} MB)")

    label_writers = {
        "op": ("操作手册", _write_active_collection),
        "faq": ("FAQ", _write_active_faq_collection),
        "troubleshooting": ("排查树", _write_active_troubleshooting_collection),
    }

    existing_collections = set()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/collections")
            if resp.status_code == 200:
                data = resp.json()
                existing_collections = {
                    c["name"] for c in data.get("result", {}).get("collections", [])
                }
    except Exception:
        pass

    imported = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for snap_path in snapshots:
            fname = snap_path.stem  # e.g. "op_operation_docs_20260716_120000"
            parts = fname.split("_", 1)
            if len(parts) < 2:
                print(f"\n[WARN] 无法解析文件名: {snap_path.name}，跳过")
                continue
            label = parts[0]
            col_name = parts[1]

            label_info = label_writers.get(label)
            if not label_info:
                print(f"\n[WARN] 未知类型前缀 '{label}': {snap_path.name}，跳过")
                continue
            label_desc, write_func = label_info

            print(f"\n{'=' * 50}")
            print(f"[{label}] 恢复 {label_desc} -> {col_name}")

            # 检查 collection 是否已存在
            if col_name in existing_collections:
                print(f"  collection 已存在，跳过")
                # 但仍然更新指针
                write_func(col_name)
                print(f"  [OK] 指针已指向: {col_name}")
                imported.append(col_name)
                continue

            # 1. 上传快照到 Qdrant 服务器
            upload_url = f"{base}/collections/{quote(col_name)}/snapshots/upload"
            print(f"  上传快照文件...")
            snap_data = snap_path.read_bytes()
            resp = await client.post(
                upload_url,
                content=snap_data,
                timeout=120.0,
            )
            if resp.status_code not in (200, 201, 202):
                print(f"  [ERR] 上传失败: {resp.status_code} {resp.text}")
                continue
            print(f"  上传成功 ({resp.status_code})")

            # 2. 恢复
            recover_url = f"{base}/collections/{quote(col_name)}/snapshots/recover"
            print(f"  恢复中...")
            resp = await client.put(
                recover_url,
                json={"location": f"{col_name}.snapshot"},
                timeout=60.0,
            )
            if resp.status_code not in (200, 202):
                print(f"  [ERR] 恢复失败: {resp.status_code} {resp.text}")
                continue
            print(f"  恢复请求已提交")

            # 等待恢复完成（简单轮询）
            for attempt in range(30):
                await asyncio.sleep(2)
                try:
                    check_resp = await client.get(
                        f"{base}/collections/{quote(col_name)}",
                        timeout=5.0,
                    )
                    if check_resp.status_code == 200:
                        status = check_resp.json().get("result", {}).get("status", "")
                        if status == "green":
                            print(f"  恢复完成 (status=green)")
                            break
                        print(f"  ...状态: {status}")
                except Exception:
                    pass
            else:
                print(f"  [WARN] 等待超时，但恢复可能仍在进行中")

            # 3. 写指针
            write_func(col_name)
            print(f"  [OK] 指针已指向: {col_name}")
            imported.append(col_name)

    print(f"\n[OK] 导入完成: {len(imported)} 个 collection 已就绪")
    return imported


# ============================================================
# 列表
# ============================================================

async def list_collections_and_snapshots():
    """列出所有 collection 及活跃状态"""
    base = _qdrant_base()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 获取所有 collection
        resp = await client.get(f"{base}/collections")
        if resp.status_code != 200:
            print(f"[ERR] 无法连接 Qdrant: {resp.status_code}")
            return
        data = resp.json()
        all_cols = data.get("result", {}).get("collections", [])

    active = dict(_get_active_collections())

    label_names = {"op": "操作手册", "faq": "FAQ", "troubleshooting": "排查树"}
    active_names = {v for _, v in _get_active_collections()}

    print(f"\n{'Collection':<50} {'状态':<10} {'活跃指针'}")
    print("-" * 85)
    for c in sorted(all_cols, key=lambda x: x["name"]):
        name = c["name"]
        is_active = "★" if name in active_names else " "
        # 判断类型
        label = ""
        for lb, label_name in label_names.items():
            if name.startswith(lb if lb != "op" else "operation") or \
               name.startswith("faq") or name.startswith("troubleshooting"):
                pass
        status = c.get("status", "?")
        print(f"{is_active} {name:<48} {status:<10}")

    print(f"\n活跃指针:")
    for label, col_name in sorted(active.items()):
        print(f"  {label_names.get(label, label)}: {col_name}")


# ============================================================
# CLI
# ============================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Qdrant 快照导出/导入",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m ai.ingestion.snapshot_manager export --dir ./snapshots
  python -m ai.ingestion.snapshot_manager import --dir ./snapshots
  python -m ai.ingestion.snapshot_manager list
        """,
    )
    sub = parser.add_subparsers(dest="cmd", help="子命令")

    ep = sub.add_parser("export", help="导出活跃 collection 的快照")
    ep.add_argument("--dir", default=str(DEFAULT_SNAPSHOT_DIR),
                    help=f"输出目录 (默认: {DEFAULT_SNAPSHOT_DIR})")

    ip = sub.add_parser("import", help="从快照文件恢复 collection")
    ip.add_argument("--dir", default=str(DEFAULT_SNAPSHOT_DIR),
                    help=f"快照目录 (默认: {DEFAULT_SNAPSHOT_DIR})")

    sub.add_parser("list", help="列出所有 collection 及活跃状态")

    args = parser.parse_args()

    if args.cmd == "export":
        await export_snapshots(args.dir)
    elif args.cmd == "import":
        await import_snapshots(args.dir)
    elif args.cmd == "list":
        await list_collections_and_snapshots()
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
