import requests
import logging
from typing import Dict, Optional, List
from app.core.config import settings
from app.modules.wechat.utils.crypto import generate_wechat_username, generate_wechat_user_password

logger = logging.getLogger(__name__)


class AuthService:

    def __init__(self):
        pass

    def get_wechat_user_token(self, openid: str) -> Optional[str]:
        try:
            username = generate_wechat_username(openid)
            password = generate_wechat_user_password(openid)

            login_data = {
                "username": username,
                "password": password
            }

            login_response = requests.post(f"{settings.AUTH_SERVICE_URL}/AAS/auth/login", json=login_data, timeout=5)

            if login_response.status_code == 200:
                login_result = login_response.json()
                token = login_result.get("access_token")
                refresh_token = login_result.get("refresh_token")
                if token:
                    return token, refresh_token
        except Exception as e:
            logger.error(f"获取微信用户token失败: {e}")

        return None

    def register_wechat_user(self, openid: str) -> bool:
        try:
            username = generate_wechat_username(openid)
            password = generate_wechat_user_password(openid)

            user_data = {
                "user_id": openid,
                "username": username,
                "password": password
            }

            create_response = requests.post(
                f"{settings.AUTH_SERVICE_URL}/AAS/auth/register",
                json=user_data,
                timeout=5
            )

            if create_response.status_code == 200:
                logger.info(f"成功注册微信用户: {username}, openid: {openid}")
                return True
            else:
                logger.error(f"注册用户失败: {create_response.status_code}, {create_response.text}")
        except Exception as e:
            logger.error(f"调用认证服务注册用户时发生异常: {e}")

        return False

    def get_user_permissions(self, openid: str) -> Optional[Dict]:
        token_result = self.get_wechat_user_token(openid)
        if token_result is None:
            return None
        
        token, refresh_token = token_result
        username = generate_wechat_username(openid)
        try:
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = "Bearer test_token"

            response = requests.get(f"{settings.AUTH_SERVICE_URL}/AAS/auth/me", headers=headers, timeout=3)

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"获取权限失败: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"获取用户权限时发生异常: {e}")

        return None

    def save_user_name(self, openid: str, name: str, usp_pwd: dict = None) -> bool:
        try:
            token_result = self.get_wechat_user_token(openid)
            if token_result is None:
                return None
            
            token, refresh_token = token_result
            username = generate_wechat_username(openid)
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = "Bearer test_token"

            update_data = {
                "name": name
            }

            update_response = requests.post(
                f"{settings.AUTH_SERVICE_URL}/AAS/auth/users/{username}/uspinfo",
                json=update_data,
                headers=headers,
                timeout=5
            )

            if update_response.status_code == 200:
                logger.info(f"成功更新用户 {username} 的名字为: {name}")
                return update_response.json()
            else:
                logger.error(f"更新用户名字失败: {update_response.status_code}, {update_response.text}")
        except Exception as e:
            logger.error(f"保存用户名字时发生异常: {e}")

        return None

    def handle_user_unsubscribe(self, openid: str) -> bool:
        try:
            username = generate_wechat_username(openid)

            logger.info(f"处理用户 {username} 取消关注事件")

            return True
        except Exception as e:
            logger.error(f"处理用户取消关注事件时发生异常: {e}")

        return False

    def change_password(self, username: str, token: str, new_password: str) -> tuple:
        try:
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = "Bearer test_token"

            get_response = requests.get(
                f"{settings.AUTH_SERVICE_URL}/AAS/auth/users/{username}/detail",
                headers=headers,
                timeout=5
            )

            if get_response.status_code != 200:
                logger.error(f"获取用户信息失败: {get_response.status_code}")
                return (False, "获取用户信息失败")

            user_info = get_response.json()
            external_credentials = user_info.get("external_credentials", {})

            if "usp" not in external_credentials:
                external_credentials["usp"] = {}
            external_credentials["usp"]["password"] = new_password

            update_data = {
                "external_credentials": external_credentials
            }

            response = requests.put(
                f"{settings.AUTH_SERVICE_URL}/AAS/auth/users/{username}",
                json=update_data,
                headers=headers,
                timeout=5
            )

            if response.status_code == 200:
                logger.info(f"成功修改用户 {username} 的 USP 密码")
                return (True, "USP 密码修改成功")
            else:
                error_msg = response.json().get("detail", response.json().get("message", "修改失败")) if response.content else "修改失败"
                logger.error(f"修改用户 USP 密码失败: {response.status_code}, {error_msg}")
                return (False, error_msg)
        except Exception as e:
            logger.error(f"修改用户 USP 密码时发生异常: {e}")
            return (False, str(e))


auth_service = AuthService()