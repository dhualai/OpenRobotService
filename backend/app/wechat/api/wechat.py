import os
import json
import shutil
import traceback
import uuid
from datetime import datetime
import logging
import csv
import asyncio
from difflib import SequenceMatcher
from typing import Optional

from fastapi import APIRouter, Request, Query, HTTPException, Body, UploadFile, File, Form
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
from app.wechat.services.permission_service import PermissionService
from app.wechat.api.match_report import parse_daily_report
from app.modules.admin.services.daily_report_service import daily_report_service
from app.wechat.api.dependencies import admin_auth

templates = Jinja2Templates(directory="app/wechat/templates")
logger = logging.getLogger(__name__)

router = APIRouter(tags=["微信接口"])

user_states = {}


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
    from_user_name = message.get('FromUserName')
    to_user_name = message.get('ToUserName')
    reply_xml = build_reply_text(from_user_name, to_user_name, 'HI! https://usp.ep-zl.com/p/app/call')
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
        
        logger.info("开始检查用户权限")
        permissions = auth_service.get_user_permissions(openid)
        
        if not permissions or not permissions.get('permissions'):
            logger.error(f"用户 {openid} 无权限访问")
            return templates.TemplateResponse("error.html", {"request": request, "error": "no_permission", "error_details": "无权限访问，请联系管理员"})
        
        logger.info(f"用户 {openid} 权限检查通过")
        
        scheme = request.url.scheme
        netloc = request.url.netloc
        
        processed_path = state.replace('0', '/')
        logger.info(f"处理后的path参数: {processed_path}")
        frontend_url = f"{scheme}://{netloc}{processed_path}?token={token}&refresh_token={refresh_token}"
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
    
    welcome_message = "👋 欢迎关注我们！\n\n请输入您的姓名，以便我们为您提供个性化服务。\n\n例如：\r\r@张三"
    reply_xml = build_reply_text(from_user_name, to_user_name, welcome_message)
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