from fastapi import APIRouter, HTTPException, Request, Depends
from typing import Optional
from app.wechat.schemas.message import SendMessageRequest, BroadcastMessageRequest, TemplateMessageRequest, SendLinkMessageRequest, ApiResponse, SendNotificationRequest, NotificationResponse
from app.wechat.services.wechat_service import wechat_service
from app.wechat.services.permission_service import PermissionService
from app.wechat.api.dependencies import admin_auth
from app.services.user_service import user_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["消息管理"])


@router.post("/send_message", response_model=ApiResponse)
async def api_send_message(request: SendMessageRequest, credentials: Optional = admin_auth):
    try:
        logger.info(f"尝试给用户 {request.open_id} 发送消息")
        
        if wechat_service.send_message_to_user(request.open_id, request.content, request.url):
            return ApiResponse(code=200, message="消息推送成功")
        else:
            return ApiResponse(code=500, message="消息推送失败")
    except Exception as e:
        logger.error(f'API推送消息异常: {e}', exc_info=True)
        return ApiResponse(code=500, message="服务器内部错误")


@router.post("/broadcast_message", response_model=ApiResponse)
async def api_broadcast_message(request: BroadcastMessageRequest, credentials: Optional = admin_auth):
    try:
        logger.info(f"尝试发送广播消息")
        
        if wechat_service.broadcast_message(request.content):
            return ApiResponse(code=200, message="广播发送成功")
        else:
            return ApiResponse(code=500, message="广播发送失败")
    except Exception as e:
        logger.error(f'API广播消息异常: {e}', exc_info=True)
        return ApiResponse(code=500, message="服务器内部错误")


@router.post("/send_link_message", response_model=ApiResponse)
async def api_send_link_message(request: SendLinkMessageRequest, credentials: Optional = admin_auth):
    try:
        logger.info(f"尝试给用户 {request.open_id} 发送链接消息，标题: {request.title}")
        
        success, result = wechat_service.send_link_message_to_user(request.open_id, request.title, request.description, request.url)
        if success:
            return ApiResponse(code=200, message="链接消息推送成功")
        else:
            logger.info(f'尝试给用户 {request.open_id} 发送链接消息，失败: {result}')
            return ApiResponse(code=500, message=result)
    except Exception as e:
        logger.error(f'API推送链接消息异常: {e}', exc_info=True)
        return ApiResponse(code=500, message=str(e))


async def send_notification_core(payload: dict, token: str = None):
    try:
        import asyncio
        from datetime import datetime
        
        request = SendNotificationRequest(**payload)
        
        logger.info(token)
        
        users = await PermissionService.get_user_list(None, token)
        
        users_dict = {}
        for user in users:
            users_dict[user['username']] = {
                'username': user['username'],
                'name': user['name'],
                'id': user['id']
            }
        
        logger.info(f"尝试发送通知，消息ID: {request.message_id}, 目标用户数: {len(request.at.user_names)}")
        
        async def send_to_user(user_name):
            try:
                title = "通知消息"
                
                # users_dict 仅含 user_service.get_user_list 默认分页(limit=100)内的用户，
                # 命中失败时按 username 查库取真实 id，避免把 username（形如 wechat_xxx）
                # 误当 open_id 推给微信触发 errcode 40003 invalid openid。
                user_info = users_dict.get(user_name)
                if not user_info:
                    user_detail = user_service.get_user_detail(user_name)
                    if user_detail:
                        user_info = {
                            'username': user_detail['username'],
                            'name': user_detail.get('name') or user_name,
                            'id': user_detail['id'],
                        }
                        # 缓存到 users_dict，本次发送后续 .get(user_name) 均可命中真实 name
                        users_dict[user_name] = user_info
                if not user_info or not user_info.get('id'):
                    logger.warning(f'用户 {user_name} 未找到或缺少有效 id，跳过微信推送')
                    return {
                        "name": user_name,
                        "user_name": user_name,
                        "status": "failed",
                        "platform": "wechat",
                        "error_code": "NO_OPEN_ID",
                        "error_message": f'用户 {user_name} 未绑定微信，无法推送'
                    }
                open_id = user_info['id']

                if request.msg_type == "link":
                    description = request.link.content
                    url = request.link.url
                    success, result = wechat_service.send_link_message_to_user(open_id, title, description, url)
                elif request.msg_type == "template":
                    template_data = request.template.data
                    url = request.template.url
                    template_id = request.template.id
                    print(request.template)
                    success, result = wechat_service.send_template_message(open_id, template_data, url, template_id)
                else:
                    logger.error(f'发送消息给用户 {users_dict.get(user_name, {"name": user_name})["name"]} 未知消息类型: {request.msg_type}')
                    return {
                        "name": users_dict.get(user_name, {"name": user_name})["name"],
                        "user_name": user_name,
                        "status": "failed",
                        "platform": "wechat",
                        "error_code": "UNKNOWN_MSG_TYPE",
                        "error_message": f'发送消息给用户 {users_dict.get(user_name, {"name": user_name})["name"]} 未知消息类型,请联系管理员'
                    }
                if success:
                    logger.info(f'发送消息给用户 {users_dict.get(user_name, {"name": user_name})["name"]} 消息类型: {request.msg_type}')
                    return {
                        "name": users_dict.get(user_name, {"name": user_name})["name"],
                        "user_name": user_name,
                        "status": "delivered",
                        "platform": "wechat"
                    }
                else:
                    err_code = result.get('errcode', 0)
                    if err_code in (45047, 45015):
                        name = users_dict.get(user_name, {"name": user_name})["name"]
                        err_map = {
                            45047: f"客服接口下行条数超过上限,请主动联系 {name} 处理(也可让其在服务号聊天窗口激活交互)",
                            45015: f"回复时间超过限制,请主动联系 {name} 处理(也可让其在服务号聊天窗口激活交互)",
                            40001: f"服务主体异常，请联系管理员",
                            40003: f"不合法的 OpenID，请联系管理员",
                            40008: f"不合法的消息类型，请联系管理员",
                            40013: f"不合法的 AppID，请联系管理员",
                            40036: f"不合法的 template_id 长度，请联系管理员",
                            40037: f"不合法的 template_id，请联系管理员",
                            40039: f"不合法的 URL 长度，请联系管理员",
                            40249: f"禁止发送营销内容，请联系管理员",
                            43116: f"模板被限制下发，请联系管理员",
                            47003: f"参数格式错误，请联系管理员"
                        }
                        error_message = err_map.get(err_code, "未知错误")
                    else:
                        error_message = result.get("errmsg", '未知错误')

                    logger.info(f"尝试给用户 {user_name} 发送消息结果: {result}")
                    return {
                        "name": users_dict.get(user_name, {"name": user_name})["name"],
                        "user_name": user_name,
                        "status": "failed",
                        "platform": "wechat",
                        "error_code": "SEND_FAILED",
                        "error_message": error_message
                    }
            except Exception as e:
                logger.error(f'发送消息给用户 {users_dict.get(user_name, {"name": user_name})["name"]} 异常: {e}')
                return {
                    "name": users_dict.get(user_name, {"name": user_name})["name"],
                    "user_name": user_name,
                    "status": "failed",
                    "platform": "wechat",
                    "error_code": "EXCEPTION",
                    "error_message": f'发送消息给用户 {users_dict.get(user_name, {"name": user_name})["name"]} 异常,请联系管理员'
                }
        
        tasks = [send_to_user(user_name) for user_name in request.at.user_names]
        recipients = await asyncio.gather(*tasks)
        
        success_count = sum(1 for r in recipients if r['status'] == 'delivered')
        total_count = len(recipients)
        
        if success_count == total_count:
            status = "success"
            code = 200
            message = "所有消息发送成功"
        elif success_count > 0:
            status = "partial"
            code = 206
            message = "部分消息发送失败"
        else:
            status = "failed"
            code = 500
            message = "所有消息发送失败"
        
        response = NotificationResponse(
            code=code,
            message=message,
            message_id=request.message_id,
            data={
                "status": status,
                "send_time": datetime.now().isoformat(),
                "recipients": recipients
            },
            timestamp=datetime.now().isoformat()
        )
        
        return response.dict()
    except Exception as e:
        logger.error(f'发送通知异常: {e}', exc_info=True)
        from datetime import datetime
        return NotificationResponse(
            code=500,
            message=str(e),
            message_id=payload.get('message_id', ""),
            data={
                "status": "failed",
                "send_time": datetime.now().isoformat(),
                "recipients": []
            },
            timestamp=datetime.now().isoformat()
        ).dict()


@router.post("/webnotify", response_model=NotificationResponse)
async def api_send_notification(request: SendNotificationRequest, request_obj: Request, credentials: Optional = admin_auth):
    token = request_obj.headers.get("Authorization", "").replace("Bearer ", "")
    result = await send_notification_core(request.dict(), token)
    return NotificationResponse(**result)