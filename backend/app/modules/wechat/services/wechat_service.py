import time
import requests
import json
import asyncio
import hashlib
import random
from typing import Dict, List, Optional
from app.core.config import settings


class WechatService:

    def __init__(self):
        self.session = requests.Session()
        self.access_token_info = {
            'access_token': '',
            'expires_at': 0
        }
        self.jsapi_ticket_info = {
            'jsapi_ticket': '',
            'expires_at': 0
        }

    def get_access_token(self) -> Optional[str]:
        now = int(time.time())

        if self.access_token_info['access_token'] and self.access_token_info['expires_at'] > now:
            return self.access_token_info['access_token']

        url = f'{settings.WECHAT_TOKEN_URL}?grant_type=client_credential&appid={settings.WECHAT_CONFIG["app_id"]}&secret={settings.WECHAT_CONFIG["app_secret"]}'

        try:
            response = self.session.get(url, timeout=5)
            result = response.json()

            if 'access_token' in result:
                self.access_token_info['access_token'] = result['access_token']
                self.access_token_info['expires_at'] = now + 7000
                return result['access_token']
            else:
                print(f'获取access_token失败: {result}')
                return None
        except Exception as e:
            print(f'请求access_token异常: {e}')
            return None

    def send_message_to_user(self, open_id: str, content: str, url: Optional[str] = None) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            return False

        url_endpoint = f'{settings.WECHAT_SEND_MESSAGE_URL}?access_token={access_token}'

        data = {
            'touser': open_id,
            'msgtype': 'text',
            'text': {
                'content': content + (f'\n\n点击查看：{url}' if url else '')
            }
        }
        print(data)
        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url_endpoint, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode') == 0:
                print(f'成功推送消息给用户 {open_id}')
                return True
            else:
                print(f'推送消息失败: {result}')
                return False
        except Exception as e:
            print(f'请求推送消息异常: {e}')
            return False

    def send_link_message_to_user(self, open_id: str, title: str, description: str, url: str) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            return False, {"errmsg": "获取access_token失败"}

        url_endpoint = f'{settings.WECHAT_SEND_MESSAGE_URL}?access_token={access_token}'

        data = {
            'touser': open_id,
            'msgtype': 'link',
            'link': {
                'title': title,
                'description': description,
                'url': url
            }
        }
        print(data)
        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url_endpoint, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode') == 0:
                print(f'成功推送链接消息给用户 {open_id}')
                return True, None
            else:
                print(f'推送链接消息失败: {result}')
                return False, result
        except Exception as e:
            print(f'请求推送链接消息异常: {e}')
            return False, {'errcode': -1, 'errmsg': str(e)}

    def send_template_message(self, open_id: str, data: Dict, link_url: Optional[str] = None, template_id: str = None) -> bool:
        if template_id is None:
            template_id = 'TukPKMkubGh-SRmWQjHxV027zUYwcCbDUSxcJh-fCMA'

        access_token = self.get_access_token()
        if not access_token:
            return False, {"errmsg": "获取access_token失败"}

        url = f'{settings.WECHAT_TEMPLATE_MESSAGE_URL}?access_token={access_token}'

        if link_url:
            request_data = {
                'touser': open_id,
                'template_id': template_id,
                'data': data,
                'url': link_url
            }
        else:
            request_data = {
                'touser': open_id,
                'template_id': template_id,
                'data': data,
            }

        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url, data=json.dumps(request_data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode') == 0:
                print(f'成功推送模板消息给用户 {open_id}')
                return True, None

            else:
                print(f'推送模板消息失败: {result}')
                return False, result
        except Exception as e:
            print(f'请求推送模板消息异常: {e}')
            return False, {'errcode': -1, 'errmsg': str(e)}

    def get_user_list(self, next_openid: str = '') -> Optional[Dict]:
        access_token = self.get_access_token()
        if not access_token:
            return None

        url = f'{settings.WECHAT_USER_LIST_URL}?access_token={access_token}&next_openid={next_openid}'

        try:
            response = self.session.get(url, timeout=5)
            result = response.json()

            if 'data' in result and 'openid' in result['data']:
                return result
            else:
                print(f'获取用户列表失败: {result}')
                return None
        except Exception as e:
            print(f'请求用户列表异常: {e}')
            return None

    def broadcast_message(self, content: str) -> bool:
        user_list_result = self.get_user_list()
        if not user_list_result or 'data' not in user_list_result or 'openid' not in user_list_result['data']:
            print('获取用户列表失败，无法进行广播')
            return False

        open_ids = user_list_result['data']['openid']
        success_count = 0
        fail_count = 0

        for open_id in open_ids:
            if self.send_message_to_user(open_id, content):
                success_count += 1
            else:
                fail_count += 1

            time.sleep(0.1)

        print(f'广播完成，成功: {success_count}, 失败: {fail_count}')
        return success_count > 0

    def create_wechat_menu(self) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            print("获取access_token失败，无法创建菜单")
            return False

        try:
            with open("menu.json", "r", encoding="utf-8") as f:
                menu_data = json.load(f)

            regular_menu_data = {"button": menu_data.get("button", [])}

            url = f"{settings.WECHAT_MENU_CREATE_URL}?access_token={access_token}"

            headers = {'Content-Type': 'application/json'}
            menu_json = json.dumps(regular_menu_data, ensure_ascii=False)
            response = self.session.post(url, data=menu_json.encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode') == 0:
                print("微信服务号菜单创建成功")
                return True
            else:
                print(f"微信服务号菜单创建失败: {result}")
                return False
        except FileNotFoundError:
            print("menu.json文件不存在")
            return False
        except json.JSONDecodeError as e:
            print(f"解析menu.json文件失败: {e}")
            return False
        except Exception as e:
            print(f"创建微信服务号菜单时发生异常: {e}")
            return False

    def get_wechat_menu(self) -> Optional[Dict]:
        access_token = self.get_access_token()
        if not access_token:
            print("获取access_token失败，无法获取菜单")
            return None

        try:
            url = f"{settings.WECHAT_MENU_GET_URL}?access_token={access_token}"
            response = self.session.get(url, timeout=5)
            result = response.json()

            if 'menu' in result or 'conditionalmenu' in result:
                print("成功获取微信服务号菜单")
                return result
            else:
                print(f"获取微信服务号菜单失败: {result}")
                return None
        except Exception as e:
            print(f"获取微信服务号菜单时发生异常: {e}")
            return None

    def delete_wechat_menu(self) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            print("获取access_token失败，无法删除菜单")
            return False

        try:
            url = f"{settings.WECHAT_MENU_DELETE_URL}?access_token={access_token}"
            response = self.session.get(url, timeout=5)
            result = response.json()

            if result.get('errcode') == 0:
                print("微信服务号菜单删除成功")
                return True
            else:
                print(f"微信服务号菜单删除失败: {result}")
                return False
        except Exception as e:
            print(f"删除微信服务号菜单时发生异常: {e}")
            return False

    def create_conditional_menu(self, menu_data: Dict) -> Optional[str]:
        access_token = self.get_access_token()
        if not access_token:
            print("获取access_token失败，无法创建个性化菜单")
            return None

        try:
            url = f"{settings.WECHAT_MENU_ADDCONDITIONAL_URL}?access_token={access_token}"
            headers = {'Content-Type': 'application/json'}
            menu_json = json.dumps(menu_data, ensure_ascii=False)
            response = self.session.post(url, data=menu_json.encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                print("个性化菜单创建成功")
                return result.get('menuid')
            else:
                print(f"个性化菜单创建失败: {result}")
                return None
        except Exception as e:
            print(f"创建个性化菜单时发生异常: {e}")
            return None

    def create_conditional_menu_from_file(self) -> List[str]:
        access_token = self.get_access_token()
        if not access_token:
            print("获取access_token失败，无法创建个性化菜单")
            return []

        try:
            with open("conditional_menu.json", "r", encoding="utf-8") as f:
                menu_data = json.load(f)

            menu_ids = []

            if not self.create_wechat_menu():
                print("创建默认菜单失败，无法继续创建个性化菜单")
                return []

            if "conditionalmenu" in menu_data:
                for conditional_menu in menu_data["conditionalmenu"]:
                    if "matchrule" not in conditional_menu:
                        print("conditionalmenu缺少matchrule字段，跳过该个性化菜单")
                        continue

                    menuid = self.create_conditional_menu(conditional_menu)
                    if menuid:
                        menu_ids.append(menuid)
            elif "matchrule" in menu_data:
                menuid = self.create_conditional_menu(menu_data)
                if menuid:
                    menu_ids.append(menuid)
            else:
                print("menu.json文件缺少matchrule字段，无法创建个性化菜单")

            return menu_ids
        except FileNotFoundError:
            print("menu.json文件不存在")
            return []
        except json.JSONDecodeError as e:
            print(f"解析menu.json文件失败: {e}")
            return []
        except Exception as e:
            print(f"从文件创建个性化菜单时发生异常: {e}")
            return []

    def delete_conditional_menu(self, menuid: str) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            print("获取access_token失败，无法删除个性化菜单")
            return False

        try:
            url = f"{settings.WECHAT_MENU_DELCONDITIONAL_URL}?access_token={access_token}"
            headers = {'Content-Type': 'application/json'}
            data = json.dumps({'menuid': menuid}, ensure_ascii=False)
            response = self.session.post(url, data=data.encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode') == 0:
                print("个性化菜单删除成功")
                return True
            else:
                print(f"个性化菜单删除失败: {result}")
                return False
        except Exception as e:
            print(f"删除个性化菜单时发生异常: {e}")
            return False

    def try_match_menu(self, user_id: str) -> Optional[Dict]:
        access_token = self.get_access_token()
        if not access_token:
            print("获取access_token失败，无法测试个性化菜单")
            return None

        try:
            url = f"{settings.WECHAT_MENU_TRYCATCH_URL}?access_token={access_token}"
            headers = {'Content-Type': 'application/json'}
            data = json.dumps({'user_id': user_id}, ensure_ascii=False)
            response = self.session.post(url, data=data.encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if 'menu' in result:
                print("成功获取匹配的个性化菜单")
                return result
            else:
                print(f"测试个性化菜单失败: {result}")
                return None
        except Exception as e:
            print(f"测试个性化菜单时发生异常: {e}")
            return None

    async def get_openid(self, code: str, app_id: str, app_secret: str) -> Optional[Dict]:
        url = f"https://api.weixin.qq.com/sns/oauth2/access_token?appid={app_id}&secret={app_secret}&code={code}&grant_type=authorization_code"

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.session.get(url, timeout=5)
            )
            result = response.json()

            if 'openid' in result:
                return result
            else:
                print(f'获取openid失败: {result}')
                return result
        except Exception as e:
            print(f'请求openid异常: {e}')
            return None

    def get_jsapi_ticket(self) -> Optional[str]:
        now = int(time.time())

        if self.jsapi_ticket_info['jsapi_ticket'] and self.jsapi_ticket_info['expires_at'] > now:
            return self.jsapi_ticket_info['jsapi_ticket']

        access_token = self.get_access_token()
        if not access_token:
            print("获取access_token失败，无法获取jsapi_ticket")
            return None

        url = f"https://api.weixin.qq.com/cgi-bin/ticket/getticket?access_token={access_token}&type=jsapi"

        try:
            response = self.session.get(url, timeout=5)
            result = response.json()

            if result.get('errcode') == 0 and 'ticket' in result:
                self.jsapi_ticket_info['jsapi_ticket'] = result['ticket']
                self.jsapi_ticket_info['expires_at'] = now + 7000
                return result['ticket']
            else:
                print(f'获取jsapi_ticket失败: {result}')
                return None
        except Exception as e:
            print(f'请求jsapi_ticket异常: {e}')
            return None

    def generate_nonce_str(self, length: int = 16) -> str:
        chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        return ''.join(random.choice(chars) for _ in range(length))

    def generate_signature(self, jsapi_ticket: str, nonce_str: str, timestamp: int, url: str) -> str:
        params = [
            f'jsapi_ticket={jsapi_ticket}',
            f'noncestr={nonce_str}',
            f'timestamp={timestamp}',
            f'url={url}'
        ]
        params.sort()

        string1 = '&'.join(params)

        sha = hashlib.sha1(string1.encode('utf-8'))
        return sha.hexdigest()

    async def get_js_sdk_config(self, url: str) -> Optional[Dict]:
        try:
            jsapi_ticket = self.get_jsapi_ticket()
            if not jsapi_ticket:
                print("获取jsapi_ticket失败，无法生成JS-SDK配置")
                return None

            nonce_str = self.generate_nonce_str()
            timestamp = int(time.time())

            signature = self.generate_signature(jsapi_ticket, nonce_str, timestamp, url)

            return {
                'appId': settings.WECHAT_CONFIG['app_id'],
                'timestamp': timestamp,
                'nonceStr': nonce_str,
                'signature': signature
            }
        except Exception as e:
            print(f'获取JS-SDK配置异常: {e}')
            return None

    def get_tags(self) -> Optional[Dict]:
        access_token = self.get_access_token()
        if not access_token:
            return None

        url = f'{settings.WECHAT_TAGS_GET_URL}?access_token={access_token}'

        try:
            response = self.session.get(url, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                return result
            else:
                print(f'获取标签失败: {result}')
                return None
        except Exception as e:
            print(f'请求获取标签异常: {e}')
            return None

    def create_tag(self, name: str) -> Optional[Dict]:
        access_token = self.get_access_token()
        if not access_token:
            return None

        url = f'{settings.WECHAT_TAGS_CREATE_URL}?access_token={access_token}'

        data = {
            'tag': {
                'name': name
            }
        }

        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                return result
            else:
                print(f'创建标签失败: {result}')
                return None
        except Exception as e:
            print(f'请求创建标签异常: {e}')
            return None

    def update_tag(self, tag_id: int, name: str) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            return False

        url = f'{settings.WECHAT_TAGS_UPDATE_URL}?access_token={access_token}'

        data = {
            'tag': {
                'id': tag_id,
                'name': name
            }
        }

        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                return True
            else:
                print(f'更新标签失败: {result}')
                return False
        except Exception as e:
            print(f'请求更新标签异常: {e}')
            return False

    def delete_tag(self, tag_id: int) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            return False

        url = f'{settings.WECHAT_TAGS_DELETE_URL}?access_token={access_token}'

        data = {
            'tag': {
                'id': tag_id
            }
        }

        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                return True
            else:
                print(f'删除标签失败: {result}')
                return False
        except Exception as e:
            print(f'请求删除标签异常: {e}')
            return False

    def batch_tagging(self, openid_list: List[str], tag_id: int) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            return False

        url = f'{settings.WECHAT_TAGS_MEMBERS_BATCHTAGGING_URL}?access_token={access_token}'

        data = {
            'openid_list': openid_list,
            'tagid': tag_id
        }

        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                return True
            else:
                print(f'批量打标签失败: {result}')
                return False
        except Exception as e:
            print(f'请求批量打标签异常: {e}')
            return False

    def batch_untagging(self, openid_list: List[str], tag_id: int) -> bool:
        access_token = self.get_access_token()
        if not access_token:
            return False

        url = f'{settings.WECHAT_TAGS_MEMBERS_BATCHUNTAGGING_URL}?access_token={access_token}'

        data = {
            'openid_list': openid_list,
            'tagid': tag_id
        }

        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                return True
            else:
                print(f'批量取消标签失败: {result}')
                return False
        except Exception as e:
            print(f'请求批量取消标签异常: {e}')
            return False

    def get_tag_fans(self, tag_id: int, next_openid: str = '') -> Optional[Dict]:
        access_token = self.get_access_token()
        if not access_token:
            return None

        url = f'{settings.WECHAT_TAGS_MEMBERS_GET_URL}?access_token={access_token}'

        data = {
            'tagid': tag_id,
            'next_openid': next_openid
        }

        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                return result
            else:
                print(f'获取标签下粉丝列表失败: {result}')
                return None
        except Exception as e:
            print(f'请求获取标签下粉丝列表异常: {e}')
            return None

    def get_tag_id_list(self, openid: str) -> Optional[Dict]:
        access_token = self.get_access_token()
        if not access_token:
            return None

        url = f'{settings.WECHAT_TAGS_GET_ID_LIST_URL}?access_token={access_token}'

        data = {
            'openid': openid
        }

        try:
            headers = {'Content-Type': 'application/json'}
            response = self.session.post(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, timeout=5)
            result = response.json()

            if result.get('errcode', 0) == 0:
                return result
            else:
                print(f'获取用户标签列表失败: {result}')
                return None
        except Exception as e:
            print(f'请求获取用户标签列表异常: {e}')
            return None

    def request_debug(self, url: str, method: str, params: Dict, body: Dict) -> Optional[Dict]:
        access_token = self.get_access_token()
        if not access_token:
            return {'errcode': 400, 'errmsg': 'access_token is empty'}

        url = f'{settings.WECHAT_API_BASE_URL}{url}?access_token={access_token}'

        headers = {'Content-Type': 'application/json'}
        if method == "GET":
            response = self.session.get(url, params=params, timeout=30.0)
        elif method == "POST":
            response = self.session.post(url, params=params, json=body, headers=headers, timeout=30.0)
        else:
            return {'errcode': 400, 'errmsg': f"不支持的方法: {method}"}

        result = response.json()

        return result


wechat_service = WechatService()