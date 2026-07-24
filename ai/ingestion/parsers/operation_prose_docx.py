"""
车端实施文档 .docx → cheduan_manual 集合

来源：cheduan_doc/车端实施文档.docx（40MB，384 段落 + 嵌入图片）
流程：提取图片 → pandoc 转 markdown → 按标题层级切块 → 向量化入库
"""
import re
import zipfile
import subprocess
from pathlib import Path
from typing import List
from dataclasses import dataclass, field

from ai.config import get_docs_dir
from ai.ingestion.base import BaseIngester, Chunk
from ai.ingestion.registry import register


# ── 数据模型 ────────────────────────────────────────────────────

@dataclass
class ProseSection:
    """文档中的一个章节"""
    title: str              # 标题文本（如 "3.2 传感器校准流程"）
    level: int              # 标题层级 1-4
    parent_title: str       # 上级标题
    content: str            # 正文（markdown，不含标题行）
    images: List[str] = field(default_factory=list)  # 图片文件名
    order: int = 0


# ── Ingester ─────────────────────────────────────────────────────

class OperationProseDocxIngester(BaseIngester[ProseSection]):
    """车端实施手册 docx（章节体+图片）→ cheduan_manual 集合"""

    source_paths = [get_docs_dir() / "cheduan_doc" / "车端实施文档.docx"]
    collection_prefix = "cheduan_manual"
    collection_type = "operation"
    rebuild = True

    @staticmethod
    def _pointer_reader() -> str:
        from ai.config import get_active_cheduan_manual_collection
        return get_active_cheduan_manual_collection()

    @staticmethod
    def _pointer_writer(name: str) -> None:
        from ai.config import _write_active_cheduan_manual_collection
        _write_active_cheduan_manual_collection(name)

    pointer_reader = staticmethod(_pointer_reader)
    pointer_writer = staticmethod(_pointer_writer)

    def get_source_label(self) -> str:
        return "车端实施文档"

    def parse(self) -> List[ProseSection]:
        docx_path = self.source_paths[0]
        # 1. 提取图片到 cheduan_doc/media/
        _extract_media(docx_path)

        # 2. pandoc 转 markdown（自动生成 ![]() 引用）
        md_text = _convert_via_pandoc(docx_path)

        # 3. 按标题切块
        return _parse_prose_markdown(md_text)

    def to_chunk(self, s: ProseSection) -> Chunk:
        title_path = f"{s.parent_title} > {s.title}" if s.parent_title else s.title

        # 替换正文中的图片引用，去掉 media/ 前缀并补全 URL
        from ai.config import get_ai_config
        media_prefix = get_ai_config().media_url_prefix
        content = s.content
        image_urls: list = []
        if s.images:
            for img in s.images:
                # img 格式: "media/image35.png" → 纯文件名
                fname = img.split("/")[-1] if "/" in img else img
                full_url = f"{media_prefix}/cheduan_doc/{fname}"
                image_urls.append(full_url)
                # 替换正文中 pandoc 生成的相对路径引用
                content = content.replace(f"]({img})", f"]({full_url})")
                content = content.replace(f"]({fname})", f"]({full_url})")

        text = f"【车端实施手册】{title_path}\n{content}"

        payload: dict = {
            "title": s.title,
            "level": s.level,
            "parent_title": s.parent_title,
            "section_order": s.order,
            "content": content,
            "images": s.images,
            "image_urls": image_urls,
            "source": "车端实施文档.docx",
        }

        # 图片 URL 显式列出（帮助 LLM 发现）
        if image_urls:
            img_refs = " ".join(f"![]({url})" for url in image_urls)
            text += f"\n{img_refs}"

        return Chunk(
            id=self.stable_id("cheduan_manual", str(s.order), s.title[:60]),
            text=text,
            payload=payload,
        )


# ── 图片提取 ────────────────────────────────────────────────────

def _extract_media(docx_path: Path) -> Path:
    """
    从 docx zip 中提取所有内嵌图片到 {doc_dir}/media/。
    返回 media 目录路径。
    """
    doc_dir = docx_path.parent
    media_dir = doc_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    extracted = 0
    with zipfile.ZipFile(str(docx_path)) as z:
        for name in z.namelist():
            if not name.startswith("word/media/") or name.endswith("/"):
                continue
            fname = name.split("/")[-1]
            dest = media_dir / fname
            if not dest.exists():
                dest.write_bytes(z.read(name))
                extracted += 1

    if extracted:
        print(f"  [MEDIA] 提取 {extracted} 张图片 -> {media_dir}")
    return media_dir


# ── pandoc 转换 ─────────────────────────────────────────────────

def _convert_via_pandoc(docx_path: Path) -> str:
    """
    pandoc 将 docx 转为 markdown。
    自动处理图片引用（![]() 指向 media/ 目录）。
    """
    # pandoc 需要从 doc 所在目录执行，这样 media/ 的相对路径才正确
    result = subprocess.run(
        ["pandoc", str(docx_path.name), "-f", "docx", "-t", "markdown", "--wrap=none"],
        capture_output=True, encoding="utf-8", check=True,
        cwd=str(docx_path.parent),  # cd 到 doc 目录
    )
    return result.stdout


# ── Markdown 切块 ───────────────────────────────────────────────

def _parse_prose_markdown(md_text: str) -> List[ProseSection]:
    """
    按标题（# ~ ####）切分 markdown 为 section。
    过小的 section 自动合并到上一个。
    """
    heading_pattern = re.compile(r'^(#{1,4})\s+(.+?)[ \t\r]*$', re.MULTILINE)
    matches = list(heading_pattern.finditer(md_text))

    if not matches:
        # 无标题：整个文档作为一个 section
        return [_make_section("车端实施文档", 1, "", md_text, 0)]

    sections: List[ProseSection] = []
    parent_stack: List[tuple] = []  # [(title, level)]

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip().rstrip("*").strip()

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        content = md_text[start:end]

        # 去掉标题行本身，保留正文
        body = "\n".join(content.split("\n")[1:]).strip()
        if not body:
            continue

        # 更新标题栈
        while parent_stack and parent_stack[-1][1] >= level:
            parent_stack.pop()
        parent_title = parent_stack[-1][0] if parent_stack else ""
        parent_stack.append((title, level))

        # 提取图片引用
        images = [
            m2.group(1)
            for m2 in re.finditer(r'!\[.*?\]\((?:\./)?(media/[^)]+)\)', body)
        ]

        sections.append(ProseSection(
            title=title,
            level=level,
            parent_title=parent_title,
            content=body,
            images=images,
            order=len(sections),
        ))

    return _merge_small_sections(sections, min_chars=50)


def _make_section(title: str, level: int, parent: str, content: str, order: int) -> ProseSection:
    images = [
        m.group(1)
        for m in re.finditer(r'!\[.*?\]\((?:\./)?(media/[^)]+)\)', content)
    ]
    return ProseSection(
        title=title, level=level, parent_title=parent,
        content=content, images=images, order=order,
    )


def _merge_small_sections(sections: List[ProseSection], min_chars: int = 50) -> List[ProseSection]:
    """合并过小的 section 到上一个"""
    if len(sections) <= 1:
        return sections

    merged: List[ProseSection] = []
    for s in sections:
        if len(s.content) < min_chars and merged:
            prev = merged[-1]
            prev.content += f"\n\n### {s.title}\n{s.content}"
            prev.images.extend(s.images)
        else:
            merged.append(s)
    return merged


def register_all():
    register(OperationProseDocxIngester, description="车端实施手册 docx（章节体+图片）→ cheduan_manual 集合")


register_all()
