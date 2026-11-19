"""系统任务 - 文档/关联/阶段接口用例（G3 补强）。

覆盖 mock 已实现但原零覆盖的接口：
spec-doc / operation-logs / relations / respond / complete-step。
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


async def _new_task(client, headers) -> int:
    """创建一个任务并返回 id，作为各接口的测试前置。"""
    r = await _api(client, 'post', '/api/tasks', headers=headers,
                   json={'title': '文档链路测试任务', 'description': '文档接口测试前置'}, expected_status=200)
    return r.json()['id']


@allure.feature('系统任务')
class TestTaskDocuments:
    """规格文档 / 操作日志 / 关联 / 阶段"""

    @allure.story('规格文档')
    @allure.title('正常：获取规格文档默认值')
    @pytest.mark.api
    async def test_spec_doc_get_default(self, mock_api_client, mock_auth_header):
        """正常流程：未写过的规格文档返回默认值"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'get', f'/api/tasks/{tid}/spec-doc', headers=mock_auth_header,
                   expected_status=200, expected_fields={'content': '', 'revision': 1})

    @allure.story('规格文档')
    @allure.title('正常：更新规格文档 revision 递增')
    @pytest.mark.api
    async def test_spec_doc_put_revision(self, mock_api_client, mock_auth_header):
        """正常流程：更新文档后 revision 递增"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'put', f'/api/tasks/{tid}/spec-doc', headers=mock_auth_header,
                   json={'content': '完整问题描述'}, expected_status=200,
                   expected_fields={'content': '完整问题描述', 'revision': 2})

    @allure.story('规格文档')
    @allure.title('数据校验：更新规格文档缺 content')
    @pytest.mark.api
    async def test_spec_doc_put_empty(self, mock_api_client, mock_auth_header):
        """数据校验：content 为空返回 422"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'put', f'/api/tasks/{tid}/spec-doc', headers=mock_auth_header,
                   json={'content': ''}, expected_status=422)

    @allure.story('操作日志')
    @allure.title('正常：获取操作日志（空）')
    @pytest.mark.api
    async def test_operation_logs_empty(self, mock_api_client, mock_auth_header):
        """正常流程：新任务的操作日志为空列表"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'get', f'/api/tasks/{tid}/operation-logs', headers=mock_auth_header,
                   expected_status=200, expected_fields={'items': [], 'total': 0})

    @allure.story('工单关联')
    @allure.title('正常：创建关联')
    @pytest.mark.api
    async def test_relation_create(self, mock_api_client, mock_auth_header):
        """正常流程：创建工单关联返回关联记录"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'post', f'/api/tasks/{tid}/relations', headers=mock_auth_header,
                   json={'target_task_id': 999, 'relation_type': 'predecessor'},
                   expected_status=200,
                   expected_fields={'source_task_id': tid, 'target_task_id': 999})

    @allure.story('工单关联')
    @allure.title('数据校验：创建关联缺 target_task_id')
    @pytest.mark.api
    async def test_relation_create_missing_target(self, mock_api_client, mock_auth_header):
        """数据校验：缺 target_task_id 返回 422"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'post', f'/api/tasks/{tid}/relations', headers=mock_auth_header,
                   json={}, expected_status=422)

    @allure.story('工单关联')
    @allure.title('正常：获取关联列表与关系树')
    @pytest.mark.api
    async def test_relation_list_and_tree(self, mock_api_client, mock_auth_header):
        """正常流程：关联列表与关系树结构正确"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'get', f'/api/tasks/{tid}/relations', headers=mock_auth_header,
                   expected_status=200, expected_fields={'total': 0})
        await _api(mock_api_client, 'get', f'/api/tasks/{tid}/relations/tree', headers=mock_auth_header,
                   expected_status=200, expected_fields={'task_id': tid})

    @allure.story('阶段响应')
    @allure.title('正常：响应阶段设置协商状态')
    @pytest.mark.api
    async def test_task_respond(self, mock_api_client, mock_auth_header):
        """正常流程：响应阶段后 curr_step_agreed=True"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'post', f'/api/tasks/{tid}/respond', headers=mock_auth_header,
                   json={'curr_step_id': 1}, expected_status=200,
                   expected_fields={'curr_step_id': 1, 'curr_step_agreed': True})

    @allure.story('阶段完成')
    @allure.title('异常：未协商一致不可完成阶段')
    @pytest.mark.api
    async def test_complete_step_not_agreed(self, mock_api_client, mock_auth_header):
        """异常流程：未协商完成时完成阶段返回 400"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'post', f'/api/tasks/{tid}/complete-step', headers=mock_auth_header,
                   json={'next_step_id': 2}, expected_status=400)

    @allure.story('阶段完成')
    @allure.title('正常：协商一致后完成阶段')
    @pytest.mark.api
    async def test_complete_step_ok(self, mock_api_client, mock_auth_header):
        """正常流程：先响应协商再完成阶段，进入下一步"""
        tid = await _new_task(mock_api_client, mock_auth_header)
        await _api(mock_api_client, 'post', f'/api/tasks/{tid}/respond', headers=mock_auth_header,
                   json={'curr_step_id': 1}, expected_status=200)
        await _api(mock_api_client, 'post', f'/api/tasks/{tid}/complete-step', headers=mock_auth_header,
                   json={'next_step_id': 2}, expected_status=200,
                   expected_fields={'curr_step_id': 2, 'curr_step_agreed': False})
