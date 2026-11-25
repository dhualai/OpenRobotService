"""系统任务 - 异常路径补强用例（G4）。

补齐 CRUD 中未覆盖的异常分支：更新/删除/指派不存在资源、创建缺必填字段。
"""

import allure
import pytest

from automation.src.assertions import assert_status_code
from automation.src.assertions.report import flush_assert_attachment
from automation.src.reporting.api_step import api_step


async def _api(client, method: str, path: str, headers=None,
               expected_status: int | None = None, **kwargs):
    """委托统一上报：自动附加入参、出参、断言。"""
    return await api_step(client, method, path, headers=headers,
                          expected_status=expected_status, **kwargs)


@allure.feature('系统任务')
class TestTaskNegative:
    """工单 CRUD 异常路径"""

    @allure.story('更新工单')
    @allure.title('异常：更新不存在的工单')
    @pytest.mark.api
    async def test_update_task_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：更新不存在工单返回 404"""
        await _api(mock_api_client, 'put', '/api/tasks/99999', headers=mock_auth_header,
                   json={'title': 'x'}, expected_status=404)

    @allure.story('删除工单')
    @allure.title('异常：删除不存在的工单')
    @pytest.mark.api
    async def test_delete_task_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：删除不存在工单返回 404"""
        await _api(mock_api_client, 'delete', '/api/tasks/99999', headers=mock_auth_header,
                   expected_status=404)

    @allure.story('指派工单')
    @allure.title('异常：指派不存在的工单')
    @pytest.mark.api
    async def test_assign_task_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：指派不存在工单返回 404"""
        await _api(mock_api_client, 'patch', '/api/tasks/99999/assign', headers=mock_auth_header,
                   json={'assigned_to': 'engineer'}, expected_status=404)

    @allure.story('创建工单')
    @allure.title('数据校验：缺 description')
    @pytest.mark.api
    async def test_create_task_missing_description(self, mock_api_client, mock_auth_header):
        """数据校验：缺 description 返回 422"""
        await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header,
                   json={'title': '只有标题'}, expected_status=422)
