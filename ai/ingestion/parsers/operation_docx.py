"""
USP 实施与操作手册 .docx → operation_docs 集合

来源：operation_doc/USP 实施与操作手册.docx
流程：pandoc 转 md → 去封面/目录 → 按 ## 标题切块 → 向量化入 Qdrant
"""
import re
import subprocess
from pathlib import Path
from typing import List
from dataclasses import dataclass, field

from ai.config import get_docs_dir
from ai.ingestion.base import BaseIngester, Chunk
from ai.ingestion.registry import register


# ── 数据模型 ────────────────────────────────────────────────────

@dataclass
class ManualChunk:
    """操作手册的一个 ## 标题段落"""
    id: str
    title: str
    section: str
    chapter: str
    content: str           # 完整正文（含 ## 标题 + ###/#### 子节）
    images: List[str] = field(default_factory=list)


# 正文开始的标记
_CONTENT_START_MARKERS = [
    r'^# \*?\*?0 文档信息',
    r'^# \*?\*?1 USP',
    r'^# \*?\*?1 部署',
]

# 仅拆分这些 ## 节的 ### 子标题
_SPLIT_SECTIONS = {"2.1"}


class OperationDocxIngester(BaseIngester[ManualChunk]):
    """USP 操作手册 docx → operation_docs 集合"""

    source_paths = [get_docs_dir() / "operation_doc" / "USP 实施与操作手册.docx"]
    collection_prefix = "operation_docs"
    collection_type = "operation"
    rebuild = True

    @staticmethod
    def _pointer_reader() -> str:
        from ai.config import get_active_collection
        return get_active_collection()

    @staticmethod
    def _pointer_writer(name: str) -> None:
        from ai.config import _write_active_collection
        _write_active_collection(name)

    pointer_reader = staticmethod(_pointer_reader)
    pointer_writer = staticmethod(_pointer_writer)

    def get_source_label(self) -> str:
        return "USP实施与操作手册"

    def validate_source_files(self) -> bool:
        # 也检查备选路径
        from ai.config import get_docs_dir
        docs = get_docs_dir()
        alternatives = [
            docs / "operation_doc" / "USP 实施与操作手册.docx",
            docs / "operation_doc" / "USP实施与操作手册.md",
        ]
        if not any(p.exists() for p in alternatives):
            self._log(f"[WARN] 操作手册源文件不存在，尝试过: {alternatives}")
            return False
        return True

    def parse(self) -> List[ManualChunk]:
        filepath = _resolve_source(self.source_paths[0])

        if filepath.suffix.lower() == '.docx':
            try:
                text = _convert_docx(str(filepath))
            except FileNotFoundError:
                raise RuntimeError("pandoc 未安装，请先安装 pandoc: https://pandoc.org/installing.html")
        else:
            text = filepath.read_text(encoding='utf-8')

        text = _strip_frontmatter(text)
        return _parse_manual(text)

    def to_chunk(self, c: ManualChunk) -> Chunk:
        return Chunk(
            id=c.id,
            text=f"{c.title}\n{c.content}",
            payload={
                "title": c.title,
                "section": c.section,
                "chapter": c.chapter,
                "content": c.content,
                "images": c.images,
                "source": "USP实施与操作手册",
            },
        )


# ── 解析逻辑 ────────────────────────────────────────────────────

def _resolve_source(preferred: Path) -> Path:
    """解析源文件路径，支持自动查找备选路径"""
    import os
    if os.path.isfile(str(preferred)):
        return preferred

    from ai.config import get_docs_dir
    docs = get_docs_dir()
    alternatives = [
        docs / "operation_doc" / "USP 实施与操作手册.docx",
        docs / "operation_doc" / "USP实施与操作手册.md",
    ]
    for alt in alternatives:
        if alt.is_file():
            return alt
    raise FileNotFoundError(f"未找到操作手册源文件: {preferred}")


def _convert_docx(filepath: str) -> str:
    result = subprocess.run(
        ["pandoc", filepath, "-f", "docx", "-t", "markdown", "--wrap=none"],
        capture_output=True, encoding="utf-8", check=True,
    )
    return result.stdout


def _strip_frontmatter(text: str) -> str:
    for marker in _CONTENT_START_MARKERS:
        m = re.search(marker, text, re.MULTILINE)
        if m:
            return text[m.start():]
    return text


def _parse_manual(text: str, source_label: str = "usp-manual") -> List[ManualChunk]:
    heading_pattern = re.compile(r'^##[ \t]+(.+?)[ \t\r]*$', re.MULTILINE)
    matches = list(heading_pattern.finditer(text))

    if not matches:
        return []

    chunks: List[ManualChunk] = []
    for i, m in enumerate(matches):
        raw_title = m.group(1).strip()
        title = raw_title.strip('*').strip()
        if title.startswith('##'):
            title = title.lstrip('#').strip()
        if not title:
            continue

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_content = text[start:end].strip()

        body_lines = raw_content.split('\n')[1:]
        body = '\n'.join(body_lines).strip()
        if not body:
            continue

        sec_match = re.match(r'([\d.]+|[^\s]+)\s', title)
        section_num = sec_match.group(1) if sec_match else title[:20]
        chapter_num = section_num.split('.')[0] if '.' in section_num else section_num

        images = [
            img_m.group(1)
            for img_m in re.finditer(r'!\[.*?\]\((?:\./)?(media/[^)]+)\)', raw_content)
        ]

        import hashlib
        chunk_id = hashlib.md5((f"{source_label}:" + title).encode()).hexdigest()

        chunks.append(ManualChunk(
            id=chunk_id,
            title=title,
            section=section_num,
            chapter=chapter_num,
            content=raw_content,
            images=images,
        ))

    return _split_by_h3(chunks, source_label)


def _split_by_h3(chunks: List[ManualChunk], source_label: str) -> List[ManualChunk]:
    """拆分 §2.1 的 ### 子节（自研车/睿芯行/科钛车差异大）"""
    h3_pattern = re.compile(r'^###[ \t]+(.+?)[ \t\r]*$', re.MULTILINE)
    result: List[ManualChunk] = []

    for c in chunks:
        if c.section not in _SPLIT_SECTIONS:
            result.append(c)
            continue

        h3_matches = list(h3_pattern.finditer(c.content))
        if not h3_matches:
            result.append(c)
            continue

        parent_title_line = c.content.split('\n')[0].strip()
        parent_title = parent_title_line.lstrip('#').strip().strip('*').strip()

        for i, m in enumerate(h3_matches):
            raw_h3 = m.group(1).strip()
            h3_title = raw_h3.strip('*').strip()
            if not h3_title:
                continue

            start = m.start()
            end = h3_matches[i + 1].start() if i + 1 < len(h3_matches) else len(c.content)
            h3_content = c.content[start:end].strip()
            body = '\n'.join(h3_content.split('\n')[1:]).strip()
            if not body:
                continue

            images = [
                img_m.group(1)
                for img_m in re.finditer(r'!\[.*?\]\((?:\./)?(media/[^)]+)\)', h3_content)
            ]

            full_title = f"{parent_title} > {h3_title}"
            sub_section = f"{c.section}.{i + 1}"

            import hashlib
            chunk_id = hashlib.md5((f"{source_label}:" + full_title).encode()).hexdigest()

            result.append(ManualChunk(
                id=chunk_id,
                title=full_title,
                section=sub_section,
                chapter=c.chapter,
                content=h3_content,
                images=images,
            ))

    return result


def register_all():
    register(OperationDocxIngester, description="USP 操作手册 docx → operation_docs 集合")


register_all()
