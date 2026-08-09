"""外部集成模块 - 代码驱动用例。

覆盖 task-user-mappings CRUD、任务源 sources（X-API-Key 鉴权）、wecom 同步。
"""

import allure
import pytest
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment

_API_KEY = {'X-API-Key': 'test-api-key'}


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


@allure.feature('外部集成')
class TestMappings:
    """任务用户映射 CRUD"""

    @allure.story('映射')
    @allure.title('正常：映射列表')
    @pytest.mark.api
    async def test_mappings_list(self, mock_api_client, mock_auth_header):
        """正常流程：映射列表"""
        await _api(mock_api_client, 'get', '/api/admin/task-user-mappings', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('映射')
    @allure.title('正常：创建映射')
    @pytest.mark.api
    async def test_mappings_create(self, mock_api_client, mock_auth_header):
        """正常流程：创建映射"""
        await _api(mock_api_client, 'post', '/api/admin/task-user-mappings', headers=mock_auth_header,
                   json={'source': 'zentao', 'external_account': 'zhangsan',
                         'external_realname': '张三', 'local_user_id': 1},
                   expected_status=201, expected_fields={'source': 'zentao'})

    @allure.story('映射')
    @allure.title('数据校验：缺 source')
    @pytest.mark.api
    async def test_mappings_create_missing_source(self, mock_api_client, mock_auth_header):
        """数据校验：缺 source"""
        await _api(mock_api_client, 'post', '/api/admin/task-user-mappings', headers=mock_auth_header,
                   json={'external_account': 'x'}, expected_status=422)

    @allure.story('映射')
    @allure.title('异常：重复映射 409')
    @pytest.mark.api
    async def test_mappings_create_duplicate(self, mock_api_client, mock_auth_header):
        """异常流程：source+external_account 重复"""
        await _api(mock_api_client, 'post', '/api/admin/task-user-mappings', headers=mock_auth_header,
                   json={'source': 'zentao', 'external_account': 'dup-user'}, expected_status=201)
        await _api(mock_api_client, 'post', '/api/admin/task-user-mappings', headers=mock_auth_header,
                   json={'source': 'zentao', 'external_account': 'dup-user'}, expected_status=409)

    @allure.story('映射')
    @allure.title('正常：更新映射')
    @pytest.mark.api
    async def test_mappings_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新映射"""
        r = await _api(mock_api_client, 'post', '/api/admin/task-user-mappings', headers=mock_auth_header,
                       json={'source': 'zentao', 'external_account': 'lisi'}, expected_status=201)
        mid = r.json()['id']
        await _api(mock_api_client, 'put', f'/api/admin/task-user-mappings/{mid}',
                   headers=mock_auth_header, json={'local_user_id': 9}, expected_status=200,
                   expected_fields={'id': mid})

    @allure.story('映射')
    @allure.title('异常：更新映射不存在')
    @pytest.mark.api
    async def test_mappings_update_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：映射不存在"""
        await _api(mock_api_client, 'put', '/api/admin/task-user-mappings/999',
                   headers=mock_auth_header, json={'local_user_id': 1}, expected_status=404)

    @allure.story('映射')
    @allure.title('正常：删除映射')
    @pytest.mark.api
    async def test_mappings_delete(self, mock_api_client, mock_auth_header):
        """正常流程：删除映射"""
        r = await _api(mock_api_client, 'post', '/api/admin/task-user-mappings', headers=mock_auth_header,
                       json={'source': 'zentao', 'external_account': 'wangwu'}, expected_status=201)
        mid = r.json()['id']
        await _api(mock_api_client, 'delete', f'/api/admin/task-user-mappings/{mid}',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('映射')
    @allure.title('异常：删除映射不存在')
    @pytest.mark.api
    async def test_mappings_delete_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：映射不存在"""
        await _api(mock_api_client, 'delete', '/api/admin/task-user-mappings/999',
                   headers=mock_auth_header, expected_status=404)

    @allure.story('映射')
    @allure.title('权限：未认证访问映射')
    @pytest.mark.api
    async def test_mappings_unauthorized(self, mock_api_client):
        """权限：未认证访问映射"""
        await _api(mock_api_client, 'get', '/api/admin/task-user-mappings', expected_status=401)


@allure.feature('外部集成')
class TestSources:
    """任务源（X-API-Key 鉴权）"""

    @allure.story('任务源')
    @allure.title('权限：缺 API Key 401')
    @pytest.mark.api
    async def test_sources_missing_key(self, mock_api_client):
        """权限：缺 API Key"""
        await _api(mock_api_client, 'get', '/api/tasks/sources', expected_status=401)

    @allure.story('任务源')
    @allure.title('权限：错误 API Key 401')
    @pytest.mark.api
    async def test_sources_wrong_key(self, mock_api_client):
        """权限：错误 API Key"""
        await _api(mock_api_client, 'get', '/api/tasks/sources', headers={'X-API-Key': 'bad-key'},
                   expected_status=401)

    @allure.story('任务源')
    @allure.title('正常：任务源列表')
    @pytest.mark.api
    async def test_sources_list(self, mock_api_client):
        """正常流程：任务源列表"""
        await _api(mock_api_client, 'get', '/api/tasks/sources', headers=_API_KEY,
                   expected_status=200)

    @allure.story('任务源')
    @allure.title('正常：同步已注册源')
    @pytest.mark.api
    async def test_sources_sync_ok(self, mock_api_client):
        """正常流程：同步已注册源"""
        await _api(mock_api_client, 'post', '/api/tasks/sources/zentao/sync', headers=_API_KEY,
                   expected_status=200, expected_fields={'code': 200})

    @allure.story('任务源')
    @allure.title('异常：同步未注册源 404')
    @pytest.mark.api
    async def test_sources_sync_not_registered(self, mock_api_client):
        """异常流程：同步未注册源"""
        await _api(mock_api_client, 'post', '/api/tasks/sources/unknown-source/sync',
                   headers=_API_KEY, expected_status=404)


@allure.feature('外部集成')
class TestWecomSync:
    """企微同步"""

    @allure.story('企微同步')
    @allure.title('权限：未认证 401')
    @pytest.mark.api
    async def test_wecom_sync_unauthorized(self, mock_api_client):
        """权限：未认证同步企微项目"""
        await _api(mock_api_client, 'post', '/api/tasks/sources/wecom/projects/sync',
                   expected_status=401)

    @allure.story('企微同步')
    @allure.title('正常：同步企微项目')
    @pytest.mark.api
    async def test_wecom_sync_ok(self, mock_api_client, mock_auth_header):
        """正常流程：同步企微项目"""
        await _api(mock_api_client, 'post', '/api/tasks/sources/wecom/projects/sync',
                   headers=mock_auth_header, expected_status=200,
                   expected_fields={'code': 200})
