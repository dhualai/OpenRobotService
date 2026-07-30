#!/usr/bin/env python3
"""
KB Markdown 图片路径批量修复工具

扫描 kb/ 下所有 .md 文件，统一图片引用为 ./media/filename 格式。

修复项:
  1. 本地图片路径统一为 ./media/xxx
     - D:\...\media\media\image.png → ./media/image.png
     - media/image.png → ./media/image.png
     - ./media/image.png → ./media/image.png（不重复加 ./）
  2. 清理 pandoc {width="..." height="..."} 属性
  3. 清理 media/media/ 双嵌套路径
  4. 跳过 http/https 外链

用法:
    python tools/fix_kb_image_paths.py          # 扫描并修复
    python tools/fix_kb_image_paths.py --dry-run  # 仅报告，不写入
"""
import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def fix_md_content(text: str) -> Tuple[str, int]:
    """修复单个 md 文件的内容。返回 (fixed_text, change_count)。"""
    changes = 0
    original = text

    # --- 1. 统一图片路径为 ./media/filename ---
    def _fix_img_ref(m: re.Match) -> str:
        alt = m.group(1)
        raw_path = m.group(2)
        if raw_path.startswith(("http://", "https://")):
            return m.group(0)
        fname = re.split(r'[\\/]', raw_path)[-1]
        fname = re.sub(r'\{.*$', '', fname).strip()
        fname = fname.split("?")[0]
        if not fname:
            return m.group(0)
        return f"![{alt}](./media/{fname})"

    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _fix_img_ref, text)
    if text != original:
        changes += 1  # 有图片路径变化

    # --- 2. 二次清理：图片引用后的 {width=... height=...} ---
    text, n = re.subn(
        r'(\./media/[^)\s]+)\s*\{[^}]*\}', r'\1', text,
    )
    changes += n

    # --- 3. 清理残留在文本行内的 {width="..." height="..."} ---
    text, n = re.subn(r'\{width="[^"]*"\s*height="[^"]*"\}', "", text)
    changes += n

    # --- 4. 清理 media/media/ 双嵌套 ---
    text, n = re.subn(r'\./media/media/', "./media/", text)
    changes += n

    # --- 5. 清理 pandoc TOC 锚点 {#xxx} ---
    text, n = re.subn(r'\{#[^}]*\}', "", text)
    changes += n

    return text, changes


def find_md_files(kb_root: Path) -> List[Path]:
    """扫描 kb 目录下所有 .md 文件"""
    if not kb_root.is_dir():
        _safe_print(f"Error: kb root not found: {kb_root}")
        sys.exit(1)
    return sorted(kb_root.rglob("*.md"))


def _summarize_image_refs(text: str) -> str:
    """提取文本中所有图片引用路径的摘要"""
    refs = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)
    if not refs:
        return "(无图片)"
    # Group by pattern
    local = [r for r in refs if r.startswith("./media/")]
    absolute = [r for r in refs if re.match(r'^[A-Za-z]:[/\\]', r)]
    relative = [r for r in refs if r.startswith("media/") and not r.startswith("./media/")]
    external = [r for r in refs if r.startswith(("http://", "https://"))]
    parts = []
    if local:
        parts.append(f"{len(local)} 个 ./media/")
    if absolute:
        parts.append(f"{len(absolute)} 个绝对路径")
    if relative:
        parts.append(f"{len(relative)} 个 media/ (缺 ./)")
    if external:
        parts.append(f"{len(external)} 个外链")
    return ", ".join(parts) if parts else "(未知格式)"


def main():
    parser = argparse.ArgumentParser(
        description="KB Markdown 图片路径批量修复工具",
    )
    parser.add_argument(
        "--kb-root", type=str, default=None,
        help="kb 根目录（默认: ../OpenRobotService_Data/kb/）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅报告，不写入文件",
    )
    args = parser.parse_args()

    if args.kb_root:
        kb_root = Path(args.kb_root).resolve()
    else:
        kb_root = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "OpenRobotService_Data" / "kb"
        ).resolve()

    md_files = find_md_files(kb_root)
    _safe_print(f"扫描 kb 目录: {kb_root}")
    _safe_print(f"发现 {len(md_files)} 个 .md 文件\n")

    if args.dry_run:
        _safe_print("=== DRY RUN 模式（不写入文件）===\n")

    fixed_count = 0
    total_changes = 0
    problem_files: List[Tuple[Path, str]] = []

    for md_path in md_files:
        rel_path = md_path.relative_to(kb_root)
        original = md_path.read_text(encoding="utf-8")

        # 先看有什么格式的图片
        before_summary = _summarize_image_refs(original)

        fixed, changes = fix_md_content(original)

        if fixed != original:
            fixed_count += 1
            total_changes += changes
            after_summary = _summarize_image_refs(fixed)
            _safe_print(f"✏  {rel_path}  [{before_summary}] → [{after_summary}]")

            if not args.dry_run:
                md_path.write_text(fixed, encoding="utf-8")
        else:
            # 有图片但不需要修复的
            if "![" in original:
                _safe_print(f"✓  {rel_path}  [{before_summary}] — 已正确")

    # Summary
    _safe_print(f"\n{'=' * 50}")
    if args.dry_run:
        _safe_print(f"DRY RUN: {fixed_count} 个文件需要修复")
    else:
        _safe_print(f"已修复 {fixed_count} 个文件")
    _safe_print(f"共 {len(md_files)} 个 .md 文件")


if __name__ == "__main__":
    main()
