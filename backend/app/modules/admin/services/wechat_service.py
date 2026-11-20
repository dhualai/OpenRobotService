import requests
import json
from typing import Dict, Any, Optional
from datetime import datetime
from app.modules.admin.utils_das.logging import get_logger
import asyncio
import aiohttp

logger = get_logger()

class WeChatService:
    
    SEND_LINK_MESSAGE_URL = "https://usp.ep-zl.com/api/wechat/send_link_message"
    
    @classmethod
    def send_link_message(cls, open_id: str, title: str, description: str, url: str) -> Dict[str, Any]:
        try:
            logger.info(f"准备发送微信链接消息给用户: {open_id}")
            
            payload = {
                "open_id": open_id,
                "title": title,
                "description": description,
                "url": url
            }
            
            response = requests.post(
                cls.SEND_LINK_MESSAGE_URL,
                headers={"Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=30
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            if result.get("code") == 200:
                logger.info(f"微信链接消息发送成功: {result}")
                return result
            else:
                logger.error(f"微信链接消息发送失败: {result}")
                return {
                    "status": "error",
                    "message": f"发送微信链接消息失败: {result.get('message', '未知错误')}"
                }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"发送微信链接消息失败: {str(e)}")
            return {
                "status": "error",
                "message": f"发送微信链接消息失败: {str(e)}"
            }
        except Exception as e:
            logger.error(f"发送微信链接消息时发生未知错误: {str(e)}")
            return {
                "status": "error",
                "message": f"发送微信链接消息时发生未知错误: {str(e)}"
            }
    
    @classmethod
    async def send_link_message_async(cls, open_id: str, title: str, description: str, url: str) -> Dict[str, Any]:
        try:
            logger.info(f"准备异步发送微信链接消息给用户: {open_id}")
            
            payload = {
                "open_id": open_id,
                "title": title,
                "description": description,
                "url": url
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    cls.SEND_LINK_MESSAGE_URL,
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(payload),
                    timeout=30
                ) as response:
                    response.raise_for_status()
                    
                    result = await response.json()
                    
                    if result.get("code") == 200:
                        logger.info(f"微信链接消息异步发送成功: {result}")
                        return result
                    else:
                        logger.error(f"微信链接消息异步发送失败: {result}")
                        return {
                            "status": "error",
                            "message": f"异步发送微信链接消息失败: {result.get('message', '未知错误')}"
                        }
                    
        except aiohttp.ClientError as e:
            logger.error(f"异步发送微信链接消息失败: {str(e)}")
            return {
                "status": "error",
                "message": f"异步发送微信链接消息失败: {str(e)}"
            }
        except Exception as e:
            logger.error(f"异步发送微信链接消息时发生未知错误: {str(e)}")
            return {
                "status": "error",
                "message": f"异步发送微信链接消息时发生未知错误: {str(e)}"
            }
    
    @classmethod
    def send_notification(cls, open_id: str, content: str, url: str = "https://usp.ep-zl.com") -> Dict[str, Any]:
        title = "系统通知"
        description = content
        
        return cls.send_link_message(open_id, title, description, url)
    
    @classmethod
    async def send_notification_async(cls, open_id: str, content: str, url: str = "https://usp.ep-zl.com") -> Dict[str, Any]:
        title = "系统通知"
        description = content
        
        return await cls.send_link_message_async(open_id, title, description, url)