"""
一键入库：按五层 domain 架构批量入库 kb/ 下的所有 markdown 知识库

使用方法：
    python -m ai.ingestion.ingest_all                     # 全部 5 个 domain 入库
    python -m ai.ingestion.ingest_all --dry-run            # 预览所有源
    python -m ai.ingestion.ingest_all --domain team        # 仅入库 team
    python -m ai.ingestion.ingest_all --skip industry      # 跳过 industry
    python -m ai.ingestion.ingest_all --list               # 列出各 domain 文件统计

domain:
    industry, company, team, project, personal
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
    skip_domains: set = None,
    only_domain: str = "",
    dry_run: bool = False,
) -> dict:
    """
    运行所有 domain 的 KBDomainIngester。

    Args:
        skip_domains: 跳过的 domain 集合
        only_domain: 仅运行此 domain（空 = 全部运行）
        dry_run: 预览模式，不写入 Qdrant

    Returns:
        {"success": N, "failed": [...], "skipped": [...]}
    """
    skip_domains = skip_domains or set()

    from ai.config import KB_DOMAINS
    from ai.ingestion.parsers.kb_markdown import KBDomainIngester
    from ai.config import get_ai_config
    from ai.ingestion.base import BaseIngester

    # markdown 入库只覆盖五个文档 domain；dispatch 是派单模块自己的
    # 历史工单向量库（L3-A 路独立集合），不走 markdown 入库——KB_DOMAINS
    # 里带它是为了运行时指针管理，直接全量迭代会 ValueError: Unknown domain。
    _INGEST_DOMAINS = [d for d in KB_DOMAINS if d != "dispatch"]

    # 确定要运行的 domain 列表
    if only_domain:
        if only_domain not in _INGEST_DOMAINS:
            print(f"[ERR] 未知 domain: {only_domain}. 可选: {', '.join(_INGEST_DOMAINS)}")
            return {"success": 0, "failed": [f"unknown_domain:{only_domain}"], "skipped": []}
        domains = [only_domain]
    else:
        domains = [d for d in _INGEST_DOMAINS if d not in skip_domains]

    print(f"[INFO] Domain 列表: {', '.join(domains)}")
    print()

    succeeded = []
    failed = []
    skipped = []

    # 本地文件模式：整个 ingest_all 过程共享一个 QdrantClient
    shared_client = None
    try:
        config = get_ai_config()
        if config.qdrant_local_path and not dry_run:
            shared_client = BaseIngester._make_qdrant_client(config)
            resolved = Path(config.qdrant_local_path)
            if not resolved.is_absolute():
                from ai.ingestion.base import _project_root as base_root
                resolved = base_root / resolved
            print(f"[INFO] 本地文件模式: {resolved}")
            if resolved.is_dir():
                cols = [c.name for c in shared_client.get_collections().collections]
                print(f"[INFO] 已有 collections: {cols}")

        for domain in domains:
            ingester = KBDomainIngester(domain=domain)

            if not ingester.source_paths:
                print(f"\n{'=' * 60}")
                print(f"[SKIP] kb/{domain}/ — 无 .md 文件")
                print(f"{'=' * 60}")
                skipped.append(domain)
                continue

            print(f"\n{'=' * 60}")
            print(f"[INGEST] kb/{domain}/ — {len(ingester.source_paths)} 个 .md 文件")
            print(f"{'=' * 60}")

            if dry_run:
                ingester.run_dry_run()
                succeeded.append(domain)
            else:
                try:
                    ok = await ingester.auto_ingest(client=shared_client)
                    if ok:
                        print(f"[OK] kb/{domain}/ 入库完成")
                        succeeded.append(domain)
                    else:
                        print(f"[FAIL] kb/{domain}/ 入库失败")
                        failed.append(domain)
                except Exception as e:
                    print(f"[ERR] kb/{domain}/ 入库异常: {e}")
                    import traceback
                    traceback.print_exc()
                    failed.append(domain)
    finally:
        if shared_client is not None:
            try:
                shared_client.close()
            except Exception:
                pass

    print(f"\n{'=' * 60}")
    print(f"[DONE] 成功: {len(succeeded)}/{len(domains)}")
    if succeeded:
        print(f"       已入库: {', '.join(succeeded)}")
    if failed:
        print(f"[FAIL] 失败: {', '.join(failed)}")
    if skipped:
        print(f"[SKIP] 跳过: {', '.join(skipped)}")
    print(f"{'=' * 60}")

    return {"success": succeeded, "failed": failed, "skipped": skipped}


def list_domains():
    """列出各 domain 的文件统计"""
    from ai.config import KB_DOMAINS
    from ai.ingestion.parsers.kb_markdown import KBDomainIngester

    print("五层 Domain 知识库文件统计:\n")
    total_files = 0
    for domain in [d for d in KB_DOMAINS if d != "dispatch"]:
        ingester = KBDomainIngester(domain=domain)
        n = len(ingester.source_paths)
        total_files += n
        status = "[OK]" if n > 0 else "(empty)"
        print(f"  [{domain:10s}] {n:4d} files  {status}")
        if n > 0 and n <= 10:
            for f in ingester.source_paths:
                rel = f.relative_to(ingester._domain_dir)
                print(f"              ├─ {rel}")
    print(f"\n  合计: {total_files} 个 .md 文件")


async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="一键入库：按五层 domain 架构批量入库 kb/ 下所有 markdown 知识库",
    )
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="预览模式，不写入 Qdrant")
    parser.add_argument("--domain", default="",
                        choices=["", "industry", "company", "team", "project", "personal"],
                        help="仅入库指定 domain")
    parser.add_argument("--skip", action="append", default=[],
                        choices=["industry", "company", "team", "project", "personal"],
                        help="跳过指定 domain")
    parser.add_argument("--list", action="store_true",
                        help="列出各 domain 文件统计")
    args = parser.parse_args()

    if args.list:
        list_domains()
        return

    await ingest_all(
        skip_domains=set(args.skip),
        only_domain=args.domain,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(main())
