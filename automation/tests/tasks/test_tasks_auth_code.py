"""系统任务 - 权限用例（G2 补强）。

注意：mock 的鉴权只校验 token 存在性（_require_auth），不校验角色语义。
本文件用例覆盖"无 token 被拒"的契约；角色越权由真实后端场景车道验收，
避免把 mock 的空鉴权当成"权限已覆盖"。
"""

import allure
import pytest

from automation.src.assertions import assert_status_code
from automation.src.assertions.report import flush_assert_attachment


async def _api(client, method: str, path: str, headers=None,
               expected_status: int | None = None, **kwargs):
    with allure.step(f'{method.upper()} {path}'):
        r = await client.request(method, path, headers=headers, **kwargs)
        if expected_status is not None:
            assert_status_code(r, expected_status)
        flush_assert_attachment()
        return r


@allure.feature('系统任务')
class TestTaskPermissions:
    """无 token 访问受保护端点（mock 仅验 token 存在性）"""

    @allure.story('权限')
    @allure.title('权限：无 token 创建工单')
    @pytest.mark.api
    async def test_create_task_without_token(self, mock_api_client):
        """权限：无 token 建单返回 401"""
        await _api(mock_api_client, 'post', '/api/tasks',
                   json={'title': '无权建单', 'description': 'test'}, expected_status=401)

    @allure.story('权限')
    @allure.title('权限：无 token 关键词筛选')
    @pytest.mark.api
    async def test_filter_without_token(self, mock_api_client):
        """权限：无 token 筛选返回 401"""
        await _api(mock_api_client, 'post', '/api/tasks/filter', json={}, expected_status=401)

    @allure.story('权限')
    @allure.title('权限：无 token 催办')
    @pytest.mark.api
    async def test_cuiban_without_token(self, mock_api_client):
        """权限：无 token 催办返回 401"""
        await _api(mock_api_client, 'post', '/api/tasks/cuiban-notification',
                   json={'task_id': 1}, expected_status=401)

    @allure.story('权限')
    @allure.title('权限：无 token 查询可指派人员')
    @pytest.mark.api
    async def test_assignable_users_without_token(self, mock_api_client):
        """权限：无 token 查可指派人员返回 401"""
        await _api(mock_api_client, 'get', '/api/tasks/assignable-users', expected_status=401)

