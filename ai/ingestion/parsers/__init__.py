"""
知识库 Parser 集合

每个模块对应一种源文件格式，定义 Ingester 子类。
通过 register() 函数注册到全局 registry。
"""
from ai.ingestion.registry import register
from ai.ingestion.base import BaseIngester, Chunk

__all__ = ["BaseIngester", "Chunk", "register"]
