"""ArchiveReport Pydantic schemas"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ArchiveReportSaveRequest(BaseModel):
    """保存草稿（PUT /archive-reports/{report_id}）"""
    content: str = Field(..., description="归档报告正文（markdown）")
    revision: Optional[int] = Field(None, description="乐观锁版本号（不传则跳过校验）")


class ArchiveReportSubmitRequest(BaseModel):
    """提交审核（POST /archive-reports/{report_id}/submit）

    审核人由系统按问题领域 + 在审负荷概率派审，不再依赖调用方传 reviewer。
    reviewer 字段保留兼容旧前端，服务端忽略。
    """
    reviewer: Optional[str] = Field(None, description="已废弃：系统派审，忽略此字段")
    content: Optional[str] = Field(None, description="可选：提交时顺便保存最新 content")
    revision: Optional[int] = Field(None, description="乐观锁版本号")


class ArchiveReportApproveRequest(BaseModel):
    review_comment: str = Field(..., min_length=1, max_length=5000)
    value_point: str = Field(..., min_length=1, max_length=5000)
    revision: int = Field(..., ge=1)

    @field_validator("review_comment", "value_point")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("审核意见和价值点不能为空")
        return value.strip()


class ArchiveReportRejectRequest(BaseModel):
    """审核驳回（POST /archive-reports/{report_id}/reject）"""
    review_comment: str = Field(..., min_length=1, max_length=5000, description="驳回理由")
    revision: int = Field(..., ge=1)

    @field_validator("review_comment")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写驳回意见")
        return value.strip()


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
    value_point: Optional[str] = None
    can_review: bool = False
    revision: int

    class Config:
        from_attributes = True


class ArchiveReportListResponse(BaseModel):
    """版本历史列表"""
    total: int
    items: list[ArchiveReportResponse]
