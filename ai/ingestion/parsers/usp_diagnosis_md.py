"""
USP 诊断知识库 → usp_diagnosis 集合

来源：docs/PRODUCT/usp_diagnosis_kb.md
      从《USP 产品功能手册 v1》(2025.12) 提取的诊断相关内容
      含：使用建议 / 术语定义 / 异常诊断 / 地图故障排查

用途：当用户询问 USP 调度系统相关问题（机器人不接任务、中途停滞、
      掉线、地图报错等）时，通过向量检索命中后注入 prompt 作为背景知识。
"""
import re
import hashlib
from pathlib import Path
from typing import List
from dataclasses import dataclass, field

from ai.config import get_docs_dir
from ai.ingestion.base import BaseIngester, Chunk
from ai.ingestion.registry import register

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent  # ai/ 目录


# ── 数据模型 ────────────────────────────────────────────────────

@dataclass
class USPProseSection:
    """USP 文档中的一个章节"""
    title: str
    level: int              # 标题层级 1-2
    content: str            # 正文（markdown）
    order: int = 0


# ── Ingester ─────────────────────────────────────────────────────

class USPDiagnosisIngester(BaseIngester[USPProseSection]):
    """USP 产品功能手册（诊断章节）→ usp_diagnosis 集合"""

    source_paths = [
        get_docs_dir() / "PRODUCT" / "usp_diagnosis_kb.md",
    ]
    collection_prefix = "usp_diagnosis"
    collection_type = "usp_diagnosis"
    rebuild = True

    _POINTER_FILE = _PROJECT_DIR / "kb" / "active_usp_diagnosis_collection.txt"

    @staticmethod
    def pointer_reader() -> str:
        f = USPDiagnosisIngester._POINTER_FILE
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
        return ""

    @staticmethod
    def pointer_writer(name: str) -> None:
        USPDiagnosisIngester._POINTER_FILE.write_text(name + "\n", encoding="utf-8")

    def get_source_label(self) -> str:
        return "USP 诊断知识库"

    def validate_source_files(self) -> bool:
        if self.source_paths[0].exists():
            return True
        self._log("[WARN] usp_diagnosis_kb.md 不存在，跳过")
        return False

    def parse(self) -> List[USPProseSection]:
        md_path = self.source_paths[0]
        text = md_path.read_text(encoding="utf-8")

        # 按 ## 标题切分章节
        sections = _parse_markdown_sections(text)
        self._log(f"  加载 {len(sections)} 个章节")
        return sections

    def to_chunk(self, s: USPProseSection) -> Chunk:
        text = f"【USP 诊断知识库】{s.title}\n{s.content}"

        return Chunk(
            id=self.stable_id("usp_diagnosis", str(s.order), s.title[:60]),
            text=text,
            payload={
                "title": s.title,
                "level": s.level,
                "section_order": s.order,
                "content": s.content,
                "source": "USP 产品功能手册 v1 (2025.12)",
            },
        )


# ── Markdown 切块 ───────────────────────────────────────────────

def _parse_markdown_sections(md_text: str) -> List[USPProseSection]:
    """按 ## 标题切分 markdown 为 section。"""
    heading_pattern = re.compile(r'^(#{1,4})\s+(.+?)[ \t\r]*$', re.MULTILINE)
    matches = list(heading_pattern.finditer(md_text))

    if not matches:
        return [USPProseSection(title="USP 诊断知识库", level=1,
                                 content=md_text, order=0)]

    sections: List[USPProseSection] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip().rstrip("*").strip()

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        content_block = md_text[start:end]

        # 去掉标题行本身，保留正文
        body = "\n".join(content_block.split("\n")[1:]).strip()
        if not body or len(body) < 100:
            continue

        sections.append(USPProseSection(
            title=title,
            level=level,
            content=body,
            order=len(sections),
        ))

    return _merge_small_sections(sections, min_chars=200)


def _merge_small_sections(sections: List[USPProseSection], min_chars: int = 200) -> List[USPProseSection]:
    """合并过小的 section 到上一个"""
    if len(sections) <= 1:
        return sections

    merged: List[USPProseSection] = []
    for s in sections:
        if len(s.content) < min_chars and merged:
            prev = merged[-1]
            prev.content += f"\n\n### {s.title}\n{s.content}"
        else:
            merged.append(s)
    return merged


def register_all():
    register(USPDiagnosisIngester, description="USP 诊断知识库 → usp_diagnosis 集合")


register_all()
