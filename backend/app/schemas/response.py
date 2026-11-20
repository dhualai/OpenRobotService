"""统一响应契约（Pydantic）。原 `app/modules/aas/schemas/response.py`，迁入底座（MIGRATION.md 阶段 1）。"""
from typing import Optional, Dict, List, Any
from pydantic import BaseModel

class BaseResponse(BaseModel):
    code: int = 0
    message: str = "success"
    timestamp: Optional[int] = None
    request_id: Optional[str] = None

class SuccessResponse(BaseResponse):
    data: Optional[Dict[str, Any]] = None

class ErrorResponse(BaseResponse):
    code: int = 1
    message: str
    error_details: Optional[Dict[str, Any]] = None

class DataResponse(BaseResponse):
    data: Any

class ListResponse(BaseResponse):
    data: List[Any]
    total: Optional[int] = None
    page: Optional[int] = None
    page_size: Optional[int] = None
