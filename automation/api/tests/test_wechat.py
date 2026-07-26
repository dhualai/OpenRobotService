"""Phase 2: WeChat API tests.

WeChat health, menu, message, tag, and notification endpoints.
Uses mock backend (no real infrastructure needed).
"""

import pytest

from automation.assertions import assert_status_code


pytestmark = pytest.mark.api


class TestWeChatHealth:
    """GET /api/wechat/health — WeChat module health check."""

    async def test_wechat_health(self, mock_api_client, mock_auth_header):
        response = await mock_api_client.request("GET", "/api/wechat/health",
            headers=mock_auth_header)
        assert_status_code(response, 200)
        data = response.json()
        assert data["message"] == "服务运行正常"


class TestWeChatMenu:
    """WeChat menu endpoint tests."""

    async def test_get_menu(self, mock_api_client, mock_auth_header):
        response = await mock_api_client.request("GET", "/api/wechat/get_menu",
            headers=mock_auth_header)
        assert_status_code(response, 200)
        assert "menu" in response.json()

    async def test_create_menu(self, mock_api_client, mock_auth_header):
        menu_data = {
            "button": [
                {"type": "click", "name": "今日推荐", "key": "V1001_TODAY"},
                {"type": "view", "name": "搜索", "url": "http://www.qq.com"},
            ]
        }
        response = await mock_api_client.request("POST", "/api/wechat/create_menu",
            headers=mock_auth_header, json=menu_data)
        assert_status_code(response, 200)
        assert response.json()["errcode"] == 0


class TestWeChatMessage:
    """WeChat message sending tests."""

    async def test_send_message(self, mock_api_client, mock_auth_header):
        msg_data = {
            "touser": ["user_openid"],
            "msgtype": "text",
            "text": {"content": "Hello from test"},
        }
        response = await mock_api_client.request("POST", "/api/wechat/send_message",
            headers=mock_auth_header, json=msg_data)
        assert_status_code(response, 200)
        assert response.json()["errcode"] == 0


class TestWeChatTag:
    """WeChat tag management tests."""

    async def test_list_tags(self, mock_api_client, mock_auth_header):
        response = await mock_api_client.request("GET", "/api/wechat",
            headers=mock_auth_header)
        assert_status_code(response, 200)
        data = response.json()
        assert "tags" in data
        assert isinstance(data["tags"], list)

    async def test_create_tag(self, mock_api_client, mock_auth_header):
        tag_data = {"name": "VIP客户"}
        response = await mock_api_client.request("POST", "/api/wechat",
            headers=mock_auth_header, json=tag_data)
        assert_status_code(response, 200)
        data = response.json()
        assert data["tag"]["name"] == "VIP客户"
        assert data["tag"]["id"] > 0
