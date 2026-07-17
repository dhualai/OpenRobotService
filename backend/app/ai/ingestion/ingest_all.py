"""
一键入库：依次运行三个知识库的入库脚本

使用方法：
    python -m app.ai.ingestion.ingest_all              # 全部入库
    python -m app.ai.ingestion.ingest_all --dry-run    # 预览（仅排查树支持）
    python -m app.ai.ingestion.ingest_all --skip manual # 跳过操作手册
    python -m app.ai.ingestion.ingest_all --skip faq    # 跳过 FAQ
    python -m app.ai.ingestion.ingest_all --skip troubleshooting  # 跳过排查树
"""
import sys
import asyncio
from pathlib import Path

_backend_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from dotenv import load_dotenv
load_dotenv(_backend_dir / ".env")

# 注册的入库任务：(模块名, 显示名)
_REGISTRY = [
    ("ingest_operation_manual", "操作手册"),
    ("ingest_faq",             "FAQ"),
    ("ingest_troubleshooting", "问题排查树"),
    ("ingest_cheduan",         "车端错误码"),
    ("ingest_translation",     "翻译表"),
]


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="一键入库：操作手册 + FAQ + 问题排查树 + 车端错误码 + 翻译表")
    parser.add_argument("--dry-run", "-n", action="store_true", help="预览模式（目前仅排查树支持）")
    parser.add_argument("--skip", action="append", default=[],
                        choices=["manual", "faq", "troubleshooting", "cheduan", "translation"],
                        help="跳过指定知识库")
    args = parser.parse_args()

    skip_map = {
        "manual":          "ingest_operation_manual",
        "faq":             "ingest_faq",
        "troubleshooting": "ingest_troubleshooting",
        "cheduan":         "ingest_cheduan",
        "translation":     "ingest_translation",
    }
    skip_modules = {skip_map[s] for s in args.skip}

    success = 0
    failed = []

    for module_name, display_name in _REGISTRY:
        if module_name in skip_modules:
            print(f"\n[SKIP] {display_name}")
            continue

        print(f"\n{'=' * 60}")
        print(f"[INGEST] 开始入库: {display_name}")
        print(f"{'=' * 60}")

        try:
            mod = __import__(
                f"app.ai.ingestion.{module_name}",
                fromlist=["auto_ingest"],
            )
            if args.dry_run and hasattr(mod, "load_troubleshooting_json"):
                # 排查树支持 dry-run
                chunks = mod.load_troubleshooting_json()
                mod.print_summary(chunks)
                print(f"\n[Dry-run] 排查树线性化预览 (前 3 个):\n")
                for c in chunks[:3]:
                    print(f"━━━ {c.symptom_id}: {c.symptom_name} [{c.category}] ━━━")
                    print(c.linearized_tree[:300])
                    print()
                success += 1
            else:
                ok = await mod.auto_ingest()
                if ok:
                    print(f"[OK] {display_name} 入库完成")
                    success += 1
                else:
                    print(f"[FAIL] {display_name} 入库失败")
                    failed.append(display_name)
        except Exception as e:
            print(f"[ERR] {display_name} 入库异常: {e}")
            failed.append(display_name)

    print(f"\n{'=' * 60}")
    print(f"[DONE] 成功: {success}/{len(_REGISTRY)}")
    if failed:
        print(f"[FAIL] 失败: {', '.join(failed)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
