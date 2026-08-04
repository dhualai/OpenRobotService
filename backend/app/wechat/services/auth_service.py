import logging
from typing import Dict, Optional
import json

from app.wechat.utils.crypto import generate_wechat_username, generate_wechat_user_password
from app.core.auth_service import AuthService as CoreAuthService, AuthServiceError
from app.services.user_service import user_service
from app.core.database import db_manager
from app.core.security import get_password_hash
from app.services.hmac_utils import chinese_to_pinyin, generate_password

logger = logging.getLogger(__name__)


class AuthService:

    def __init__(self):
        pass

    def get_wechat_user_token(self, openid: str) -> Optional[str]:
        try:
            username = generate_wechat_username(openid)
            password = generate_wechat_user_password(openid)

            login_result = CoreAuthService.login(username, password)
            token = login_result.get("access_token")
            refresh_token = login_result.get("refresh_token")
            if token:
                return token, refresh_token
        except AuthServiceError as e:
            logger.error(f"获取微信用户token失败: {e.detail}")
        except Exception as e:
            logger.error(f"获取微信用户token失败: {e}")

        return None

    def register_wechat_user(self, openid: str) -> bool:
        try:
            username = generate_wechat_username(openid)
            password = generate_wechat_user_password(openid)
            hashed_password = get_password_hash(password)

            default_role_id = "role_e9351224"

            existing_user = db_manager.get_user(username)
            if existing_user:
                logger.info(f"用户已存在，更新密码: {username}")
                user_detail = user_service.get_user_detail(username)
                if user_detail:
                    success = db_manager.update_user(user_detail["id"], password_hash=hashed_password, status="active")
                    if success:
                        logger.info(f"成功更新用户密码: {username}")
                        user_project_role_id = f"upr_{openid}_global_{default_role_id}"
                        db_manager.add_user_project_role(
                            user_project_role_id,
                            openid,
                            None,
                            default_role_id
                        )
                        return True
                    else:
                        logger.error(f"更新用户密码失败")
                        return False
                return False

            success = db_manager.add_user(
                user_id=openid,
                username=username,
                hashed_password=hashed_password,
                permissions=["user"],
                status="active"
            )

            if success:
                logger.info(f"成功注册微信用户: {username}, openid: {openid}")
                user_project_role_id = f"upr_{openid}_global_{default_role_id}"
                db_manager.add_user_project_role(
                    user_project_role_id,
                    openid,
                    None,
                    default_role_id
                )
                return True
            else:
                logger.error(f"注册用户失败")
        except Exception as e:
            logger.error(f"注册用户时发生异常: {e}")

        return False

    def get_user_permissions(self, openid: str) -> Optional[Dict]:
        token_result = self.get_wechat_user_token(openid)
        if token_result is None:
            return None
        
        token, refresh_token = token_result
        try:
            return CoreAuthService.get_user_info(token)
        except AuthServiceError as e:
            logger.error(f"获取权限失败: {e.detail}")
        except Exception as e:
            logger.error(f"获取用户权限时发生异常: {e}")

        return None

    def save_user_name(self, openid: str, name: str, usp_pwd: dict = None) -> Optional[Dict]:
        """保存用户姓名并生成 USP 凭证。

        安全设计（参考 tmp_aas）：DB 中 external_credentials.usp.password 存哈希值，
        明文密码仅通过返回值一次性回显给调用方，用于即时告知用户初始密码。
        失败返回 None。
        """
        try:
            username = generate_wechat_username(openid)
            user_detail = user_service.get_user_detail(username)

            if not user_detail:
                logger.error(f"用户不存在: {username}")
                return None

            usp_username = chinese_to_pinyin(name)

            all_users = user_service.get_user_list()
            existing_usernames = set()
            for user in all_users:
                credentials = user.get("external_credentials", {})
                if credentials.get("usp"):
                    existing_usernames.add(credentials["usp"].get("username", ""))

            if usp_username in existing_usernames:
                suffix = 2
                while f"{usp_username}{suffix}" in existing_usernames:
                    suffix += 1
                usp_username = f"{usp_username}{suffix}"

            usp_password = generate_password(usp_username)
            usp_password_hash = get_password_hash(usp_password)

            external_credentials = user_detail.get("external_credentials", {})
            external_credentials["usp"] = {
                "username": usp_username,
                "password": usp_password_hash
            }

            update_data = {
                "name": name,
                "external_credentials": external_credentials
            }

            success = db_manager.update_user(user_detail["id"], **update_data)

            if success:
                logger.info(f"成功更新用户 {username} 的名字为: {name}")
                # 仅通过返回值一次性返回明文凭证，DB 中存的是哈希
                return {"username": usp_username, "password": usp_password}
            else:
                logger.error(f"更新用户名字失败")
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
            user_info = user_service.get_user_detail(username)
            if not user_info:
                logger.error(f"获取用户信息失败")
                return (False, "获取用户信息失败")

            external_credentials = user_info.get("external_credentials", {})

            if "usp" not in external_credentials:
                external_credentials["usp"] = {}
            external_credentials["usp"]["password"] = get_password_hash(new_password)

            update_data = {
                "external_credentials": external_credentials
            }

            success = db_manager.update_user(user_info["id"], **update_data)

            if success:
                logger.info(f"成功修改用户 {username} 的 USP 密码")
                return (True, "USP 密码修改成功")
            else:
                logger.error(f"修改用户 USP 密码失败")
                return (False, "修改失败")
        except Exception as e:
            logger.error(f"修改用户 USP 密码时发生异常: {e}")
            return (False, str(e))


auth_service = AuthService()