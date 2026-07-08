from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional, List, Dict, Any
from app.modules.das.schemas.request_models import NotifyRequest, NotifyResponse
from app.modules.das.utils.config import security, DEBUG_MODE
from app.modules.das.utils.logging import get_logger
from app.modules.das.services.permission_service import PermissionService
from app.modules.das.services.wechat_service import WeChatService

logger = get_logger()

router = APIRouter(prefix="/backend/notify", tags=["notify"])

@router.post("/", response_model=NotifyResponse, summary="发送通知")
async def send_notification(
        request: NotifyRequest,
        request_obj: Request,
        credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
):
    try:
        logger.info(f"收到通知请求: {request.msg_type}")

        token = request_obj.headers.get("Authorization", "").replace("Bearer ", "")

        users = await PermissionService.get_users_list(request_obj, token)
        logger.info(f"获取用户列表成功，共 {len(users)} 个用户")
        at_info = request.at
        if at_info:
            logger.info(f"@用户: {at_info.user_names}, 是否@所有人: {at_info.is_all}")
            if at_info.is_all:
                raise HTTPException(status_code=400, detail="暂不支持@所有人功能")
        filtered_users = [row for row in users if row["username"] in at_info.user_names]

        if request.msg_type == "text":
            if not request.text:
                raise HTTPException(status_code=400, detail="文本消息必须包含 text 字段")

            content = request.text.content

            logger.info(f"发送文本通知: {content}")

            return NotifyResponse(
                status="success",
                message="文本通知发送成功"
            )
        elif request.msg_type == "link":

            content = request.link.content
            import time
            max_retries = 5
            retry_interval = 1
            failed_users = []
            
            for user in filtered_users:
                retry_count = 0
                success = False
                
                while retry_count < max_retries and not success:
                    try:
                        result = WeChatService.send_link_message(
                            open_id=user["id"],
                            title=request.link.title,
                            description=content,
                            url=request.link.url
                        )
                        if result.get("status") != "error":
                            success = True
                        else:
                            retry_count += 1
                            if retry_count < max_retries:
                                logger.info(f"发送消息给用户 {user['id']} 失败，{retry_interval}秒后重试，第{retry_count}次")
                                time.sleep(retry_interval)
                    except Exception as e:
                        retry_count += 1
                        if retry_count < max_retries:
                            logger.error(f"发送消息给用户 {user['id']} 异常: {str(e)}，{retry_interval}秒后重试，第{retry_count}次")
                            time.sleep(retry_interval)
                
                if not success:
                    failed_users.append(user["id"])
            
            if failed_users:
                logger.warning(f"部分用户消息发送失败: {failed_users}")
                if len(failed_users) == len(filtered_users):
                    raise HTTPException(status_code=500, detail=f"所有用户消息发送失败")
                else:
                    return NotifyResponse(
                        status="partial_success",
                        message=f"部分用户消息发送失败: {failed_users}"
                    )
            else:
                return NotifyResponse(
                    status="success",
                    message="@通知发送成功"
                )
        else:
            logger.info(f"发送 {request.msg_type} 类型的通知")
            return NotifyResponse(
                status="success",
                message=f"{request.msg_type} 类型的通知发送成功"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"发送通知失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"发送通知失败: {str(e)}")