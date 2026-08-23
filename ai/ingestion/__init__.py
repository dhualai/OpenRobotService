"""
知识库导入模块 — 统一入库框架

核心接口：
  - BaseIngester / Chunk       → 基类，所有 parser 继承它
  - KBDomainIngester           → 统一 markdown 入库器（kb/{domain}/）
  - registry.register()        → 注册 parser
  - ingest_all.ingest_all()    → 一键入库（5 domain 循环）
"""
from ai.ingestion.base import BaseIngester, Chunk, FunctionalIngester
from ai.ingestion.registry import register, discover_parsers, list_registered

__all__ = [
    "BaseIngester", "Chunk", "FunctionalIngester",
    "register", "discover_parsers", "list_registered",
]
