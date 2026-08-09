"""微信模块 - 代码驱动用例。

覆盖后端纯逻辑/契约级接口（排除依赖真实微信环境的 OAuth/js-sdk 等）。
"""

import allure
import pytest
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment


async def _api(client, method: str, path: str, step: str = '', headers=None,
               expected_status: int | None = None, expected_fields: dict | None = None, **kwargs):
    """Send a request wrapped in an Allure step block; assertions run inside the step."""
    with allure.step(step or f'{method.upper()} {path}'):
        r = await client.request(method, path, headers=headers, **kwargs)
        if expected_status is not None:
            assert_status_code(r, expected_status)
        if expected_fields:
            assert_dict_contains_subset(r.json(), expected_fields)
        flush_assert_attachment()
        return r


@allure.feature('微信')
class TestWechatLogin:
    """微信登录"""

    @allure.story('登录')
    @allure.title('正常：openid 登录')
    @pytest.mark.api
    async def test_login_ok(self, mock_api_client):
        """正常流程：openid 登录返回 token"""
        await _api(mock_api_client, 'post', '/api/wechat/login', json={'openid': 'openid-test-001'},
                   expected_status=200)

    @allure.story('登录')
    @allure.title('数据校验：缺 openid')
    @pytest.mark.api
    async def test_login_missing_openid(self, mock_api_client):
        """数据校验：缺 openid"""
        await _api(mock_api_client, 'post', '/api/wechat/login', json={}, expected_status=400)


@allure.feature('微信')
class TestWechatPermissions:
    """用户权限"""

    @allure.story('权限')
    @allure.title('正常：获取用户权限')
    @pytest.mark.api
    async def test_permissions_ok(self, mock_api_client):
        """正常流程：获取用户权限"""
        await _api(mock_api_client, 'get', '/api/wechat/permissions?openid=openid-test-001',
                   expected_status=200, expected_fields={'openid': 'openid-test-001'})

    @allure.story('权限')
    @allure.title('异常：用户不存在')
    @pytest.mark.api
    async def test_permissions_user_not_found(self, mock_api_client):
        """异常流程：无 openid 用户不存在"""
        await _api(mock_api_client, 'get', '/api/wechat/permissions', expected_status=404)


@allure.feature('微信')
class TestWechatTag:
    """微信标签"""

    @allure.story('标签')
    @allure.title('正常：标签列表')
    @pytest.mark.api
    async def test_tag_list(self, mock_api_client):
        """正常流程：标签列表"""
        await _api(mock_api_client, 'get', '/api/wechat/tag', expected_status=200,
                   expected_fields={'code': 0})

    @allure.story('标签')
    @allure.title('正常：创建标签')
    @pytest.mark.api
    async def test_tag_create(self, mock_api_client):
        """正常流程：创建标签"""
        await _api(mock_api_client, 'post', '/api/wechat/tag', json={'name': 'VIP'},
                   expected_status=200, expected_fields={'code': 0})

    @allure.story('标签')
    @allure.title('数据校验：缺 name')
    @pytest.mark.api
    async def test_tag_create_missing_name(self, mock_api_client):
        """数据校验：缺 name"""
        await _api(mock_api_client, 'post', '/api/wechat/tag', json={}, expected_status=422)

    @allure.story('标签')
    @allure.title('正常：更新标签')
    @pytest.mark.api
    async def test_tag_update(self, mock_api_client):
        """正常流程：更新标签"""
        await _api(mock_api_client, 'put', '/api/wechat/tag/1', json={'name': 'VVIP'},
                   expected_status=200, expected_fields={'code': 0})

    @allure.story('标签')
    @allure.title('异常：更新标签不存在')
    @pytest.mark.api
    async def test_tag_update_not_found(self, mock_api_client):
        """异常流程：标签不存在"""
        await _api(mock_api_client, 'put', '/api/wechat/tag/999', json={'name': 'x'},
                   expected_status=404)

    @allure.story('标签')
    @allure.title('正常：删除标签')
    @pytest.mark.api
    async def test_tag_delete(self, mock_api_client):
        """正常流程：删除标签"""
        await _api(mock_api_client, 'delete', '/api/wechat/tag/1', expected_status=200,
                   expected_fields={'code': 0})

    @allure.story('标签')
    @allure.title('异常：删除标签不存在')
    @pytest.mark.api
    async def test_tag_delete_not_found(self, mock_api_client):
        """异常流程：标签不存在"""
        await _api(mock_api_client, 'delete', '/api/wechat/tag/999', expected_status=404)

    @allure.story('标签')
    @allure.title('数据校验：批量打标超 100 人')
    @pytest.mark.api
    async def test_tag_batch_over_limit(self, mock_api_client):
        """数据校验：批量打标超过 100 人"""
        await _api(mock_api_client, 'post', '/api/wechat/tag/batch-tagging',
                   json={'openid_list': [f'o{i}' for i in range(101)], 'tag_id': 1},
                   expected_status=400)

    @allure.story('标签')
    @allure.title('正常：标签粉丝列表')
    @pytest.mark.api
    async def test_tag_fans(self, mock_api_client):
        """正常流程：标签粉丝列表"""
        await _api(mock_api_client, 'get', '/api/wechat/tag/1/fans', expected_status=200,
                   expected_fields={'code': 0})

    @allure.story('标签')
    @allure.title('正常：用户标签查询')
    @pytest.mark.api
    async def test_tag_user(self, mock_api_client):
        """正常流程：用户标签查询"""
        await _api(mock_api_client, 'get', '/api/wechat/tag/user/openid-1', expected_status=200,
                   expected_fields={'code': 0})


@allure.feature('微信')
class TestWechatMenu:
    """微信菜单"""

    @allure.story('菜单')
    @allure.title('正常：获取菜单')
    @pytest.mark.api
    async def test_menu_get(self, mock_api_client):
        """正常流程：获取菜单"""
        await _api(mock_api_client, 'get', '/api/wechat/get_menu', expected_status=200,
                   expected_fields={'code': 200})

    @allure.story('菜单')
    @allure.title('正常：创建菜单')
    @pytest.mark.api
    async def test_menu_create(self, mock_api_client):
        """正常流程：创建菜单"""
        await _api(mock_api_client, 'post', '/api/wechat/create_menu', expected_status=200,
                   expected_fields={'code': 0})

    @allure.story('菜单')
    @allure.title('正常：删除菜单')
    @pytest.mark.api
    async def test_menu_delete(self, mock_api_client):
        """正常流程：删除菜单"""
        await _api(mock_api_client, 'delete', '/api/wechat/delete_menu', expected_status=200,
                   expected_fields={'code': 0})


@allure.feature('微信')
class TestWechatMessage:
    """微信消息"""

    @allure.story('消息')
    @allure.title('正常：发送模板消息')
    @pytest.mark.api
    async def test_send_message(self, mock_api_client):
        """正常流程：发送模板消息"""
        await _api(mock_api_client, 'post', '/api/wechat/send_message',
                   json={'open_id': 'openid-1', 'content': 'hello'}, expected_status=200,
                   expected_fields={'code': 200})

    @allure.story('消息')
    @allure.title('数据校验：缺 open_id')
    @pytest.mark.api
    async def test_send_message_missing_openid(self, mock_api_client):
        """数据校验：缺 open_id"""
        await _api(mock_api_client, 'post', '/api/wechat/send_message', json={},
                   expected_status=422)

    @allure.story('消息')
    @allure.title('正常：群发消息')
    @pytest.mark.api
    async def test_broadcast(self, mock_api_client):
        """正常流程：群发消息"""
        await _api(mock_api_client, 'post', '/api/wechat/broadcast_message',
                   json={'content': 'all'}, expected_status=200, expected_fields={'code': 200})

    @allure.story('消息')
    @allure.title('数据校验：群发缺 content')
    @pytest.mark.api
    async def test_broadcast_missing_content(self, mock_api_client):
        """数据校验：群发缺 content"""
        await _api(mock_api_client, 'post', '/api/wechat/broadcast_message', json={},
                   expected_status=422)

    @allure.story('消息')
    @allure.title('数据校验：链接消息缺 url')
    @pytest.mark.api
    async def test_send_link_missing_url(self, mock_api_client):
        """数据校验：链接消息缺 url"""
        await _api(mock_api_client, 'post', '/api/wechat/send_link_message',
                   json={'open_id': 'openid-1', 'title': 't', 'description': 'd'},
                   expected_status=422)

    @allure.story('消息')
    @allure.title('异常：webnotify @所有人')
    @pytest.mark.api
    async def test_webnotify_at_all(self, mock_api_client):
        """异常流程：webnotify @所有人被拒"""
        await _api(mock_api_client, 'post', '/api/wechat/webnotify',
                   json={'msg_type': 'text', 'message_id': '1', 'at': {'is_all': True}},
                   expected_status=400)

    @allure.story('消息')
    @allure.title('异常：backend/notify @所有人')
    @pytest.mark.api
    async def test_backend_notify_at_all(self, mock_api_client):
        """异常流程：backend/notify @所有人被拒"""
        await _api(mock_api_client, 'post', '/api/wechat/backend/notify/',
                   json={'text': {'content': 'x'}, 'at': {'is_all': True}},
                   expected_status=400)


@allure.feature('微信')
class TestWechatImportData:
    """数据导入"""

    @allure.story('导入')
    @allure.title('数据校验：缺 project')
    @pytest.mark.api
    async def test_import_missing_project(self, mock_api_client):
        """数据校验：缺 project"""
        await _api(mock_api_client, 'post', '/api/wechat/import-data',
                   json={'indicator': 'i', 'content': []}, expected_status=400)

    @allure.story('导入')
    @allure.title('数据校验：content 非列表')
    @pytest.mark.api
    async def test_import_content_not_list(self, mock_api_client):
        """数据校验：content 非列表"""
        await _api(mock_api_client, 'post', '/api/wechat/import-data',
                   json={'project': 'p', 'indicator': 'i', 'content': 'not-list'},
                   expected_status=400)

    @allure.story('导入')
    @allure.title('正常：导入数据')
    @pytest.mark.api
    async def test_import_ok(self, mock_api_client):
        """正常流程：导入数据"""
        await _api(mock_api_client, 'post', '/api/wechat/import-data',
                   json={'project': 'p', 'indicator': 'i', 'content': [{'a': 1}]},
                   expected_status=200, expected_fields={'success': True})
