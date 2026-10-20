"""admin 数据包导入与数据访问 API。

数据来源：DAS 导出的数据包文件（.bz2 压缩或 .json），承载 GroupEfficiency 等指标数据。
上传后解析（data_import_service.parse_packet_file）→ 按天切分（transform_data）
→ 落库 CollectionData（DataHandler._insert_data / DataService.insert_batch_collection_data）。

同时迁移 DAS「数据访问服务」（项目数据/DAS/api/data.py）：
- POST /data/access       数据访问（读取 CollectionData）
- POST /data/history/access 历史数据访问（指定时间范围读取）
- POST /data/insert       统一数据插入

二维码导入（POST /data/import-qrcode）：解码数据平台导出的数据二维码（QRV1 单码 /
QRV2 分片，gzip+Base64 压缩），得到 GroupEfficiency 指标 JSON 后走与文件导入相同的
落库链路（按导入当天切分入库 CollectionData）。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.modules.admin.schemas_das.request_models import DataAccessRequest
from app.modules.admin.services.data_import_service import (
    MAX_FILE_SIZE,
    _BEIJING_TZ,
    _format_time,
    decode_qr_payload,
    parse_packet_file,
    summarize_content,
    validate_and_prepare_import_data,
)
from app.modules.admin.services.routing_service import DataHandler
from app.modules.admin.utils_das.config import security, DEBUG_MODE

data_router = APIRouter(prefix="/data", tags=["admin-data"])


class QrcodeImportBody(BaseModel):
    """二维码导入请求体：payload（单码/已拼接完整串）与 fragments（分片列表）二选一。"""

    project: str
    payload: Optional[str] = None
    fragments: Optional[List[str]] = None


@data_router.post("/upload-file", summary="上传并解析数据包文件(.json/.bz2)")
async def upload_file(
    file: UploadFile = File(...),
    project: str = Form(None, description="项目标识（未传时取数据包内的 project 字段）"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> dict:
    """上传 .bz2 / .json 数据包文件，解析后按天切分并入库。"""
    try:
        file_bytes = await file.read()

        if len(file_bytes) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="文件大小不能超过20MB")

        # 1. 按扩展名解析文件（.bz2 解压 / .json 直接读）
        raw_data = parse_packet_file(file_bytes, file.filename or "")

        # 2. 校验必要字段并构造插入数据（GroupEfficiency 按天切分）
        insert_data = validate_and_prepare_import_data(raw_data, project)
        if not insert_data.get("content"):
            raise HTTPException(status_code=400, detail="未解析到有效数据，请检查文件内容")

        # 3. 落库 CollectionData
        handler = DataHandler()
        result = await handler._insert_data(insert_data)

        if not isinstance(result, dict) or result.get("code") != 0:
            raise HTTPException(
                status_code=500,
                detail=(result or {}).get("message", "数据入库失败"),
            )

        return {
            "success": True,
            "message": "文件上传成功并已处理",
            "filename": file.filename,
            "file_size": f"{len(file_bytes) / 1024:.2f}KB",
            "project": insert_data.get("project"),
            "indicator": insert_data.get("indicator"),
            "chunk_count": len(insert_data.get("content", [])),
            "chunks": summarize_content(insert_data.get("content", [])),
            "api_response": result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"系统错误: {str(e)}")


@data_router.post("/import-qrcode", summary="二维码导入数据包(QRV1/QRV2)")
async def import_qrcode(
    body: QrcodeImportBody,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> dict:
    """解码数据平台导出的数据二维码，按导入当天落库 CollectionData。

    二维码内容（QRV1 单码 / QRV2 分片列表）解码后为 GroupEfficiency 指标 JSON
    （effectWorkTime/dataIndicators 等 17 字段），构造与文件导入一致的数据包结构
    （时间戳取当前时刻，切分到当天），走 DataHandler._insert_data 落库。
    """
    try:
        # 1. 解码二维码内容 → GroupEfficiency 指标 JSON
        metrics = decode_qr_payload(body.payload, body.fragments)
        if not isinstance(metrics, dict) or not ("effectWorkTime" in metrics or "dataIndicators" in metrics):
            raise ValueError("二维码内容不是搬运效率指标数据，请确认二维码来自数据平台导出")

        # 2. 构造 GroupEfficiency 数据包：时间戳取当前时刻，与文件导入链路共用切分/落库
        now = datetime.now(_BEIJING_TZ)
        insert_data = validate_and_prepare_import_data(
            {
                "project": body.project,
                "indicator": "GroupEfficiency",
                "content": [{"data": [{_format_time(now): metrics}]}],
                "collection_time": now.isoformat(),
            }
        )
        if not insert_data.get("content"):
            raise HTTPException(status_code=400, detail="未解析到有效数据，请检查二维码内容")

        # 3. 落库 CollectionData
        handler = DataHandler()
        result = await handler._insert_data(insert_data)

        if not isinstance(result, dict) or result.get("code") != 0:
            raise HTTPException(
                status_code=500,
                detail=(result or {}).get("message", "数据入库失败"),
            )

        return {
            "success": True,
            "message": "二维码数据导入成功",
            "filename": None,
            "file_size": None,
            "project": insert_data.get("project"),
            "indicator": insert_data.get("indicator"),
            "chunk_count": len(insert_data.get("content", [])),
            "chunks": summarize_content(insert_data.get("content", [])),
            "api_response": result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"系统错误: {str(e)}")


@data_router.post("/access/", summary="数据访问接口")
async def access_data_route(
    request_data: DataAccessRequest,
    request: Request,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> Dict[str, Any]:
    """读取 CollectionData 数据（实时/当天）。

    与 DAS /data/access/ 一致：按 project + tag 读取，indicator 传 '*' 表示取全部指标。
    """
    handler = DataHandler()
    return await handler._get_data(request_data)


@data_router.post("/history/access/", summary="历史数据访问接口")
async def access_history_data_route(
    request_data: DataAccessRequest,
    request: Request,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> Dict[str, Any]:
    """读取 CollectionData 历史数据（按 start_time/end_time 过滤）。

    与 DAS /data/history/access/ 一致。
    """
    handler = DataHandler()
    return await handler._get_data(request_data)


@data_router.post("/insert/", summary="统一数据插入接口")
async def insert_data_route(
    request_data: Dict[str, Any],
    request: Request,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> Dict[str, Any]:
    """批量插入 CollectionData 数据。

    请求体结构：{project, indicator, content: [{data, start_time, end_time}], collection_time}，
    与 DAS /data/insert/ 一致。
    """
    handler = DataHandler()
    return await handler._insert_data(request_data)
