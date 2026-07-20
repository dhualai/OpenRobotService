"""
知识库导入模块 — 统一入库框架

核心接口：
  - BaseIngester / Chunk   → 基类，所有 parser 继承它
  - registry.register()    → 注册 parser
  - ingest_all.ingest_all() → 一键入库

旧接口（保持兼容）：
  - loader.ingest_documents() → 通用 md 导入
  - 各独立脚本的 auto_ingest() 仍可直接调用
"""
from ai.ingestion.base import BaseIngester, Chunk, FunctionalIngester
from ai.ingestion.registry import register, discover_parsers, list_registered

# 旧接口兼容
from ai.ingestion.loader import ingest_documents, parse_markdown_file, DocChunk

__all__ = [
    "BaseIngester", "Chunk", "FunctionalIngester",
    "register", "discover_parsers", "list_registered",
    "ingest_documents", "parse_markdown_file", "DocChunk",
]
