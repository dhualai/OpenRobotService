"""再导出 shim（MIGRATION.md 阶段 1）。真实契约已迁至 `app/schemas/response.py`。"""
from app.schemas.response import (
    BaseResponse, SuccessResponse, ErrorResponse, DataResponse, ListResponse,
)

__all__ = ["BaseResponse", "SuccessResponse", "ErrorResponse", "DataResponse", "ListResponse"]
