"""
一键入库：自动发现并运行所有已注册的知识库入库脚本

使用方法：
    python -m ai.ingestion.ingest_all                     # 全部入库
    python -m ai.ingestion.ingest_all --dry-run            # 预览所有源
    python -m ai.ingestion.ingest_all --skip cheduan       # 跳过指定类型
    python -m ai.ingestion.ingest_all --only faq           # 仅入库指定类型
    python -m ai.ingestion.ingest_all --list               # 列出已注册的 parser

collection_type 可选值：
    operation, faq, troubleshooting, cheduan, translation
"""
import sys
import asyncio
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")


async def ingest_all(
    skip_types: set = None,
    only_type: str = "",
    dry_run: bool = False,
) -> dict:
    """
    运行所有已注册的 Ingester。

    Args:
        skip_types: 跳过的 collection_type 集合
        only_type: 仅运行此类型（空 = 全部运行）
        dry_run: 预览模式，不写入 Qdrant

    Returns:
        {"success": N, "failed": [...], "skipped": [...]}
    """
    skip_types = skip_types or set()

    # 导入所有 parser 模块（触发 register_all()）
    _import_all_parsers()

    from ai.ingestion.registry import list_registered

    registered = list_registered()
    if not registered:
        print("[ERR] 未找到任何已注册的 Ingester")
        return {"success": 0, "failed": ["no_parsers_found"], "skipped": []}

    succeeded = []
    failed = []
    skipped = []

    # 关键排序：rebuild=True 的先执行（创建新集合），rebuild=False 的后执行（追加到新集合）
    registered.sort(key=lambda m: (m.collection_type, not m.ingester_cls.rebuild))

    # 本地文件模式：整个 ingest_all 过程共享一个 QdrantClient，避免文件锁冲突
    shared_client = None
    try:
        from ai.config import get_ai_config
        from ai.ingestion.base import BaseIngester
        config = get_ai_config()
        if config.qdrant_local_path:
            shared_client = BaseIngester._make_qdrant_client(config)
            resolved = Path(config.qdrant_local_path)
            if not resolved.is_absolute():
                from ai.ingestion.base import _project_root as base_root
                resolved = base_root / resolved
            print(f"[INFO] 本地文件模式: {resolved}")
            print(f"[INFO] 目录存在: {resolved.is_dir()}, collections: {[c.name for c in shared_client.get_collections().collections]}")

        for meta in registered:
            ct = meta.collection_type

            if only_type and ct != only_type:
                skipped.append(meta.name)
                continue
            if ct in skip_types:
                skipped.append(meta.name)
                continue

            print(f"\n{'=' * 60}")
            print(f"[INGEST] {meta.description or meta.name}")
            print(f"   类型: {ct}, 源文件: {meta.source_patterns}")
            print(f"{'=' * 60}")

            try:
                ingester = meta.ingester_cls()

                if dry_run:
                    ingester.run_dry_run()
                    succeeded.append(meta.name)
                else:
                    ok = await ingester.auto_ingest(client=shared_client)
                    if ok:
                        print(f"[OK] {meta.name} 入库完成")
                        succeeded.append(meta.name)
                    else:
                        print(f"[FAIL] {meta.name} 入库失败")
                        failed.append(meta.name)
            except Exception as e:
                print(f"[ERR] {meta.name} 入库异常: {e}")
                import traceback
                traceback.print_exc()
                failed.append(meta.name)
    finally:
        if shared_client is not None:
            try:
                shared_client.close()
            except Exception:
                pass

    print(f"\n{'=' * 60}")
    print(f"[DONE] 成功: {len(succeeded)}/{len(registered)}")
    if failed:
        print(f"[FAIL] 失败: {', '.join(failed)}")
    if skipped:
        print(f"[SKIP] 跳过: {', '.join(skipped)}")
    print(f"{'=' * 60}")

    return {"success": succeeded, "failed": failed, "skipped": skipped}


def _import_all_parsers():
    """触发所有 parser 模块的 register_all()"""
    # 自动发现模式：扫描 parsers/ 目录
    from ai.ingestion.registry import discover_parsers
    discovered = discover_parsers()

    if not discovered:
        # Fallback: 显式导入已知模块
        from ai.ingestion.registry import register_builtin_parsers
        register_builtin_parsers()


async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="一键入库：自动发现并运行所有知识库 parser",
    )
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="预览模式，不写入 Qdrant")
    parser.add_argument("--skip", action="append", default=[],
                        choices=["operation", "faq", "troubleshooting", "cheduan", "translation"],
                        help="跳过指定类型的知识库")
    parser.add_argument("--only", default="",
                        choices=["", "operation", "faq", "troubleshooting", "cheduan", "translation"],
                        help="仅入库指定类型")
    parser.add_argument("--list", action="store_true",
                        help="列出所有已注册的 parser")
    args = parser.parse_args()

    _import_all_parsers()

    if args.list:
        from ai.ingestion.registry import list_registered
        registered = list_registered()
        if not registered:
            print("（没有已注册的 parser — 请检查 ai/ingestion/parsers/ 目录）")
        else:
            for meta in registered:
                srcs = ", ".join(meta.source_patterns)
                print(f"  [{meta.collection_type or '?'}] {meta.name}")
                print(f"       源文件: {srcs}")
                if meta.description:
                    print(f"       描述: {meta.description}")
        return

    await ingest_all(
        skip_types=set(args.skip),
        only_type=args.only,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(main())
