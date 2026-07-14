# 路径: backend/app/ai/ingestion/__init__.py
"""
知识库导入模块
"""
from app.ai.ingestion.loader import ingest_documents, parse_markdown_file, DocChunk

__all__ = ["ingest_documents", "parse_markdown_file", "DocChunk"]
