"""ArchiveReport Pydantic schemas"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ArchiveReportSaveRequest(BaseModel):
    """保存草稿（PUT /archive-reports/{report_id}）"""
    content: str = Field(..., description="归档报告正文（markdown）")
    revision: Optional[int] = Field(None, description="乐观锁版本号（不传则跳过校验）")


class ArchiveReportSubmitRequest(BaseModel):
    """提交审核（POST /archive-reports/{report_id}/submit）"""
    reviewer: str = Field(..., min_length=1, description="审核人 users.id")
    content: Optional[str] = Field(None, description="可选：提交时顺便保存最新 content")
    revision: Optional[int] = Field(None, description="乐观锁版本号")


class ArchiveReportRejectRequest(BaseModel):
    """审核驳回（POST /archive-reports/{report_id}/reject）"""
    review_comment: str = Field(..., min_length=1, description="驳回理由")


class ArchiveReportResponse(BaseModel):
    id: int
    task_id: int
    content: str
    version: int
    created_by: str
    created_by_name: Optional[str] = None
    updated_by: Optional[str] = None
    updated_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    submit_status: str
    reviewer: Optional[str] = None
    reviewer_name: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_comment: Optional[str] = None
    revision: int

    class Config:
        from_attributes = True


class ArchiveReportListResponse(BaseModel):
    """版本历史列表"""
    total: int
    items: list[ArchiveReportResponse]
