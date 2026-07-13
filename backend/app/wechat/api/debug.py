from fastapi import APIRouter, Request
from pydantic import BaseModel
import logging
from app.wechat.schemas.message import ApiResponse
from app.wechat.services.wechat_service import wechat_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["调试工具"])


class DebugRequest(BaseModel):
    url: str
    method: str = "GET"
    params: dict = {}
    body: dict = {}


@router.post("/debug", response_model=dict)
async def api_wechat_debug(request: DebugRequest, request_obj: Request):
    try:
        token = request_obj.headers.get("Authorization", "").replace("Bearer ", "")
        logger.info(f"调试请求，token: {token}")
        
        if not token:
            return {
                "code": 401,
                "message": "缺少token",
                "data": {}
            }
        
        method = request.method.upper()
        url = request.url
        params = request.params
        body = request.body
        
        logger.info(f"发送请求: {method} {url}")
        logger.info(f"查询参数: {params}")
        logger.info(f"请求体: {body}")
        result = wechat_service.request_debug(url, method, params, body)
        if result.get('errcode', 0) != 0:
            return {
                "code": result.get('errcode', 400),
                "message": result.get('errmsg', '调试请求失败'),
                "data": {}
            }
        
        logger.info(f"响应数据: {result}")
        
        return {
            "code": 200,
            "message": "调试请求成功",
            "data": {
                "response": result
            }
        }
               
    except Exception as e:
        logger.error(f'调试接口异常: {e}', exc_info=True)
        return {
            "code": 500,
            "message": str(e),
            "data": {}
        }