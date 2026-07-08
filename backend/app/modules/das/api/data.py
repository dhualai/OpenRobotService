import os
from pkgutil import iter_modules
import uuid
import shutil
import json
import traceback
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, Request, HTTPException,UploadFile,File,Form
from typing import Optional, Dict, Any
from datetime import datetime
from app.modules.das.schemas.request_models import DataAccessRequest
from app.modules.das.services.routing_service import DataHandler
from app.modules.das.utils.config import security, DEBUG_MODE
from app.modules.das.customized.groupefficiency import transform_data

router = APIRouter(prefix="/data", tags=["data"])

@router.post("/access/", summary="数据访问接口")
async def access_data_route(
    request_data: DataAccessRequest,
    request: Request,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
):
    handler = DataHandler()
    return await handler._get_data(request_data)

@router.post("/history/access/", summary="历史数据访问接口")
async def access_history_data_route(
    request_data: DataAccessRequest,
    request: Request,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
):
    handler = DataHandler()
    return await handler._get_data(request_data)

@router.post("/insert/", summary="统一数据插入接口")
async def insert_data_route(
    request_data: Dict[str, Any],
    request: Request,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
):
    handler = DataHandler()
    result = await handler._insert_data(request_data)
    return result

async def validate_and_prepare_import_data(data: dict,project: str) -> dict:
    if not project:
        project = data.get("project")
    else:
        data["project"] = project
    indicator = data.get("indicator")
    data_content = data.get("content")
    
    if not project:
        raise ValueError("缺少必填参数: project")
        
    if not indicator:
        raise ValueError("缺少必填参数: indicator")
        
    if not data_content:
        raise ValueError("缺少必填参数: content")
        
    if not isinstance(data_content, list):
        raise ValueError("数据必须是列表类型")
    if len(data_content) == 0:
        raise ValueError("数据内容不能为空")
        
    message_type = data.get("message_type", "realtime_data")
    collection_time = data.get("collection_time", datetime.now().isoformat())

    return transform_data(data)

@router.post("/upload-file")
async def upload_file(file: UploadFile = File(...), project: str = Form(None)):
    try:
        print(f"收到文件上传请求，文件名: {file.filename}, project: {project}")
        
        allowed_extensions = {'.json', '.bz2'}
        file_extension = os.path.splitext(file.filename)[1].lower()
        
        if file_extension not in allowed_extensions:
            print(f"文件类型不允许: {file_extension}")
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "只允许上传 .bz2 和 .json 格式的文件"}
            )
        
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
        
        max_size = 20 * 1024 * 1024
        if file_size > max_size:
            print(f"文件大小超出限制: {file_size / 1024 / 1024:.2f}MB")
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "文件大小不能超过5MB"}
            )
        
        upload_dir = "/data/apps/mmp/wx/uploads"
        if not os.path.exists(upload_dir):
            os.makedirs(upload_dir)
            print(f"创建上传目录: {upload_dir}")
        
        unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{file_extension}"
        file_path = os.path.join(upload_dir, unique_filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        print(f"文件保存成功: {file_path}")
        
        processed_data = await process_uploaded_file(file_path, file_extension)
        if 'data' not in processed_data:
            print(f"处理文件内容失败，未包含数据: {processed_data}")
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "处理文件内容失败，未包含数据"}
            )

        insert_data = await validate_and_prepare_import_data(processed_data["data"],project)
        if len(insert_data) == 0:
            return JSONResponse(
            status_code=500,
            content={"success": False, "message": "数据内容不能为空"}
        )
        
        handler = DataHandler()
        result = await handler._insert_data(insert_data)

        response_data = {
            "success": True,
            "message": "文件上传成功并已处理",
            "filename": file.filename,
            "file_size": f"{file_size / 1024:.2f}KB",
            "data": insert_data,
            "project": insert_data.get("project",'TEST'),
            "api_status": result,
            "api_response": result
        }
        
        return JSONResponse(content=response_data)
        
    except Exception as e:
        print(f"处理文件上传时发生异常: {traceback.format_exc()}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"系统错误: {str(e)}"}
        )

async def process_uploaded_file(file_path: str, file_extension: str):
    try:
        print(f"开始处理文件: {file_path}, 类型: {file_extension}")
        
        if file_extension == '.json':
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"JSON文件解析成功，数据长度: {len(str(data))}")
            return {"data":data}
            
        elif file_extension == '.bz2':
            import bz2
            
            with bz2.open(file_path, 'rt', encoding='utf-8') as f:
                content = f.read()
            
            json_data = json.loads(content)
            print(f"BZ2文件解压成功，字符数: {len(content)}")
            return {"data": json_data, "line_count": content.count('\n') + 1, "data_type": "bz2"}
        else:
            print(f"不支持的文件类型: {file_extension}")
            return {"message": f"不支持的文件类型: {file_extension}", "processed": False}
        
    except Exception as e:
        print(f"处理文件内容时发生异常: {e}", exc_info=True)
        return {"message": f"处理失败: {str(e)}", "processed": False}