#!/usr/bin/env python3
"""AI 模块端到端功能测试：检索 → 图片 URL → 文件可达

用法:
    cd D:\Code\OpenRobotService
    python ai/tools/test_image_pipeline.py
"""
import asyncio
import re
import sys
import os
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

from ai.config import get_ai_config, _KB_DIR, get_active_collection_for
from ai.core.retrieval import get_retrieval_service

PASS = 0
FAIL = 0

def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  -- {detail}")


async def main():
    global PASS, FAIL
    config = get_ai_config()
    media_prefix = config.media_url_prefix

    print("=" * 60)
    print("AI 模块端到端功能测试")
    print("=" * 60)

    # ---- 1. 配置检查 ----
    print("\n1. 配置")
    check("DeepSeek API Key", bool(config.deepseek_api_key), config.deepseek_api_key[:8] + "..." if config.deepseek_api_key else "MISSING")
    check("Qdrant 可用 (local)", bool(config.qdrant_local_path), config.qdrant_local_path)
    check("media_url_prefix", bool(media_prefix), media_prefix)
    check("_KB_DIR 存在", _KB_DIR.is_dir(), str(_KB_DIR))

    # ---- 2. 活跃集合检查 ----
    print("\n2. Qdrant 活跃集合")
    for domain in ["team", "company", "industry"]:
        active = get_active_collection_for(domain)
        check(f"  {domain} domain", bool(active), active or "NONE")

    # ---- 3. 检索测试 ----
    print("\n3. 检索测试（查 FAQ）")
    tests = [
        ("网络不通", "网络相关 FAQ"),
        ("充电", "充电相关 FAQ"),
        ("库位配置", "库位配置相关 FAQ"),
    ]

    service = await get_retrieval_service()
    all_images_ok = True

    for query, desc in tests:
        try:
            results = await service.retrieve_domain(query, domain="team", sub_domain="faq", top_k=2)
            if results:
                r = results[0]
                imgs = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', r.content or "")
                local_imgs = [i for i in imgs if i.startswith("./media/")]
                external_imgs = [i for i in imgs if i.startswith(("http://", "https://"))]
                other = [i for i in imgs if i not in local_imgs and i not in external_imgs]

                check(
                    f"  [{desc}] 有结果",
                    len(r.content or "") > 50,
                    f"content_len={len(r.content or '')} images={len(imgs)}"
                )
                if imgs:
                    check(f"  [{desc}] 图片路径 ./media/", len(local_imgs) == len(imgs),
                          f"local={len(local_imgs)} external={len(external_imgs)} other={other[:3]}")

                    # 验证图片文件确实存在
                    for img_path in local_imgs[:2]:
                        # ./media/image1.png → image1.png
                        fname = img_path.replace("./media/", "")
                        # 拼出实际文件路径
                        file_path = _KB_DIR / "team" / "faq" / "media" / fname
                        exists = file_path.is_file()
                        if not exists:
                            all_images_ok = False
                        check(
                            f"    {fname} 文件存在",
                            exists,
                            str(file_path) if not exists else ""
                        )
                else:
                    check(f"  [{desc}] 无图片(正常)", True)
            else:
                check(f"  [{desc}] 有结果", False, "retrieve_domain returned empty")
        except Exception as e:
            check(f"  [{desc}] 检索成功", False, str(e)[:100])

    # ---- 4. KB 文件抽样 ----
    print("\n4. KB 文件抽样检查")
    md_files = sorted(_KB_DIR.rglob("*.md"))
    total_img = 0
    total_bad = 0
    bad_examples = []

    for md in md_files:
        text = md.read_text(encoding="utf-8")
        for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', text):
            total_img += 1
            path = m.group(2)
            if not (path.startswith("./media/") or path.startswith("http://") or path.startswith("https://")):
                total_bad += 1
                if len(bad_examples) < 5:
                    bad_examples.append(f"{md.relative_to(_KB_DIR)}: {path}")

    check(f"  图片总数: {total_img}", True)
    check(f"  ./media/ 格式", total_bad == 0, f"bad={total_bad} examples={bad_examples[:3]}")

    # ---- 5. 图片文件总数 ----
    print("\n5. 图片文件可用性")
    total_media_files = 0
    for media_dir in _KB_DIR.rglob("media"):
        if media_dir.is_dir():
            total_media_files += len(list(media_dir.iterdir()))
    check(f"  图片文件数: {total_media_files}", True)
    check(f"  图片可达(全部)", all_images_ok, "某些检索命中的图片文件不存在" if not all_images_ok else "")

    # ---- 结果 ----
    print("\n" + "=" * 60)
    if FAIL:
        print(f"FAILED: {PASS} passed, {FAIL} failed  ({PASS + FAIL} total)")
        sys.exit(1)
    else:
        print(f"ALL PASSED: {PASS} tests  ({PASS + FAIL} total)")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
