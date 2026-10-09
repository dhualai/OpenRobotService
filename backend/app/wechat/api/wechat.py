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
from app.wechat.services.user_info_snapshot import run_user_info_snapshot
from app.wechat.services.user_statistics_snapshot import run_user_statistics_job, run_user_statistics_job_for_range
from app.wechat.utils.qrcode import process_qrcode_content, decompress_data
from app.wechat.utils.opt_logger import log_operation
from app.services.hmac_utils import generate_password, chinese_to_pinyin, get_password_hash, verify_password
from app.services.user_service import user_service
from app.core.database import db_manager, UserDB
from app.models.user_info import UserInfo
from app.models.user_statistics import UserStatistics
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
    welcome_message = """提问请按如下菜单引导操作

✅菜单栏功能：
▫️【我要摇人】👉 提问、提单摇人
▫️【系统任务】👉 处理工单
▫️【后台管理】👉 查看工单&项目情况
"""
    reply_xml = build_reply_text(from_user_name, to_user_name, welcome_message)
    return Response(content=reply_xml, media_type="text/xml")


async def handle_event_message(message: dict):
    event_type = message.get('Event')
    from_user_name = message.get('FromUserName')
    event_key = message.get('EventKey', '')

    if event_type == 'subscribe':
        return await handle_subscribe_event(message)
    elif event_type == 'SCAN':
        return await handle_scan_event(message)
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
            summary=f'点击菜单（{parts[-1] if (parts := [p for p in event_key.split("/") if p]) else ""}）',
        )
        logger.info(f"用户 {from_user_name} 点击了View类型菜单，EventKey: {event_key}")
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

    微信登录用户（username 形如 wechat_xxx）的 users.id 即 openid，
    通过 openid 调用微信 /cgi-bin/user/info 接口获取订阅状态：
    subscribe=1 表示已关注，subscribe=0 表示未关注。

    手工创建/后台账号（username 无 wechat_ 前缀，如管理员 zhangjunlei1）不是微信用户，
    没有公众号订阅关系：直接返回 200 + is_wechat_user=false，不再把 id 当 openid
    去调微信 API（此前返回 400，前端管理端登录时反复报错并可能误弹关注提醒）。
    """
    try:
        user_detail = user_service.get_user_detail(username)

        if not user_detail:
            raise HTTPException(status_code=404, detail="用户不存在")

        # 非微信用户：无 openid 订阅关系，直接放行（subscribe=0、subscribed=false）
        if not username.startswith('wechat_'):
            return {
                "openid": "",
                "is_wechat_user": False,
                "subscribed": False,
                "subscribe": 0,
                "nickname": "",
                "headimgurl": "",
                "sex": 0,
                "city": "",
                "province": "",
                "country": "",
                "language": "",
                "subscribe_time": 0,
            }

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
            "is_wechat_user": True,
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
    begin_date: Optional[str] = Query(None, description="开始日期 yyyy-MM-dd"),
    end_date: Optional[str] = Query(None, description="结束日期 yyyy-MM-dd"),
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
    begin_date = begin_date or (body or {}).get('begin_date')
    end_date = end_date or (body or {}).get('end_date')

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


@router.post("/user-summary-db")
async def get_user_summary_from_db(
    request: Request,
    begin_date: Optional[str] = Query(None, description="开始日期 yyyy-MM-dd"),
    end_date: Optional[str] = Query(None, description="结束日期 yyyy-MM-dd"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """读取 user_statistics 表存储的用户增减数据（整点任务刷新出的昨日微信渠道明细）。

    返回结构与 /user-summary 一致：{"success": true, "list": [...], "total": N}，
    每项含 ref_date/user_source/new_user/cancel_user。表内为微信接口原样数据，
    查询跨度不限；任务每个整点都会刷新昨日数据，同一 ref_date 仅保留最新一次
    刷新的渠道明细。end_date 不能为今天或未来日期。
    """
    try:
        body = await request.json()
    except Exception:
        body = None

    begin_date = begin_date or (body or {}).get('begin_date')
    end_date = end_date or (body or {}).get('end_date')

    if not begin_date or not end_date:
        raise HTTPException(status_code=400, detail="需提供 begin_date 和 end_date (yyyy-MM-dd)")

    try:
        begin = datetime.strptime(begin_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，需为 yyyy-MM-dd")

    if begin > end:
        raise HTTPException(status_code=400, detail="begin_date 不能晚于 end_date")

    # 统计数据 T+1 落库（每日凌晨 1:00 写入昨日数据），当天及以后暂无数据
    today = datetime.now().date()
    if end >= today:
        raise HTTPException(
            status_code=400,
            detail=f"end_date 不能为今天或未来日期（统计数据 T+1 落库，最早可查到昨日；今天为 {today.strftime('%Y-%m-%d')}）",
        )

    def _query_rows() -> List[UserStatistics]:
        db = db_manager.get_db()
        try:
            return db.query(UserStatistics).filter(
                UserStatistics.ref_date >= begin,
                UserStatistics.ref_date <= end,
            ).order_by(UserStatistics.ref_date, UserStatistics.user_source).all()
        finally:
            db.close()

    try:
        rows = await asyncio.to_thread(_query_rows)

        return {
            "success": True,
            "list": [
                {
                    "ref_date": r.ref_date.strftime('%Y-%m-%d'),
                    "user_source": r.user_source,
                    "new_user": r.new_user,
                    "cancel_user": r.cancel_user,
                }
                for r in rows
            ],
            "total": len(rows),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取用户增减数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"读取用户增减数据过程中发生错误: {str(e)}")


@router.post("/batch-user-info-db")
async def get_batch_user_info_from_db(
    created_date: Optional[str] = Query(None, description="指定快照日期 yyyy-MM-dd；不传则取最新"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """读取 user_info 表最新快照并返回聚合统计（整点快照任务落库）。

    返回 {"success": true, "total": N, "real": N, "virtual": N,
    "scene_distribution": [{"scene": "...", "value": N}, ...]}。
    real/virtual 按 subscribe===1 区分；scene_distribution 仅统计已关注用户
    （subscribe===1）的 subscribe_scene 分布。数据最长滞后 1 小时。
    """
    def _query_latest() -> Optional[Dict[str, Any]]:
        db = db_manager.get_db()
        try:
            query = db.query(UserInfo)
            if created_date:
                try:
                    target_date = datetime.strptime(created_date, '%Y-%m-%d').date()
                except ValueError:
                    raise HTTPException(status_code=400, detail="created_date 日期格式错误，需为 yyyy-MM-dd")
                query = query.filter(UserInfo.created_time == target_date)

            latest = query.order_by(
                UserInfo.created_time.desc(), UserInfo.id.desc(),
            ).first()
            if not latest or not latest.user_info:
                return None
            return latest.user_info if isinstance(latest.user_info, dict) else None
        finally:
            db.close()

    try:
        payload = await asyncio.to_thread(_query_latest)

        if not payload:
            return {"success": True, "total": 0, "real": 0, "virtual": 0, "scene_distribution": []}

        items = payload.get('user_info_list', [])
        total = len(items)
        real = sum(1 for u in items if u.get('subscribe') == 1)

        scene_map: Dict[str, int] = {}
        for u in items:
            if u.get('subscribe') != 1:
                continue
            scene = str(u.get('subscribe_scene') or 'ADD_SCENE_OTHERS')
            scene_map[scene] = scene_map.get(scene, 0) + 1

        scene_distribution = sorted(
            [{"scene": s, "value": v} for s, v in scene_map.items()],
            key=lambda x: x["value"],
            reverse=True,
        )

        return {
            "success": True,
            "total": total,
            "real": real,
            "virtual": total - real,
            "scene_distribution": scene_distribution,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取用户信息快照失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"读取用户信息快照过程中发生错误: {str(e)}")


@router.post("/batch-user-info/debug-run")
async def debug_run_batch_user_info_snapshot(
    created_date: Optional[str] = Query(None, description="调试落库日期 yyyy-MM-dd；不传默认今天"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """手动触发 user_info 整点快照任务，便于线上即时调试。"""
    try:
        target_date = None
        if created_date:
            try:
                target_date = datetime.strptime(created_date, '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="created_date 日期格式错误，需为 yyyy-MM-dd")

        payload = await run_user_info_snapshot(target_date)
        if payload is None:
            return {
                "success": False,
                "message": "user_info 快照任务执行失败或无可用用户，本次未落库",
                "user_info_list": [],
                "total": 0,
                "created_date": created_date,
            }

        return {
            "success": True,
            "message": "user_info 快照任务执行成功",
            "user_info_list": payload.get("user_info_list", []),
            "total": payload.get("total", len(payload.get("user_info_list", []))),
            "created_date": created_date or datetime.now().strftime('%Y-%m-%d'),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"手动触发 user_info 快照任务失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"手动触发 user_info 快照任务失败: {str(e)}")


@router.post("/user-summary/debug-run")
async def debug_run_user_statistics_snapshot(
    begin_date: Optional[str] = Query(None, description="开始日期 yyyy-MM-dd；不传默认昨天"),
    end_date: Optional[str] = Query(None, description="结束日期 yyyy-MM-dd；不传默认昨天"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """手动触发 user_statistics 整点统计任务，便于线上即时调试。"""
    try:
        if begin_date and end_date:
            items = await run_user_statistics_job_for_range(begin_date, end_date)
        elif begin_date or end_date:
            raise HTTPException(status_code=400, detail="begin_date 和 end_date 需同时提供，格式为 yyyy-MM-dd")
        else:
            items = await run_user_statistics_job()

        if items is None:
            return {
                "success": False,
                "message": "user_statistics 统计任务执行失败，本次未落库",
                "list": [],
                "total": 0,
                "begin_date": begin_date,
                "end_date": end_date,
            }

        return {
            "success": True,
            "message": "user_statistics 统计任务执行成功",
            "list": items,
            "total": len(items),
            "begin_date": begin_date,
            "end_date": end_date,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"手动触发 user_statistics 统计任务失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"手动触发 user_statistics 统计任务失败: {str(e)}")


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


@router.get("/qrcode")
async def generate_qrcode(
    scene: str = Query(..., description="场景值 (scene_str)，1~64 字符，扫码后微信通过 EventKey 回传"),
    permanent: bool = Query(False, description="是否永久二维码 (永久码最多 10 万个)"),
    expire_seconds: int = Query(2592000, ge=1, le=2592000, description="临时码有效期 (秒)，最大 2592000"),
    as_json: bool = Query(False, description="True 时返回 JSON (含 ticket/url)，否则直接返回图片"),
    credentials: Optional = admin_auth,
):
    """生成带参数的微信公众号二维码。

    浏览器直接访问此接口即可看到二维码图片。用户扫码后微信会推送
    SCAN 或 subscribe 事件到 /api/wechat，EventKey 即此处传入的 scene 值。

    示例：
        GET /api/wechat/qrcode?scene=robot_2026&expire_seconds=604800
        GET /api/wechat/qrcode?scene=robot_perm&permanent=true

    返回：
        - 默认：image/jpeg 二维码图片
        - as_json=true: {"success": true, "ticket": "...", "url": "...", "expire_seconds": N}
    """
    if not scene or len(scene) > 64:
        raise HTTPException(status_code=400, detail="scene 不能为空且长度不能超过 64 字符")

    try:
        loop = asyncio.get_event_loop()
        ticket_result = await loop.run_in_executor(
            None,
            lambda: wechat_service.create_qrcode_ticket(
                scene_str=scene,
                is_permanent=permanent,
                expire_seconds=expire_seconds,
            )
        )

        if ticket_result is None:
            raise HTTPException(status_code=500, detail="创建二维码 ticket 失败，请稍后重试")

        if 'ticket' not in ticket_result:
            # 微信返回了错误 JSON（含 errcode）
            errcode = ticket_result.get('errcode', -1)
            errmsg = ticket_result.get('errmsg', '微信接口返回错误')
            raise HTTPException(
                status_code=400,
                detail=f"微信创建二维码失败: errcode={errcode}, errmsg={errmsg}"
            )

        ticket = ticket_result['ticket']

        # 只调试模式下返回 JSON
        if as_json:
            return {
                "success": True,
                "ticket": ticket,
                "url": ticket_result.get('url', ''),
                "expire_seconds": ticket_result.get('expire_seconds'),
                "action_name": 'QR_LIMIT_STR_SCENE' if permanent else 'QR_STR_SCENE',
                "scene_str": scene,
            }

        # 正常模式：取图片字节返回
        image_bytes = await loop.run_in_executor(
            None,
            lambda: wechat_service.get_qrcode_image_bytes(ticket)
        )

        if image_bytes is None:
            raise HTTPException(status_code=502, detail="用 ticket 换取二维码图片失败")

        return Response(content=image_bytes, media_type="image/jpeg")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成二维码异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成二维码过程中发生错误: {str(e)}")


async def handle_subscribe_event(message: dict):
    from_user_name = message.get('FromUserName')
    to_user_name = message.get('ToUserName')
    event_key = message.get('EventKey', '')
    is_scan_follow = bool(event_key)  # 通过扫码关注 → EventKey 非空

    logger.info(f"用户 {from_user_name} 关注 (扫码={is_scan_follow}, EventKey={event_key})")
    log_operation(
        timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
        client_ip='127.0.0.1',
        method='POST',
        path='',
        status_code=200,
        processing_time=0.0,
        operator=generate_wechat_username(from_user_name),
        summary=f'用户关注 (扫码={is_scan_follow})',
    )
    auth_service.register_wechat_user(from_user_name)
    welcome_message = """👋 欢迎关注我们！

🔗 请点击链接完成个人信息录入以及设置（修改）USP账户密码

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

    # 扫码关注：额外推一张跳 /app/call 的图文卡片，让用户直达扫码来源页
    if is_scan_follow:
        _send_scan_redirect_card(from_user_name, event_key)
    else:
        # 普通关注：推个人中心卡片
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


def _send_scan_redirect_card(openid: str, scene_str: str):
    """扫码后推送图文卡片，引导用户跳转。

    支持每个二维码独立配置 redirect_url / name / description / picurl：
    - wechat_qrcodes 表有记录且字段非空 → 用配置值
    - 没记录或字段为空 → 用默认 /call 拼接 scene+openid

    scene_str 归一化在入口统一做：未关注用户扫码关注时微信推 subscribe 事件，
    EventKey 形如 `qrscene_<scene>`（微信自动加前缀）；已关注用户扫码走 SCAN
    事件，EventKey 才是纯 `<scene>`。两类事件都汇聚到本函数，不剥前缀的话
    按 id 查库会炸、URL 也会带着 `qrscene_` 往外发，前端弹窗链路整体失效。
    """
    # 剥 subscribe 事件的 qrscene_ 前缀；剥完为空说明没有有效场景值，无从跳转
    if scene_str.startswith('qrscene_'):
        scene_str = scene_str[len('qrscene_'):]
    if not scene_str:
        logger.info(f'扫码事件无有效场景值，跳过跳转卡片: openid={openid}')
        return
    try:
        from urllib.parse import urlencode

        # ── 1. 查数据库：先用 scene_str 精确匹配，再 fallback 按主键 id ──
        qr_cfg = None
        try:
            from app.core.database import db_manager
            from app.models.wechat_qrcode import WechatQrcode as _W
            db = db_manager.get_db()
            # 优先按 scene_str（微信回调原样回传的 EventKey）精确匹配——这是最稳的方式
            qr_cfg = db.query(_W).filter(_W.scene_str == scene_str).first()
            if not qr_cfg:
                # 再按主键 id 查（兼容 scene_str = str(id) 的老习惯）
                try:
                    qid = int(scene_str)
                    qr_cfg = db.query(_W).filter(_W.id == qid).first()
                except (ValueError, TypeError):
                    pass  # 非数字，跳过
            db.close()
        except Exception:
            qr_cfg = None  # 没建表 / 没迁移过，静默回退默认

        # ── 2. 拼跳转 URL ──
        # 录入信息行（project_code 非空）在 entering（ticket 已生成、信息未确认）时，
        # 扫码先去录入信息详情页核对——页面上有「确认信息」按钮（录入信息行确认即发布，
        # 直接 entering → published，2026-09-30 用户口径）；
        # 确认过之后按常规走（redirect_url 优先，否则默认落地页）。
        # 'entering' 即 QrcodeStatus.ENTERING（纯字符串常量，此处不额外引入）
        # 路径里一律不硬拼 /app：FRONTEND_BASE_URL 部署值已带 /app 后缀
        #（.../t/app、.../p/app），硬拼会出现 .../app/app/... 重复。
        is_info_entering = bool(
            qr_cfg and qr_cfg.status == 'entering' and qr_cfg.project_code
        )
        base_url = None
        if is_info_entering:
            # id 必须占路径：微信 OAuth 回跳会丢 query，带 id 的 path 才稳
            #（前端 QrcodeManage / InfoEntry 契约）；scene/openid 仍以 query 兜底
            base_url = f"{settings.FRONTEND_BASE_URL}/admin/info-entry/{qr_cfg.id}"
        elif qr_cfg and qr_cfg.redirect_url:
            # 二维码配置了 redirect_url 就用它（可带 query，也可不带）
            base_url = qr_cfg.redirect_url
        elif qr_cfg:
            # 默认：录入信息详情页（其余状态扫码先进入这个页面）
            base_url = f"{settings.FRONTEND_BASE_URL}/admin/info-entry"

        if base_url:
            # 如果配置的 URL 没有 ? 就附加 scene + openid 参数
            sep = '&' if '?' in base_url else '?'
            redirect_url = f"{base_url}{sep}{urlencode({'scene': scene_str, 'openid': openid})}"
        else:
            # 无 DB 记录（可能是手动发的 EventKey 或老码），兜底走 /call
            redirect_url = f"{settings.FRONTEND_BASE_URL}/call?{urlencode({'scene': scene_str, 'openid': openid})}"

        # ── 3. 卡片标题/描述/图片 ──
        # 分支核心：先拦截 deprecated → 再按「是否录入项目信息」分流。
        # 自动生成的码 name 形如 test-1、test-2 可读性差，未录入时直接用固定文案引导用户。
        picurl = qr_cfg.qrcode_image_url if qr_cfg and qr_cfg.qrcode_image_url else ''

        if qr_cfg and qr_cfg.status == 'deprecated':
            # 已弃用：扫码直接告知停用，不再引导跳转
            title = "二维码已停用"
            description = "此二维码已停止使用，请联系管理员"
        elif qr_cfg and qr_cfg.project_code:
            # 已录入：标题取「客户名称 - 车型」，描述放项目名 / 地点
            title_parts = [p for p in [qr_cfg.customer_name, qr_cfg.vehicle_model] if p]
            title = " - ".join(title_parts) if title_parts else (qr_cfg.project_name or "扫码跳转")
            desc_parts = []
            if qr_cfg.project_name:
                desc_parts.append(f"项目：{qr_cfg.project_name}")
            if qr_cfg.project_location:
                desc_parts.append(f"地点：{qr_cfg.project_location}")
            desc_parts.append("点击前往咨询页面")
            description = "\n".join(desc_parts) or "项目信息已登记"
        elif qr_cfg:
            # 有 DB 记录但未录入信息（init / entering 空白码，自动 name 可读性差）
            title = "请完成信息录入"
            description = "请点击进入，完成项目名称、客户名称、车型等信息"
        else:
            # 无 DB 记录（老码或手动 EventKey）——兜底
            title = "点击继续"
            description = "你扫了一个带参数的二维码，点击前往对应页面"

        logger.info(f'推送扫码跳转卡片: openid={openid}, scene={scene_str}, url={redirect_url}')

        wechat_service.send_news_message_to_user(
            open_id=openid,
            title=title,
            description=description,
            url=redirect_url,
            picurl=picurl,
        )
    except Exception as e:
        logger.error(f"推送扫码跳转卡片失败: openid={openid}, scene={scene_str}, error={e}")


async def handle_scan_event(message: dict):
    """已关注用户扫码 → 微信推送 SCAN 事件，EventKey 即场景值 (scene_str)。"""
    from_user_name = message.get('FromUserName')
    to_user_name = message.get('ToUserName')
    event_key = message.get('EventKey', '')

    logger.info(f"已关注用户 {from_user_name} 扫码, EventKey={event_key}")
    log_operation(
        timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
        client_ip='127.0.0.1',
        method='POST',
        path='',
        status_code=200,
        processing_time=0.0,
        operator=generate_wechat_username(from_user_name),
        summary=f'扫码 (EventKey={event_key})',
    )

    if event_key:
        _send_scan_redirect_card(from_user_name, event_key)

    # SCAN 事件被动回复空串即可（主操作是上面的客服消息）
    return Response(content='', media_type="text/xml")


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
