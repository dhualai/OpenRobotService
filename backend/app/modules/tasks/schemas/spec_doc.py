"""工单「问题文档」schema。

- SpecDocUpdate：保存正文（md 在线编辑），revision 为乐观锁
- SpecDocResponse：读取响应（无文档时 exists=false）
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SpecDocSourceFile(BaseModel):
    """原始上传文件引用（保留 .md/.doc/.docx 原件，可下载）。"""
    object_path: str = Field(..., description="MinIO object_path")
    filename: Optional[str] = Field(None, description="原始文件名")
    size: Optional[int] = Field(None, description="文件大小（bytes）")


class SpecDocUpdate(BaseModel):
    content: str = Field(..., description="文档正文（markdown）")
    revision: Optional[int] = Field(
        None, description="客户端当前修订号（乐观锁；与库中不一致返回 409；不传=强制覆盖）"
    )
    source: Optional[str] = Field(None, description="来源：inline / upload / ai_summary")
    source_files: Optional[List[Dict[str, Any]]] = Field(
        None, description="原始文件引用列表（上传场景透传；不传则不改动）"
    )


class SpecDocResponse(BaseModel):
    exists: bool = Field(..., description="文档是否存在")
    task_id: int
    content: str = ""
    content_type: str = "markdown"
    source: Optional[str] = None
    source_files: List[Dict[str, Any]] = Field(default_factory=list)
    revision: int = 0
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    updated_by_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SpecDocParseResult(BaseModel):
    """上传解析结果（解析为 markdown + 原始文件落 MinIO 的 object_path）。"""
    content: str = Field(..., description="解析后的 markdown 正文")
    filename: str = Field(..., description="原始文件名")
    size: int = Field(..., description="原始文件大小（bytes）")
    object_path: str = Field("", description="原始文件 MinIO object_path（上传失败为空串）")


class SpecDocImageResult(BaseModel):
    """图片上传结果（编辑器插入图片/粘贴用）。"""
    url: str = Field(..., description="图片代理 URL（/api/tasks/files/{object_path}）")
