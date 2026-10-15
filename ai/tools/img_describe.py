# -*- coding: utf-8 -*-
"""知识库图片批量 VLM 打标：为 media/ 图片生成一句话描述并写回 md 的 alt。

解决的问题：md 里图片引用的 alt 多为空（![](media/image3.png)）或零语义
（image96.jpeg）——LLM 检索到图也不知道图里是什么，引用靠盲选、给用户看的
alt 是乱码文本。打标后 alt 带语义描述，LLM 选图按描述匹配问题、用户看到的
图片引用可读。

机制：
- 扫描 kb/（或 --domain 指定域）全部 md 的图片引用 ![..](media/xxx)
- alt 无语义（空/纯 imageN 文件名形态）→ 读图片 → VLM 生成一句话描述 → 写回 alt
- 幂等缓存 kb/.img_desc_cache.json：{图片kb相对路径: 描述}——重复跑只处理
  新图/变更图（按文件 sha1 变化检测），已标注图直接复用缓存
- 域上下文注入：VLM prompt 带图片所在域/文件主题，提升描述相关性
- 并发 6 路；失败图记录跳过（宁缺勿错，可重跑补标）

用法：
  python -m ai.tools.img_describe --domain xqe      # 单域（如 xqe 试点域）
  python -m ai.tools.img_describe                    # 全库
  python -m ai.tools.img_describe --domain company --dry-run
  python -m ai.tools.img_describe --domain company --limit 20   # 先试 20 张
"""
import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ai.core.logging import get_logger

logger = get_logger("IMG_DESC")

CACHE_NAME = ".img_desc_cache.json"
IMG_RE = re.compile(r"!\[([^\]]*)\]\((media/[^)]+)\)")
SEM_LIMIT = 6
_MIN_ALT_LEN = 6          # alt 长于此视为已有语义描述
_MAX_SIDE = 1024          # 送 VLM 前的最大边长（大图压缩省 token）


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _alt_is_semantic(alt: str) -> bool:
    """alt 是否已有语义描述：非空、长度达阈值、且不是 imageN/mediaN 文件名形态。"""
    a = (alt or "").strip()
    if len(a) < _MIN_ALT_LEN:
        return False
    if re.fullmatch(r"(?:image|media|img|figure|fig)[-_]?\d*", a, re.I):
        return False
    stem = Path(a).stem
    if stem == a and re.fullmatch(r"[A-Za-z0-9_\-\.]+", a):
        return False  # alt 就是文件名
    return True


def _load_cache(kb_root: Path) -> dict:
    f = kb_root / CACHE_NAME
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(kb_root: Path, cache: dict) -> None:
    (kb_root / CACHE_NAME).write_text(
        json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def scan_md_files(kb_root: Path, domain: str):
    """收集 (md 文件, 匹配对象) 列表——域过滤：单域只扫该域目录。"""
    root = kb_root / domain if domain else kb_root
    for md in sorted(root.rglob("*.md")):
        if md.name.startswith("."):
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for m in IMG_RE.finditer(text):
            yield md, text, m


def image_bytes(img_abs: Path) -> bytes:
    """读图片并压缩到最大边 _MAX_SIDE（省 base64 体积，描述不需要原图精度）。"""
    from PIL import Image
    img = Image.open(img_abs)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > _MAX_SIDE:
        scale = _MAX_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


def build_prompt(domain: str, md_topic: str) -> str:
    return (
        "这是移动机器人（AGV/AMR）行业知识库文档里的一张配图。"
        f"图片所在文档主题：{md_topic}（所属知识域：{domain}）。\n"
        "请写**一句中文描述**（15~40 字），说明这张图展示的内容，"
        "要求：面向知识库检索（读者只看描述就知道图里是什么）；"
        "只描述画面真实可见的内容（设备/界面/示意图/表格/曲线/照片），"
        "画面文字可摘录关键词；不推测、不编造。只输出这一句描述，不要任何前缀。"
    )


async def describe_one(llm, img_bytes: bytes, domain: str, md_topic: str) -> str:
    b64 = base64.b64encode(img_bytes).decode()
    data_uri = f"data:image/jpeg;base64,{b64}"
    parts = []
    async for tok in llm.stream_vision(
            build_prompt(domain, md_topic), [data_uri],
            system_prompt="你是图片标注员，只输出一句中文描述。",
            max_tokens=120, temperature=0.1):
        parts.append(tok)
    return ("".join(parts)).strip().strip('"「」')


async def main(domain: str, dry_run: bool, limit: int, concurrency: int):
    from ai.config import _KB_DIR, get_ai_config
    from ai.core import get_llm_client
    from PIL import Image  # noqa: F401 前置校验 Pillow 可用

    kb_root = _KB_DIR
    cfg = get_ai_config()
    if not (cfg.vision_base_url and cfg.vision_api_key):
        sys.exit("VISION_BASE_URL / VISION_API_KEY 未配置，无法打标")

    cache = _load_cache(kb_root)
    md_tasks = []   # (md 文件, alt 位置, 图片 kb 相对路径, 现有 alt)
    img_seen = {}   # 图片 kb 相对路径 → sha1（变化检测）

    print(f"扫描 kb/{domain or '*'} ...")
    for md, text, m in scan_md_files(kb_root, domain):
        alt, rel_img = m.group(1), m.group(2)
        img_abs = (md.parent / rel_img).resolve()
        try:
            img_rel = str(img_abs.relative_to(kb_root)).replace("\\", "/")
        except ValueError:
            continue
        if not img_abs.is_file():
            continue
        data = img_abs.read_bytes()
        sha = _sha1(data)
        img_seen[img_rel] = sha
        if _alt_is_semantic(alt):
            continue
        cached = cache.get(img_rel)
        if cached and cached.get("sha") == sha:
            md_tasks.append((md, m, img_rel, cached["desc"], True))
        else:
            md_tasks.append((md, m, img_rel, None, False))
        img_seen[img_rel] = sha

    need_desc = [(md, m, img_rel) for md, m, img_rel, desc, from_cache
                 in md_tasks if not from_cache]
    need_desc = list({(str(md), rel) for md, _, rel in need_desc})
    print(f"图片引用 {len(md_tasks)} 处；待 VLM 打标图片 {len(need_desc)} 张；"
          f"缓存命中 {len(md_tasks) - len(need_desc)} 处")
    if dry_run:
        for _, rel in need_desc[: (limit or 20)]:
            print("  [DRY]", rel)
        return

    # VLM 打标（并发）
    llm = await get_llm_client()
    sem = asyncio.Semaphore(concurrency)
    fail = []

    async def _tag_one(img_rel: str):
        async with sem:
            img_abs = kb_root / img_rel
            try:
                data = image_bytes(img_abs)
                md_topic = Path(img_rel).stem
                desc = await describe_one(llm, data, domain or "knowledge", md_topic)
                if not desc:
                    raise ValueError("空描述")
                cache[img_rel] = {"sha": _sha1((kb_root / img_rel).read_bytes()),
                                  "desc": desc}
                return img_rel, desc
            except Exception as e:
                fail.append((img_rel, str(e)[:80]))
                return img_rel, None

    rels = list(dict.fromkeys(rel for _, rel in need_desc))
    if limit:
        rels = rels[:limit]
    print(f"开始 VLM 打标 {len(rels)} 张（并发 {concurrency}）...")
    t0 = time.time()
    results = await asyncio.gather(*[_tag_one(r) for r in rels])
    print(f"VLM 打标完成 {sum(1 for _, d in results if d)}/{len(rels)}"
          f"（{time.time() - t0:.0f}s），失败 {len(fail)}")

    # 写回 alt：按 md 文件分组，逐行替换（同文件多图安全，避免偏移失效）
    by_file = {}
    for md, m, img_rel, _desc, _fc in md_tasks:
        d = cache.get(img_rel, {}).get("desc")
        if d:
            by_file.setdefault(md, []).append((m, d))
    total_w = 0
    for md, pairs in by_file.items():
        lines = md.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        for m, desc in pairs:
            old = m.group(0)
            new = f"![{desc}]({m.group(2)})"
            for i, line in enumerate(lines):
                if old in line:
                    lines[i] = line.replace(old, new, 1)
                    total_w += 1
                    break
        md.write_text("".join(lines), encoding="utf-8", newline="")

    _save_cache(kb_root, cache)
    print(f"alt 写回 {total_w} 处；缓存已保存 → {kb_root / CACHE_NAME}")
    if fail:
        print("失败清单（可重跑补标）:")
        for rel, err in fail[:10]:
            print(f"  {rel}: {err}")


def _rewrite_md_lines(md: Path, pairs) -> int:
    """逐行写回 alt（按 (match对象, 描述) 列表）——按行匹配替换，避免偏移失效。"""
    lines = md.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    n = 0
    for m, desc in pairs:
        for i, line in enumerate(lines):
            if m.group(0) in line:
                lines[i] = line.replace(
                    f"![{m.group(1)}]({m.group(2)})", f"![{desc}]({m.group(2)})", 1)
                n += 1
                break
    md.write_text("".join(lines), encoding="utf-8", newline="")
    return n


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知识库图片批量 VLM 打标")
    parser.add_argument("--domain", default="", help="仅处理指定域目录（如 xqe/company/team）")
    parser.add_argument("--dry-run", action="store_true", help="只列出待打标图片")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 张（0=全部）")
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()
    asyncio.run(main(args.domain, args.dry_run, args.limit, args.concurrency))
