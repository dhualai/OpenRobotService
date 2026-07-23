"""
平台 FAQ 知识库 → platform_faq 集合

来源：platform_faq/platform_faq.jsonl（摇人吧服务号自身介绍）
用途：当用户问"支持什么工单类型""有哪些角色""工单流转是怎样的"等
     关于服务号本身的问题时，通过向量检索命中后注入 prompt。
"""
import json
import hashlib
from pathlib import Path
from typing import List

from ai.config import get_docs_dir
from ai.ingestion.base import BaseIngester, Chunk
from ai.ingestion.registry import register

# 源文件固定在项目目录下（跟着代码走，不依赖外部 Data 目录）
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent  # ai/ 目录


# ── 数据模型 ────────────────────────────────────────────────────

class PlatformFaqEntry:
    """平台 FAQ 条目（轻量，与 tech FAQ 的 FaqEntry 不共享类型）"""
    __slots__ = ("faq_id", "question", "answer", "answer_mode", "source_type")
    def __init__(self, faq_id="", question="", answer="", answer_mode="", source_type=""):
        self.faq_id = faq_id
        self.question = question
        self.answer = answer
        self.answer_mode = answer_mode
        self.source_type = source_type


# ── Ingester ─────────────────────────────────────────────────────

class PlatformFaqIngester(BaseIngester[PlatformFaqEntry]):
    """平台 FAQ → platform_faq 集合"""

    source_paths = [
        get_docs_dir() / "platform_faq" / "platform_faq.jsonl",
    ]
    collection_prefix = "platform_faq"
    collection_type = "platform_faq"
    rebuild = True

    # 活跃集合指针文件（放在 kb/ 目录下统一管理）
    _POINTER_FILE = Path(__file__).resolve().parent.parent.parent / "kb" / "active_platform_faq_collection.txt"

    @staticmethod
    def pointer_reader() -> str:
        f = PlatformFaqIngester._POINTER_FILE
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
        return ""

    @staticmethod
    def pointer_writer(name: str) -> None:
        PlatformFaqIngester._POINTER_FILE.write_text(name + "\n", encoding="utf-8")

    def get_source_label(self) -> str:
        return "平台FAQ"

    def validate_source_files(self) -> bool:
        if self.source_paths[0].exists():
            return True
        self._log("[WARN] platform_faq.jsonl 不存在，跳过")
        return False

    def parse(self) -> List[PlatformFaqEntry]:
        entries: List[PlatformFaqEntry] = []
        with open(self.source_paths[0], encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                j = json.loads(line)
                entries.append(PlatformFaqEntry(
                    faq_id=j.get("faq_id", ""),
                    question=j.get("question", ""),
                    answer=j.get("answer", ""),
                    answer_mode=j.get("answer_mode", "explain"),
                    source_type=j.get("source_type", "direct_faq"),
                ))
        self._log(f"  加载 {len(entries)} 条平台FAQ")
        return entries

    def to_chunk(self, entry: PlatformFaqEntry) -> Chunk:
        # question + answer 合并作为向量化和检索文本
        text = f"{entry.question}\n{entry.answer}"
        return Chunk(
            id=hashlib.md5(entry.faq_id.encode()).hexdigest(),
            text=text,
            payload={
                "faq_id": entry.faq_id,
                "question": entry.question,
                "answer": entry.answer,
                "content": text,
            },
        )


def register_all():
    register(PlatformFaqIngester, description="平台 FAQ（服务号介绍）→ platform_faq 集合")


register_all()
