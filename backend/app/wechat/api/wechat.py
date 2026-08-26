import os
import json
import base64
import shutil
import traceback
import uuid
from datetime import datetime
import logging
import csv
import asyncio
from difflib import SequenceMatcher
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Query, HTTPException, Body, Depends, UploadFile, File, Form
from fastapi.responses import RedirectResponse, PlainTextResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.wechat.services.ai_service import ai_service
from app.wechat.utils.crypto import verify_wechat_signature, generate_wechat_username, generate_wechat_user_password
from app.wechat.utils.wechat_message import parse_wechat_xml, build_reply_text, build_reply_news
from app.wechat.services.auth_service import auth_service
from app.wechat.services.data_service import data_service
from app.wechat.services.wechat_service import wechat_service
from app.wechat.services.project_ticket_service import project_ticket_service
from app.wechat.utils.qrcode import process_qrcode_content, decompress_data
from app.wechat.utils.opt_logger import log_operation
from app.services.hmac_utils import generate_password, chinese_to_pinyin, get_password_hash, verify_password
from app.services.user_service import user_service
from app.core.database import db_manager, UserDB
from app.wechat.services.permission_service import PermissionService
from app.wechat.api.match_report import parse_daily_report
from app.modules.admin.services.daily_report_service import daily_report_service
from app.wechat.api.dependencies import admin_auth
from app.modules.admin.api.auth import get_current_active_user_from_token

templates = Jinja2Templates(directory="app/wechat/templates")
logger = logging.getLogger(__name__)

router = APIRouter(tags=["微信接口"])

user_states = {}


def resolve_callback_target(state: Optional[str], scheme: str, netloc: str) -> str:
    """根据微信授权回调的 state 还原前端回跳地址（不含 token）。

    新格式（前端 buildStateFromPath）：state 为 base64url 编码的**完整地址**
        （origin + 部署前缀 + 路由路径，如 https://usp.ep-zl.com/p/app/app/admin/wechat）。
        解码成功且以 http(s):// 开头则直接使用，避免丢失 /p/app 部署前缀。
    旧格式（兼容）：state 为路由路径且把 '/' 编码成 '0'（如 0app0admin0wechat），
        此时用回调请求的 scheme+netloc 重拼（可能缺少部署前缀，仅作兜底）。
    """
    if state:
        try:
            padding = '=' * (-len(state) % 4)
            try:
                decoded = base64.urlsafe_b64decode(state + padding).decode('utf-8')
            except Exception:
                decoded = base64.b64decode(state + padding).decode('utf-8')
            if decoded.startswith('http://') or decoded.startswith('https://'):
                return decoded
        except Exception:
            pass
        # 旧格式兜底：'0' 还原为 '/'
        processed_path = state.replace('0', '/')
    else:
        processed_path = '/app/call'
    return f"{scheme}://{netloc}{processed_path}"


class _InMemoryUploadFile:
    """适配 ResourceService.create_resource 的内存文件对象（模拟 starlette UploadFile）。

    create_resource 仅读取 file.filename / file.content_type / await file.read()，
    用这个轻量包装即可把下载到的头像字节喂给它，无需走真实 multipart 上传。
    """

    def __init__(self, content: bytes, filename: str, content_type: str):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._content


async def _create_avatar_resource(openid: str, image_bytes: bytes, content_type: str, nickname: Optional[str]) -> Optional[int]:
    """把头像字节建成一条 resources 记录，返回资源 id；失败返回 None。"""
    try:
        from app.core.db import AsyncSessionLocal
        from app.modules.admin.resource_manager.services.resource_service import ResourceService
        from app.modules.admin.resource_manager.models.resource import ResourceType

        ext_map = {'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png',
                   'image/gif': 'gif', 'image/webp': 'webp', 'image/bmp': 'bmp'}
        ext = ext_map.get(content_type.split(';')[0].strip().lower(), 'jpg')
        filename = f"{openid}_avatar.{ext}"

        upload_file = _InMemoryUploadFile(image_bytes, filename, content_type)
        async with AsyncSessionLocal() as db:
            resource = await ResourceService.create_resource(
                db,
                file=upload_file,
                owner_id=openid,
                resource_type=ResourceType.IMAGE,
                category='avatar',
                description=f'微信头像：{nickname or openid}',
            )
        return getattr(resource, 'id', None)
    except Exception as e:
        logger.error(f'创建微信头像资源失败: {e}', exc_info=True)
        return None


async def fetch_and_persist_wechat_user_profile(openid: str, sns_access_token: Optional[str]):
    """拉取微信网页授权用户信息，昵称写入 users.name（仅当当前为空），头像下载后建资源写入 users.avatar_resource_id。

    纯辅助流程：任何环节失败仅记日志，不影响主登录链路。
    """
    try:
        if not sns_access_token:
            logger.warning('未拿到网页授权 access_token，跳过微信用户资料拉取')
            return

        userinfo = await wechat_service.get_sns_userinfo(sns_access_token, openid)
        if not userinfo or 'openid' not in userinfo:
            logger.warning(f'拉取微信用户信息失败（可能 OAuth scope 非 snsapi_userinfo）: {userinfo}')
            return

        nickname = userinfo.get('nickname')
        headimgurl = userinfo.get('headimgurl')

        username = generate_wechat_username(openid)
        user_detail = user_service.get_user_detail(username)
        if not user_detail:
            logger.warning(f'更新微信用户资料失败：用户不存在 {username}')
            return

        update_fields = {}
        # 仅当用户尚未设置真实姓名时才用微信昵称填充，避免覆盖「@张三」手动绑定的姓名
        if nickname and not (user_detail.get('name') or '').strip():
            update_fields['name'] = nickname

        if headimgurl:
            download_result = await wechat_service.download_avatar(headimgurl)
            if download_result:
                image_bytes, content_type = download_result
                resource_id = await _create_avatar_resource(openid, image_bytes, content_type, nickname)
                if resource_id:
                    update_fields['avatar_resource_id'] = resource_id
                else:
                    logger.warning(f'微信头像资源创建失败，跳过头像更新: {openid}')
            else:
                logger.warning(f'微信头像下载失败，跳过头像更新: {openid}')

        if update_fields:
            success = db_manager.update_user(user_detail['id'], **update_fields)
            if success:
                logger.info(f'已更新微信用户资料 {openid}: {list(update_fields.keys())}')
            else:
                logger.warning(f'更新微信用户资料失败 {openid}: {list(update_fields.keys())}')
        else:
            logger.info(f'微信用户资料无需更新 {openid}')
    except Exception as e:
        logger.error(f'拉取并保存微信用户资料异常: {e}', exc_info=True)


@router.get("", response_class=PlainTextResponse)
def wechat_verify(
    signature: str = Query(..., description="微信加密签名"),
    timestamp: str = Query(..., description="时间戳"),
    nonce: str = Query(..., description="随机数"),
    echostr: str = Query(..., description="随机字符串")
):
    try:
        logger.info("微信服务器验证请求")
        logger.debug(f"接收到的signature: {signature}")
        logger.debug(f"接收到的timestamp: {timestamp}")
        logger.debug(f"接收到的nonce: {nonce}")
        logger.debug(f"接收到的echostr: {echostr}")
        
        if verify_wechat_signature(signature, timestamp, nonce, settings.WECHAT_CONFIG['token']):
            return echostr
        else:
            logger.warning("签名验证失败")
            return Response(content="签名验证失败", status_code=403)
    except Exception as e:
        logger.error(f'验证微信请求时发生异常: {e}', exc_info=True)
        return Response(content="内部服务器错误", status_code=500)


@router.post("")
async def handle_wechat_message(request: Request):
    try:
        logger.info("收到微信服务器POST请求")
        
        data = await request.body()
        logger.debug(f"收到的原始数据长度: {len(data)} 字节")
        logger.debug(f"收到的原始数据: {data}")
        
        if not data:
            logger.warning("请求体为空")
            raise HTTPException(status_code=400, detail="请求体为空")
        
        logger.info("开始解析XML数据")
        message = parse_wechat_xml(data)
        logger.info(f"解析后的消息内容: {message}")
        
        msg_type = message.get('MsgType')
        logger.info(f"消息类型: {msg_type}")
        
        if msg_type == 'text':
            logger.info("处理文本消息")
            return await handle_text_message(message)
        
        elif msg_type == 'event':
            logger.info("处理事件消息")
            return await handle_event_message(message)
        
        logger.warning(f"未知的消息类型: {msg_type}，返回空内容")
        return Response(content='', media_type="text/xml")
        
    except Exception as e:
        logger.error(f'处理微信消息失败: {e}', exc_info=True)
        return Response(content='', media_type="text/xml")


async def handle_text_message(message: dict):
    content = message.get('Content', '').strip()
    from_user_name = message.get('FromUserName')
    to_user_name = message.get('ToUserName')

    logger.info(f'收到用户 {from_user_name} 的消息: {content}')
    permissions_data = auth_service.get_user_permissions(from_user_name)
    
    if permissions_data is None:
        reply_xml = build_reply_text(from_user_name, to_user_name, '获取权限信息失败')
        return Response(content=reply_xml, media_type="text/xml")
    if permissions_data.get("name", None) is None and '@' != content[0]:
        reply_xml = build_reply_text(from_user_name, to_user_name, '请先输入您的名字。例如：\r\n@张三')
        return Response(content=reply_xml, media_type="text/xml")

    if content.startswith('@'):
        user_name = content[1:]
        logger.info(f'用户 {from_user_name} 输入的名字是: {user_name}')

        # save_user_name 哈希存储后，明文凭证仅通过返回值一次性返回
        usp_credentials = auth_service.save_user_name(from_user_name, user_name)
        usp_name = ""
        usp_password = ""

        if usp_credentials:
            usp_name = usp_credentials.get("username", "")
            usp_password = usp_credentials.get("password", "")
            
        log_operation(
            timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
            client_ip='127.0.0.1',
            method='POST',
            path='',
            status_code=200,
            processing_time=0.0,
            operator=generate_wechat_username(from_user_name),
            summary='绑定姓名',
        )

        confirmation_message = f"{user_name}！\n\n您的信息已保存，请联系相关管理员获取项目权限。\r\nusp用户名：{usp_name}\r\n初始密码：{usp_password} \n请及时自行修改密码。修改方法：回复\"#修改密码\"。"
        reply_xml = build_reply_text(from_user_name, to_user_name, confirmation_message)
        return Response(content=reply_xml, media_type="text/xml")
    elif content.startswith('help') or content.startswith('帮助'):
        reply_xml = build_reply_text(from_user_name, to_user_name, '指令：\r\n@张三：绑定姓名为张三\r\n#修改密码：修改USP密码\r\n&或建议+建议内容：提交建议或意见\r\n#或日报+日报内容：提交日报')
        return Response(content=reply_xml, media_type="text/xml")
    elif content.startswith('日报模板'):
        reply_xml = build_reply_text(from_user_name, to_user_name, '日报：项目名或者项目编号\r\n2026年3月20日\r\n替换你的内容')
        return Response(content=reply_xml, media_type="text/xml")
    elif content == '#修改密码':
        user_states[from_user_name] = {'state': 'changing_password_step1', 'temp_data': {}}
        reply_xml = build_reply_text(from_user_name, to_user_name, '请输入新的 USP 密码')
        return Response(content=reply_xml, media_type="text/xml")
    elif from_user_name in user_states:
        user_state = user_states[from_user_name]
        if user_state['state'] == 'changing_password_step1':
            first_password = content
            user_state['state'] = 'changing_password_step2'
            user_state['temp_data']['first_password'] = first_password
            reply_xml = build_reply_text(from_user_name, to_user_name, '请再次输入新的 USP 密码')
            return Response(content=reply_xml, media_type="text/xml")
        elif user_state['state'] == 'changing_password_step2':
            second_password = content
            first_password = user_state['temp_data'].get('first_password', '')
            
            if first_password == second_password:
                try:
                    token, refresh_token = auth_service.get_wechat_user_token(from_user_name)
                    username = generate_wechat_username(from_user_name)
                    success, message = auth_service.change_password(username, token, second_password)
                    
                    if success:
                        reply_message = 'USP 密码修改成功！\n 请联系调度对接人以生效新密码。'
                    else:
                        reply_message = f'USP 密码修改失败：{message}'
                except Exception as e:
                    logger.error(f'修改 USP 密码异常: {e}')
                    reply_message = 'USP 密码修改失败，请稍后重试'
                
                del user_states[from_user_name]
                reply_xml = build_reply_text(from_user_name, to_user_name, reply_message)
                return Response(content=reply_xml, media_type="text/xml")
            else:
                del user_states[from_user_name]
                reply_xml = build_reply_text(from_user_name, to_user_name, '两次输入的密码不一致，请重新输入"#修改密码"开始修改')
                return Response(content=reply_xml, media_type="text/xml")
    elif content.startswith('#') or content.startswith('日报'):
        logger.info(f'用户 {from_user_name} 输入的日报内容: {content}')
        
        report_data = parse_daily_report(content)
        print(report_data)
        if report_data['project'] is None:
            reply_xml = build_reply_text(from_user_name, to_user_name, '日报格式错误，请使用格式：日报：项目名 或 项目：项目名')
            return Response(content=reply_xml, media_type="text/xml")
        
        if report_data['date'] is None:
            reply_xml = build_reply_text(from_user_name, to_user_name, '日期格式错误，请使用格式：2026年3月20日')
            return Response(content=reply_xml, media_type="text/xml")
        
        user_id = from_user_name      
        token, refresh_token = auth_service.get_wechat_user_token(user_id)
        projects = await project_ticket_service.get_user_projects(generate_wechat_username(user_id), token)
        if projects is None:
            reply_xml = build_reply_text(from_user_name, to_user_name, '获取项目列表失败')
            return Response(content=reply_xml, media_type="text/xml")
        if len(projects) == 0:
            reply_xml = build_reply_text(from_user_name, to_user_name, '没有关联项目')
            return Response(content=reply_xml, media_type="text/xml")
        
        project_input = report_data['project'].strip()
        project = None
        
        if project_input.isdigit():
            index = int(project_input) - 1
            if 0 <= index < len(projects):
                project = projects[index]
        elif any(char.isalpha() or char in '_-.' for char in project_input):
            project = next((p for p in projects if p.get("project_code") == project_input), None)
        else:
            best_match = None
            best_ratio = 0.7
            
            for p in projects:
                project_name = p.get("name", "")
                if project_name:
                    matcher = SequenceMatcher(None, project_input.lower(), project_name.lower())
                    ratio = matcher.ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = p
            
            project = best_match
        if project is None:
            reply_xml = build_reply_text(from_user_name, to_user_name, f'项目 {report_data["project"]} 不存在')
            return Response(content=reply_xml, media_type="text/xml")
        
        reply_message = f'上报日报：{project["project_code"]}\r\n{project["name"]}\r\n{report_data["date"]}'
        
        try:
            report_data_api = {
                "project_code": project.get("project_code", ""),
                "report_date": report_data["date"],
                "report_content": {
                    "content": report_data.get("content", "")
                },
                "reporter": '',
                "reporter_id": generate_wechat_username(user_id)
            }
            print(token)
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{settings.DATA_SERVICE_URL}/api/daily-reports/",
                    json=report_data_api,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10
                ) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        logger.info(f'日报创建成功: {response_data}')
                        reply_message += '\r\n日报已成功提交！'
                    else:
                        error_text = await response.text()
                        logger.error(f'日报创建失败: HTTP {response.status}, {error_text}')
                        reply_message += f'\r\n日报提交失败，请稍后重试'
        except Exception as e:
            logger.error(f'调用日报API异常: {e}')
            reply_message += f'\r\n日报提交异常，请联系管理员'
        
        reply_xml = build_reply_text(from_user_name, to_user_name, reply_message)
        return Response(content=reply_xml, media_type="text/xml")
    elif content.startswith('&') or content.startswith('建议'):
        suggestion = content[1:]
        logger.info(f'用户 {from_user_name} 输入的建议或者意见是: {suggestion}')
        
        csv_file_path = 'suggestions.csv'
        file_exists = os.path.exists(csv_file_path)
        with open(csv_file_path, 'a', encoding='utf-8', newline='') as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(['时间', '用户', '内容'])
            writer.writerow([
                datetime.now().astimezone().isoformat(timespec='milliseconds'),
                from_user_name,
                suggestion
            ])
        
        async def send_notification_to_user(user_name):
            try:
                from_name = permissions_data.get("name", '未知用户')
                title = "新建议或意见"
                description = f'用户 {from_name} 建议：{suggestion}'
                url = "https://usp.ep-zl.com/wechat/download/suggestions.csv"
                success, result = await wechat_service.send_link_message_to_user(user_name, title, description, url)
                if success:
                    logger.info(f'成功发送通知给用户 {user_name}')
                    return {"user_name": user_name, "status": "success"}
                else:
                    err_code = result.get('errcode', 0)
                    if err_code in (45047, 45015):
                        err_map = {
                            45047: f"客服接口下行条数超过上限,请主动联系 {user_name} 处理",
                            45015: f"回复时间超过限制,请主动联系 {user_name} 处理"
                        }
                        error_message = err_map.get(err_code, "未知错误")
                    else:
                        error_message = result.get("errmsg", '未知错误')
                    logger.warning(f'发送通知给用户 {user_name} 失败: {error_message}')
                    return {"user_name": user_name, "status": "failed", "error": error_message}
            except Exception as e:
                logger.error(f'发送通知给用户 {user_name} 异常: {e}')
                return {"user_name": user_name, "status": "failed", "error": str(e)}
        
        user_id = from_user_name      
        token, refresh_token = auth_service.get_wechat_user_token(user_id)
        users = await PermissionService.get_user_list(None, token)
        print(users)
        user_list = []
        for user in users:
            if user['name'] in settings.SUGGESTIONS_NOTIFICATION_USERS:
                user_list.append(user['id'])

        notification_tasks = [send_notification_to_user(user_name) for user_name in user_list]
        notification_results = await asyncio.gather(*notification_tasks)
        logger.info(f'通知发送结果: {notification_results}')
        
        reply_xml = build_reply_text(from_user_name, to_user_name, f'您的建议或者意见已收到：{suggestion}')
        return Response(content=reply_xml, media_type="text/xml")
    else:
        log_operation(
            timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
            client_ip='127.0.0.1',
            method='POST',
            path='',
            status_code=200,
            processing_time=0.0,
            operator=generate_wechat_username(from_user_name),
            summary='发送文本数据',
        )

        welcome_message = """👋 Hi! 您好！

        ✅菜单栏功能：
        ▫️【我要摇人】👉 提问、提单摇人
        ▫️【系统任务】👉 处理工单
        ▫️【后台管理】👉 查看工单&项目情况

        💡温馨提示：
        为及时收到工单进度通知，推荐您：
        🔹 置顶本服务号
        🔹 关闭消息免打扰

        📝设置路径：
        点击右上角 → 再点击右上角「…」→【置顶服务号】
        点击右上角 → 再点击右上角「…」→【设置】→ 关闭【消息免打扰】
        """
    
    reply_xml = build_reply_text(from_user_name, to_user_name, welcome_message)
    return Response(content=reply_xml, media_type="text/xml")


async def handle_event_message(message: dict):
    event_type = message.get('Event')
    from_user_name = message.get('FromUserName')
    
    if event_type == 'subscribe':
        return await handle_subscribe_event(message)
    
    elif event_type == 'unsubscribe':
        return await handle_unsubscribe_event(message)
    
    elif event_type == 'CLICK':
        return await handle_menu_click_event(message)
    
    elif event_type == 'VIEW':
        log_operation(
            timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
            client_ip='127.0.0.1',
            method='POST',
            path='',
            status_code=200,
            processing_time=0.0,
            operator=generate_wechat_username(from_user_name),
            summary=f'点击菜单（{parts[-1] if (parts := [p for p in message.get("EventKey", "").split("/") if p]) else ""}）',
        )
        logger.info(f"用户 {from_user_name} 点击了View类型菜单，EventKey: {message.get('EventKey')}")
        return Response(content='', media_type="text/xml")
    
    return Response(content='', media_type="text/xml")


@router.post("/login")
async def wechat_login(openid: str = Body(..., description="微信用户openid")):
    try:
        token, refresh_token = auth_service.get_wechat_user_token(openid)
        
        if not token:
            registered = auth_service.register_wechat_user(openid)
            if registered:
                token, refresh_token = auth_service.get_wechat_user_token(openid)
        
        if token:
            return {"token": token, "refresh_token": refresh_token}
        else:
            raise HTTPException(status_code=401, detail="登录失败，无法获取token")
    except Exception as e:
        logger.error(f"微信用户登录失败: {e}")
        raise HTTPException(status_code=500, detail="登录过程中发生错误")


@router.get("/permissions")
async def get_user_permissions(openid: str = Query(..., description="微信用户openid")):
    try:
        permissions = auth_service.get_user_permissions(openid)
        if permissions:
            return permissions
        else:
            raise HTTPException(status_code=404, detail="获取权限失败")
    except Exception as e:
        logger.error(f"获取用户权限失败: {e}")
        raise HTTPException(status_code=500, detail="获取权限过程中发生错误")


@router.get("/check-subscription")
async def check_user_subscription(username: str = Query(..., description="用户username")):
    """检查用户是否关注了公众号。

    通过 username 查找用户 openid，再调用微信 /cgi-bin/user/info 接口获取订阅状态，
    subscribe=1 表示已关注，subscribe=0 表示未关注。
    """
    try:
        user_detail = user_service.get_user_detail(username)
        if not user_detail:
            raise HTTPException(status_code=404, detail="用户不存在")

        openid = user_detail['id']
        user_info = await wechat_service.get_user_info(openid)

        if user_info is None:
            raise HTTPException(status_code=500, detail="获取用户信息失败，请稍后重试")

        if 'errcode' in user_info:
            raise HTTPException(
                status_code=400,
                detail=f"微信API错误: {user_info.get('errmsg', '未知错误')}"
            )

        subscribed = user_info.get('subscribe', 0) == 1

        return {
            "openid": openid,
            "subscribed": subscribed,
            "subscribe": user_info.get('subscribe', 0),
            "nickname": user_info.get('nickname', ''),
            "headimgurl": user_info.get('headimgurl', ''),
            "sex": user_info.get('sex', 0),
            "city": user_info.get('city', ''),
            "province": user_info.get('province', ''),
            "country": user_info.get('country', ''),
            "language": user_info.get('language', ''),
            "subscribe_time": user_info.get('subscribe_time', 0),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"检查用户订阅状态失败: {e}")
        raise HTTPException(status_code=500, detail="检查用户订阅状态过程中发生错误")


@router.post("/batch-user-info")
async def batch_get_user_info(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """批量获取用户基本信息（管理后台「其他」页用户统计卡片用）。

    鉴权走用户 JWT（Authorization: Bearer <token>），与前端 createRequest 默认携带的
    Authorization 头一致，无需额外 X-API-Key。

    默认不传请求体时，后端自动查询 users 表获取全部用户 openid（users.id 即微信
    openid），再调用微信 /cgi-bin/user/info/batchget 拉取每个用户的订阅状态等信息。

    如需只查指定用户，可传请求体：
    1. { "user_list": [{"openid": "xxx", "lang": "zh_CN"}, ...] }  # 微信原生结构
    2. { "openids": ["xxx", "yyy"], "lang": "zh_CN" }              # 简化形式，lang 可省略默认 zh_CN

    返回 {"user_info_list": [...], "total": N}，每项含 subscribe/openid/
    subscribe_time/unionid/remark/tagid_list/subscribe_scene 等字段
    （nickname/sex/city 等字段微信已不再提供）。
    """
    # 解析请求体（可选）：未提供或为空时，落到下面查全部用户
    final_user_list: List[Dict] = []
    try:
        body = await request.json()
    except Exception:
        body = None

    if body:
        user_list = body.get('user_list')
        openids = body.get('openids')

        if isinstance(user_list, list) and user_list:
            for item in user_list:
                if not isinstance(item, dict) or not item.get('openid'):
                    raise HTTPException(status_code=400, detail="user_list 中每项必须包含 openid")
                entry = {'openid': str(item['openid'])}
                if item.get('lang'):
                    entry['lang'] = item['lang']
                final_user_list.append(entry)
        elif isinstance(openids, list) and openids:
            lang = body.get('lang') or 'zh_CN'
            final_user_list = [{'openid': str(oid), 'lang': lang} for oid in openids if oid]
            if not final_user_list:
                raise HTTPException(status_code=400, detail="openids 列表不能为空")
        # 两者都未提供 → 落到下面查全部用户

    # 未指定用户列表时，查 users 表获取全部用户 openid（id 即 openid）
    # 过滤掉 id 以 'user_' 开头的虚拟用户（如 user_admin），这些并非真实微信 openid
    if not final_user_list:
        db = db_manager.get_db()
        try:
            rows = db.query(UserDB.id).filter(~UserDB.id.like('user_%')).all()
        finally:
            db.close()
        final_user_list = [{'openid': row[0], 'lang': 'zh_CN'} for row in rows if row[0]]

        if not final_user_list:
            return {"success": True, "user_info_list": [], "total": 0}

    logger.info(f"批量获取用户信息，共 {len(final_user_list)} 个 openid")

    try:
        result = await wechat_service.batch_get_user_info(final_user_list)

        if result is None:
            raise HTTPException(status_code=500, detail="批量获取用户信息失败，请稍后重试")

        if 'errcode' in result and 'user_info_list' not in result:
            raise HTTPException(
                status_code=400,
                detail=f"微信API错误: {result.get('errmsg', '未知错误')} (errcode={result.get('errcode')})"
            )

        return {
            "success": True,
            "user_info_list": result.get('user_info_list', []),
            "total": len(result.get('user_info_list', [])),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量获取用户信息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"批量获取用户信息过程中发生错误: {str(e)}")


@router.post("/user-summary")
async def get_user_summary(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """获取用户增减数据（管理后台「其他」页用户统计卡片用）。

    鉴权走用户 JWT（Authorization: Bearer <token>），与前端 createRequest 默认携带的
    Authorization 头一致，无需额外 X-API-Key。

    请求体：
    { "begin_date": "2026-08-01", "end_date": "2026-08-07" }
    日期格式 yyyy-MM-dd。微信限制单次查询最大跨度7天，本接口自动分批合并结果，
    调用方传任意跨度均可（跨周会自动拆成多段调用并聚合 list）。

    返回 {"success": true, "list": [...], "total": N}，每项含
    ref_date/user_source/new_user/cancel_user。其中 user_source 渠道含义：
    0=其他合计,1=公众号搜索,17=名片分享,30=扫描二维码,57=文章内账号名称,
    100=微信广告,161=他人转载,149=小程序关注,200=视频号,201=直播。
    """
    try:
        body = await request.json()
    except Exception:
        body = None

    begin_date = (body or {}).get('begin_date')
    end_date = (body or {}).get('end_date')

    if not begin_date or not end_date:
        raise HTTPException(status_code=400, detail="需提供 begin_date 和 end_date (yyyy-MM-dd)")

    try:
        result = await wechat_service.get_user_summary(begin_date, end_date)

        if result is None:
            raise HTTPException(status_code=500, detail="获取用户增减数据失败，请稍后重试")

        if 'list' not in result:
            raise HTTPException(
                status_code=400,
                detail=f"微信API错误: {result.get('errmsg', '未知错误')} (errcode={result.get('errcode')})"
            )

        return {
            "success": True,
            "list": result.get('list', []),
            "total": len(result.get('list', [])),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户增减数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取用户增减数据过程中发生错误: {str(e)}")


@router.get("/callback")
async def wechat_callback(
    request: Request,
    code: Optional[str] = Query(None, description="微信授权code"),
    state: Optional[str] = Query(None, description="微信授权state")
):
    try:
        logger.info(f"收到微信授权回调请求")
        logger.info(f"请求参数: code={code}, state={state}")
        logger.info(f"请求完整URL: {str(request.url)}")
        
        if not code:
            logger.error("微信授权回调缺少code参数")
            return templates.TemplateResponse("error.html", {"request": request, "error": "missing_code"})
        
        logger.info("开始使用code兑换openid")
        app_id = settings.WECHAT_CONFIG['app_id']
        app_secret = settings.WECHAT_CONFIG['app_secret']
        
        auth_result = await wechat_service.get_openid(code, app_id, app_secret)
        
        if not auth_result or 'openid' not in auth_result:
            logger.error(f"使用code兑换openid失败: {auth_result}")
            return templates.TemplateResponse("error.html", {"request": request, "error": "invalid_code"})
        
        openid = auth_result['openid']
        logger.info(f"成功获取openid: {openid}")

        # 网页授权 access_token（sns/oauth2/access_token 返回，区别于基础 access_token），
        # 用于后续 sns/userinfo 拉取昵称/头像；此处令牌最新，先缓存下来。
        sns_access_token = auth_result.get('access_token')

        logger.info("开始创建用户登录态")
        
        token_result = auth_service.get_wechat_user_token(openid)
        
        if token_result is None:
            token = None
            refresh_token = None
        else:
            token, refresh_token = token_result
        
        if not token:
            logger.info(f"用户不存在，开始注册新用户: {openid}")
            
            if auth_service.register_wechat_user(openid):
                token_result = auth_service.get_wechat_user_token(openid)
                
                if token_result is None:
                    token = None
                    refresh_token = None
                else:
                    token, refresh_token = token_result
                
                if not token:
                    logger.error(f"用户注册成功，但获取token失败: {openid}")
                    return templates.TemplateResponse("error.html", {"request": request, "error": "auth_failed"})
            else:
                logger.error(f"用户注册失败: {openid}")
                return templates.TemplateResponse("error.html", {"request": request, "error": "register_failed"})
        
        logger.info(f"成功获取用户token: {token}")

        # 用户已落库，best-effort 拉取并保存微信昵称/头像（失败不影响登录）
        await fetch_and_persist_wechat_user_profile(openid, sns_access_token)

        logger.info("开始检查用户权限")
        permissions = auth_service.get_user_permissions(openid)
        
        if not permissions or not permissions.get('permissions'):
            logger.error(f"用户 {openid} 无权限访问")
            return templates.TemplateResponse("error.html", {"request": request, "error": "no_permission", "error_details": "无权限访问，请联系管理员"})
        
        logger.info(f"用户 {openid} 权限检查通过")
        
        scheme = request.url.scheme
        netloc = request.url.netloc
        
        # state 优先按 base64url 完整地址解码（含部署前缀，避免 /p/app 丢失）；
        # 无法解码则兼容旧的 '0'→'/' 路径格式，用回调域名兜底重拼。
        target_url = resolve_callback_target(state, scheme, netloc)
        logger.info(f"还原的前端回跳地址: {target_url}")
        frontend_url = f"{target_url}?token={token}&refresh_token={refresh_token}"
        logger.info(f"准备重定向到前端业务页面: {frontend_url}")
        print(f"【重定向地址】{frontend_url}")
        
        return RedirectResponse(url=frontend_url)
        
    except Exception as e:
        logger.error(f"处理微信授权回调时发生异常: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {"request": request, "error": "system_error", "error_details": str(e)})


@router.get("/get-openid")
async def get_wechat_openid(code: str = Query(..., description="微信授权code")):
    try:
        logger.info(f"收到获取openid请求，code: {code}")
        
        app_id = settings.WECHAT_CONFIG['app_id']
        app_secret = settings.WECHAT_CONFIG['app_secret']
        
        response = await wechat_service.get_openid(code, app_id, app_secret)
        
        if response and 'openid' in response:
            logger.info(f"成功获取openid: {response['openid']}")
            return {"success": True, "openid": response['openid']}
        else:
            error_msg = response.get('errmsg', '获取openid失败') if response else '获取openid失败'
            logger.error(f"获取openid失败: {error_msg}")
            return {"success": False, "error": error_msg}
    except Exception as e:
        logger.error(f"处理获取openid请求时发生异常: {e}", exc_info=True)
        return {"success": False, "error": f"系统错误: {str(e)}"}


async def handle_subscribe_event(message: dict):
    from_user_name = message.get('FromUserName')
    to_user_name = message.get('ToUserName')
    
    logger.info(f"用户 {from_user_name} 关注")
    
    log_operation(
        timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
        client_ip='127.0.0.1',
        method='POST',
        path='',
        status_code=200,
        processing_time=0.0,
        operator=generate_wechat_username(from_user_name),
        summary='用户关注',
    )
    
    auth_service.register_wechat_user(from_user_name)
    
    welcome_message = """👋 欢迎关注我们！

    🔗 请点击链接完成个人信息录入

    ✅注册完成后菜单栏功能：
    ▫️【我要摇人】👉 提问、提单摇人
    ▫️【系统任务】👉 处理工单
    ▫️【后台管理】👉 查看工单&项目情况

    💡温馨提示：
    为及时收到工单进度通知，推荐您：
    🔹 置顶本服务号
    🔹 关闭消息免打扰

    📝设置路径：
    点击右上角 → 再点击右上角「…」→【置顶服务号】
    点击右上角 → 再点击右上角「…」→【设置】→ 关闭【消息免打扰】
    """
    reply_xml = build_reply_text(from_user_name, to_user_name, welcome_message)
    
    # 追加推送个人中心分享卡片（news 类型图文消息）
    try:
        profile_url = f"{settings.FRONTEND_BASE_URL}/admin/profile"
        share_img = f"{settings.FRONTEND_BASE_URL}/share-thumb.png"
        wechat_service.send_news_message_to_user(
            open_id=from_user_name,
            title="设置你的个人信息",
            description="设置你的真实姓名，公司，部门等， 为你开放全部功能！",
            url=profile_url,
            picurl=share_img,
        )
    except Exception as e:
        logger.error(f"推送个人中心卡片失败: {e}")
    
    return Response(content=reply_xml, media_type="text/xml")


async def handle_unsubscribe_event(message: dict):
    from_user_name = message.get('FromUserName')
    
    logger.info(f"用户 {from_user_name} 取消关注")
    
    log_operation(
        timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
        client_ip='127.0.0.1',
        method='POST',
        path='',
        status_code=200,
        processing_time=0.0,
        operator=generate_wechat_username(from_user_name),
        summary='用户取消关注'
    )
    
    try:
        auth_service.handle_user_unsubscribe(from_user_name)
    except Exception as e:
        logger.error(f"处理用户取消关注时发生异常: {e}")
    
    return Response(content='', media_type="text/xml")


async def handle_menu_click_event(message: dict):
    from_user_name = message.get('FromUserName')
    to_user_name = message.get('ToUserName')
    event_key = message.get('EventKey')
    
    logger.info(f"用户 {from_user_name} 点击了菜单: {event_key}")
    
    log_operation(
        timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
        client_ip='127.0.0.1',
        method='POST',
        path='',
        status_code=200,
        processing_time=0.0,
        operator=generate_wechat_username(from_user_name),
        summary=f'点击菜单（{parts[-1] if (parts := [p for p in event_key.split("/") if p]) else ""}）',
    )
    
    if event_key == 'PROJECT_OVERVIEW_LIST':
        try:
            permissions_data = auth_service.get_user_permissions(from_user_name)
            
            if permissions_data:
                project_permissions = permissions_data.get('projectPermissions', {})
                articles = data_service.build_project_articles(project_permissions)
                
                if articles:
                    reply_xml = build_reply_news(from_user_name, to_user_name, articles)
                    return Response(content=reply_xml, media_type="text/xml")
                else:
                    reply_content = "暂无可访问的项目权限"
            else:
                reply_content = "获取权限信息失败"
        except Exception as e:
            logger.error(f"获取项目概览时发生错误: {e}", exc_info=True)
            reply_content = "获取项目概览失败，请稍后再试。"
    
    elif event_key == 'PROJECT_SUMMARY_IMAGE':
        articles = [{
            'title': 'AGV系统数据指标报表',
            'description': '查看实时监控与性能分析数据',
            'picurl': 'https://via.placeholder.com/300x200?text=AGV+Data+Report',
            'url': 'http://120.26.23.199:8003/agv_data_report.html'
        }]
        reply_xml = build_reply_news(from_user_name, to_user_name, articles)
        return Response(content=reply_xml, media_type="text/xml")
    
    elif event_key == 'CONTACT_US':
        reply_content = "点击左侧小键盘\r\n输入您的问题或建议。\r\n我们会尽快回复。"
    
    else:
        reply_content = f"未知的菜单操作: {event_key}"
    
    reply_xml = build_reply_text(from_user_name, to_user_name, reply_content)
    return Response(content=reply_xml, media_type="text/xml")


@router.get("/config/js-sdk-config")
async def get_js_sdk_config(url: str = Query(..., description="当前页面URL，用于生成签名")):
    try:
        logger.info(f"获取JS-SDK配置，URL: {url}")
        
        config = await wechat_service.get_js_sdk_config(url)
        
        if config:
            logger.info("JS-SDK配置获取成功")
            return config
        else:
            logger.error("获取JS-SDK配置失败")
            raise HTTPException(status_code=500, detail="获取微信JS-SDK配置失败")
    except Exception as e:
        logger.error(f"获取JS-SDK配置时发生异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"系统错误: {str(e)}")


async def validate_and_prepare_import_data(data: dict) -> dict:
    project = data.get("project")
    indicator = data.get("indicator")
    data_content = data.get("content")
    
    if not project:
        logger.warning("缺少必填参数: project")
        raise ValueError("缺少必填参数: project")
        
    if not indicator:
        logger.warning("缺少必填参数: indicator")
        raise ValueError("缺少必填参数: indicator")
        
    if not data_content:
        logger.warning("缺少必填参数: content")
        raise ValueError("缺少必填参数: content")
        
    if not isinstance(data_content, list):
        logger.warning(f"data_content必须是列表类型，当前类型: {type(data_content).__name__}")
        raise ValueError("数据必须是列表类型")
    
    message_type = data.get("message_type", "realtime_data")
    collection_time = data.get("collection_time", datetime.now().isoformat())
    
    return {
        "message_type": message_type,
        "project": project,
        "indicator": indicator,
        "content": data_content,
        "collection_time": collection_time
    }


@router.post("/import-data")
async def import_data(request: Request, data: dict = Body(...), credentials: Optional = admin_auth):
    try:
        logger.info(f"收到文本框导入请求，数据: {data}")
        
        insert_data = await validate_and_prepare_import_data(data)
        
        status_code, api_response = await data_service.insert_project_data(insert_data)
        
        if status_code is None or api_response is None:
            logger.error(f"数据插入失败，状态码: {status_code}, 响应: {api_response}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": "数据插入失败，服务不可用", "error": "数据插入失败，服务不可用"}
            )
            
        if status_code != 200:
            logger.error(f"数据插入失败，状态码: {status_code}, 响应: {api_response}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": f"数据插入失败，状态码: {status_code}", "error": f"数据插入失败，状态码: {status_code}"}
            )
        
        response_data = {
            "success": True,
            "message": "数据导入成功",
            "content": insert_data,
            "api_status": status_code,
            "api_response": api_response
        }
        
        return JSONResponse(content=response_data)
        
    except ValueError as e:
        logger.error(f"文本框导入参数验证失败: {e}")
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": str(e), "error": str(e)}
        )
    except Exception as e:
        logger.error(f"文本框导入处理异常: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "服务器内部错误", "error": str(e)}
        )