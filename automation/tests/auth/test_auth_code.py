"""认证模块 - 代码驱动用例（自由函数模式）。

与 test_auth.py（Excel 数据驱动）并行，代码驱动迁移版。
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


@allure.feature('认证')
class TestLogin:
    """登录"""

    @allure.story('登录')
    @allure.title('正常：登录成功')
    @pytest.mark.api
    async def test_login_admin(self, mock_api_client):
        """正常流程：登录成功"""
        await _api(mock_api_client, 'post', '/api/auth/login',
                   json={'username': 'testadmin', 'password': 'admin123'},
                   expected_status=200, expected_fields={'token_type': 'bearer'})

    @allure.story('登录')
    @allure.title('异常：密码错误')
    @pytest.mark.api
    async def test_login_wrong_password(self, mock_api_client):
        """异常流程：密码错误"""
        await _api(mock_api_client, 'post', '/api/auth/login',
                   json={'username': 'testadmin', 'password': 'wrong'}, expected_status=401)

    @allure.story('登录')
    @allure.title('正常：工程师登录')
    @pytest.mark.api
    async def test_login_engineer(self, mock_api_client):
        """正常流程：工程师登录"""
        await _api(mock_api_client, 'post', '/api/auth/login',
                   json={'username': 'engineer', 'password': 'eng123'},
                   expected_status=200, expected_fields={'token_type': 'bearer'})

    @allure.story('登录')
    @allure.title('正常：客户登录')
    @pytest.mark.api
    async def test_login_customer(self, mock_api_client):
        """正常流程：客户登录"""
        await _api(mock_api_client, 'post', '/api/auth/login',
                   json={'username': 'customer', 'password': 'cust123'},
                   expected_status=200, expected_fields={'token_type': 'bearer'})

    @allure.story('登录')
    @allure.title('数据校验：用户名为空')
    @pytest.mark.api
    async def test_login_empty_username(self, mock_api_client):
        """数据校验：用户名为空"""
        await _api(mock_api_client, 'post', '/api/auth/login',
                   json={'username': '', 'password': 'admin123'}, expected_status=422)

    @allure.story('登录')
    @allure.title('数据校验：缺少 username/password')
    @pytest.mark.api
    async def test_login_missing_fields(self, mock_api_client):
        """数据校验：缺少 username/password"""
        await _api(mock_api_client, 'post', '/api/auth/login', expected_status=422)

    @allure.story('登录')
    @allure.title('异常：用户不存在')
    @pytest.mark.api
    async def test_login_user_not_found(self, mock_api_client):
        """异常流程：用户不存在"""
        await _api(mock_api_client, 'post', '/api/auth/login',
                   json={'username': 'nonexistent', 'password': 'pass'}, expected_status=401)


@allure.feature('认证')
class TestMe:
    """当前用户"""

    @allure.story('当前用户')
    @allure.title('权限：无 token 访问')
    @pytest.mark.api
    async def test_me_unauthorized(self, mock_api_client):
        """权限：无 token 访问"""
        await _api(mock_api_client, 'get', '/api/auth/me', expected_status=401)

    @allure.story('当前用户')
    @allure.title('正常：获取当前用户(admin)')
    @pytest.mark.api
    async def test_me_admin(self, mock_api_client, mock_auth_header):
        """正常流程：获取当前用户(admin)"""
        await _api(mock_api_client, 'get', '/api/auth/me', headers=mock_auth_header,
                   expected_status=200, expected_fields={'username': 'testadmin'})


@allure.feature('认证')
class TestWechat:
    """微信"""

    @allure.story('微信')
    @allure.title('正常：微信健康检查')
    @pytest.mark.api
    async def test_wechat_health(self, mock_api_client):
        """正常流程：微信健康检查"""
        await _api(mock_api_client, 'get', '/api/wechat/health',
                   expected_status=200, expected_fields={'code': 200})

    @allure.story('微信')
    @allure.title('正常：微信标签列表')
    @pytest.mark.api
    async def test_wechat_tags(self, mock_api_client):
        """正常流程：微信标签列表"""
        await _api(mock_api_client, 'get', '/api/wechat', expected_status=200)

    @allure.story('微信')
    @allure.title('正常：微信回调 POST')
    @pytest.mark.api
    async def test_wechat_callback(self, mock_api_client):
        """正常流程：微信回调 POST"""
        await _api(mock_api_client, 'post', '/api/wechat', json={'xml': '<xml/>'},
                   expected_status=200)
