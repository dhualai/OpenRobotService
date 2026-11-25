"""系统任务模块 - 代码驱动用例（自由函数模式试点）。

与 test_tasks.py（Excel 数据驱动）并行，用于验证代码驱动迁移效果。
每条用例对应 api-test-cases.xlsx 中的一条任务用例（TASK-xxx）。
"""
import allure
import pytest
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment
from automation.src.config.credentials import role_credentials
from automation.src.reporting.api_step import api_step
from automation.src.reporting.business_scenarios import SCENARIO_LABEL, USP_USAGE_ISSUE

async def _auth_for_role(client, role: str) -> dict:
    username, password = role_credentials(role)
    r = await client.post('/api/auth/login', json={'username': username, 'password': password})
    return {'Authorization': f"Bearer {r.json()['access_token']}"}

async def _api(client, method: str, path: str, step: str='', headers=None, expected_status: int | None=None, expected_fields: dict | None=None, **kwargs):
    """委托统一上报：自动附加入参、出参、断言。"""
    return await api_step(client, method, path, step=step, headers=headers,
                          expected_status=expected_status,
                          expected_fields=expected_fields, **kwargs)

@allure.feature('系统任务')
class TestTaskCrud:
    """工单 CRUD：创建/查询/更新/删除"""

    @allure.story('创建工单')
    @allure.title('正常：基础字段创建')
    @pytest.mark.api
    async def test_create_task_basic(self, mock_api_client, mock_auth_header):
        """正常流程：基础字段创建"""
        r = await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header, json={'title': 'Error E1001', 'description': 'Robot fault'}, expected_status=200, expected_fields={'status': 'pending'})

    @allure.story('创建工单')
    @allure.title('正常：全字段创建')
    @pytest.mark.api
    async def test_create_task_full_fields(self, mock_api_client, mock_auth_header):
        """正常流程：全字段创建"""
        r = await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header, json={'title': 'Full task', 'description': 'Full', 'priority': 'high', 'tags': ['urgent']}, expected_status=200, expected_fields={'priority': 'high'})

    @allure.story('创建工单')
    @allure.title('数据校验：缺 title')
    @pytest.mark.api
    async def test_create_task_missing_title(self, mock_api_client, mock_auth_header):
        """数据校验：缺 title"""
        r = await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header, json={}, expected_status=422)

    @allure.story('创建工单')
    @allure.title('数据校验：title 为空')
    @pytest.mark.api
    async def test_create_task_empty_title(self, mock_api_client, mock_auth_header):
        """数据校验：title 为空"""
        r = await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header, json={'title': ''}, expected_status=422)

    @allure.story('创建工单')
    @allure.title('正常：type=bug 创建')
    @pytest.mark.api
    async def test_create_task_type_bug(self, mock_api_client, mock_auth_header):
        """正常流程：type=bug 创建"""
        r = await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header, json={'title': 'Bug 任务', 'description': 'Bug 描述', 'ticket_type': 'bug'}, expected_status=200, expected_fields={'ticket_type': 'bug'})

    @allure.story('创建工单')
    @allure.title('正常：type=requirement 创建')
    @pytest.mark.api
    async def test_create_task_type_requirement(self, mock_api_client, mock_auth_header):
        """正常流程：type=requirement 创建"""
        r = await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header, json={'title': '需求任务', 'description': '需求描述', 'ticket_type': 'requirement'}, expected_status=200, expected_fields={'ticket_type': 'requirement'})

    @allure.story('创建工单')
    @allure.title('正常：type=support 创建')
    @pytest.mark.api
    async def test_create_task_type_support(self, mock_api_client, mock_auth_header):
        """正常流程：type=support 创建"""
        r = await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header, json={'title': '支持任务', 'description': '支持描述', 'ticket_type': 'support'}, expected_status=200, expected_fields={'ticket_type': 'support'})

    @allure.story('创建工单')
    @allure.title('数据校验：type 非法')
    @pytest.mark.api
    async def test_create_task_type_invalid(self, mock_api_client, mock_auth_header):
        """数据校验：type 非法"""
        r = await _api(mock_api_client, 'post', '/api/tasks', headers=mock_auth_header, json={'title': '非法类型任务', 'description': '描述', 'ticket_type': 'invalid'}, expected_status=400)

    @allure.story('查询工单')
    @allure.title('正常：任务列表')
    @pytest.mark.api
    async def test_list_tasks(self, mock_api_client, mock_auth_header):
        """正常流程：任务列表"""
        r = await _api(mock_api_client, 'get', '/api/tasks', headers=mock_auth_header, expected_status=200,
                       expected_fields={'page': 1, 'size': 10})

    @allure.story('查询工单')
    @allure.title('正常：分页列表')
    @pytest.mark.api
    async def test_list_tasks_pagination(self, mock_api_client, mock_auth_header):
        """正常流程：分页列表"""
        # 注：本用例未传分页参数（沿用原实现），mock 默认 page=1/size=10
        r = await _api(mock_api_client, 'get', '/api/tasks', headers=mock_auth_header, expected_status=200,
                       expected_fields={'page': 1, 'size': 10})

    @allure.story('查询工单')
    @allure.title('正常：分页 size=200')
    @pytest.mark.api
    async def test_list_tasks_size_200(self, mock_api_client, mock_auth_header):
        """正常流程：分页 size=200（mock 暂不校验）"""
        r = await _api(mock_api_client, 'get', '/api/tasks?size=200', headers=mock_auth_header, expected_status=200,
                       expected_fields={'size': 200})

    @allure.story('查询工单')
    @allure.title('正常：任务详情')
    @pytest.mark.api
    async def test_get_task_detail(self, mock_api_client, mock_auth_header):
        """正常流程：任务详情"""
        r = await _api(mock_api_client, 'get', '/api/tasks/1', headers=mock_auth_header, expected_status=200, expected_fields={'id': 1})

    @allure.story('查询工单')
    @allure.title('异常：任务不存在')
    @pytest.mark.api
    async def test_get_task_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：任务不存在"""
        r = await _api(mock_api_client, 'get', '/api/tasks/99999', headers=mock_auth_header, expected_status=404)

    @allure.story('更新工单')
    @allure.title('正常：更新任务')
    @pytest.mark.api
    async def test_update_task(self, mock_api_client, mock_auth_header):
        """正常流程：更新任务"""
        r = await _api(mock_api_client, 'put', '/api/tasks/1', headers=mock_auth_header, json={'title': 'Updated'}, expected_status=200, expected_fields={'title': 'Updated'})

    @allure.story('删除工单')
    @allure.title('正常：删除任务')
    @pytest.mark.api
    async def test_delete_task(self, mock_api_client, mock_auth_header):
        """正常流程：删除任务"""
        r = await _api(mock_api_client, 'delete', '/api/tasks/1', headers=mock_auth_header, expected_status=204)

@allure.feature('系统任务')
class TestTaskStatus:
    """工单状态流转"""

    @allure.story('状态流转')
    @allure.title('状态流转：合法流转')
    @pytest.mark.api
    async def test_status_transition_valid(self, mock_api_client, mock_auth_header):
        """状态流转：合法状态流转"""
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/status', headers=mock_auth_header, json={'status': 'in_progress'}, expected_status=200, expected_fields={'status': 'in_progress'})

    @allure.story('状态流转')
    @allure.title('状态流转：非法流转 closed')
    @pytest.mark.api
    async def test_status_transition_closed_invalid(self, mock_api_client, mock_auth_header):
        """状态流转：非法状态流转"""
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/status', headers=mock_auth_header, json={'status': 'closed'}, expected_status=400)

    @allure.story('状态流转')
    @allure.title('状态流转：closed 不可重开')
    @pytest.mark.api
    async def test_status_transition_closed_reopen(self, mock_api_client, mock_auth_header):
        """状态流转：closed 不可重开"""
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/status', headers=mock_auth_header, json={'status': 'closed'}, expected_status=400)

@allure.feature('系统任务')
class TestTaskAssign:
    """工单指派"""

    @allure.story('指派工单')
    @allure.title('正常：指派工程师')
    @pytest.mark.api
    async def test_assign_engineer(self, mock_api_client, mock_auth_header):
        """正常流程：指派工程师"""
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/assign', headers=mock_auth_header, json={'assigned_to': 'engineer-01'}, expected_status=200, expected_fields={'assigned_to': 'engineer-01'})

    @allure.story('指派工单')
    @allure.title('正常：工程师接单')
    @pytest.mark.api
    async def test_assign_status_in_progress(self, mock_api_client, mock_auth_header):
        """正常流程：工程师接单"""
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/assign', headers=mock_auth_header, json={'assigned_to': 'engineer'}, expected_status=200, expected_fields={'assigned_to': 'engineer', 'status': 'in_progress'})

    @allure.story('指派工单')
    @allure.title('正常：admin 转单')
    @pytest.mark.api
    async def test_assign_admin_transfer(self, mock_api_client, mock_auth_header):
        """正常流程：admin 转单给工程师"""
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/assign', headers=mock_auth_header, json={'assigned_to': 'engineer-02'}, expected_status=200, expected_fields={'assigned_to': 'engineer-02'})

    @allure.story('指派工单')
    @allure.title('正常：清除指派')
    @pytest.mark.api
    async def test_assign_clear(self, mock_api_client, mock_auth_header):
        """正常流程：清除指派（mock 暂不实现状态校验）"""
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/assign', headers=mock_auth_header, json={'assigned_to': ''}, expected_status=200, expected_fields={'assigned_to': None})

    @allure.story('指派工单')
    @allure.title('正常：接单（mock 暂不实现冲突检测）')
    @pytest.mark.api
    async def test_assign_engineer_no_conflict(self, mock_api_client, mock_auth_header):
        """正常流程：接单（mock 暂不实现冲突检测）"""
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/assign', headers=mock_auth_header, json={'assigned_to': 'engineer'}, expected_status=200, expected_fields={'status': 'in_progress'})

    @allure.story('指派工单')
    @allure.title('正常：customer 接单')
    @pytest.mark.api
    async def test_assign_customer_role(self, mock_api_client, mock_auth_header):
        """正常流程：customer 接单"""
        headers = await _auth_for_role(mock_api_client, 'customer')
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/assign', headers=headers, json={'assigned_to': 'customer'}, expected_status=200, expected_fields={'assigned_to': 'customer', 'status': 'in_progress'})

    @allure.story('指派工单')
    @allure.title('正常：engineer 接单')
    @pytest.mark.api
    async def test_assign_engineer_role(self, mock_api_client, mock_auth_header):
        """正常流程：engineer 接单"""
        headers = await _auth_for_role(mock_api_client, 'engineer')
        r = await _api(mock_api_client, 'patch', '/api/tasks/1/assign', headers=headers, json={'assigned_to': 'engineer'}, expected_status=200, expected_fields={'assigned_to': 'engineer', 'status': 'in_progress'})

@allure.feature('系统任务')
class TestTaskComments:
    """工单评论"""

    @allure.story('评论')
    @allure.title('正常：添加评论')
    @pytest.mark.api
    async def test_comment_create(self, mock_api_client, mock_auth_header):
        """正常流程：添加评论"""
        r = await _api(mock_api_client, 'post', '/api/tasks/1/comments', headers=mock_auth_header, json={'content': 'Checking...'}, expected_status=201)

    @allure.story('评论')
    @allure.title('异常：评论不存在的任务')
    @pytest.mark.api
    async def test_comment_task_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：评论不存在的任务"""
        r = await _api(mock_api_client, 'post', '/api/tasks/99999/comments', headers=mock_auth_header, json={'content': 'x'}, expected_status=404)

@allure.feature('系统任务')
class TestTaskFilterStats:
    """工单筛选与统计"""

    @allure.story('筛选')
    @allure.title('正常：关键词筛选')
    @pytest.mark.api
    async def test_filter_keyword(self, mock_api_client, mock_auth_header):
        """正常流程：关键词筛选"""
        # 响应 {items,total}，total 随测试数据动态变化，状态码即断言
        r = await _api(mock_api_client, 'post', '/api/tasks/filter', headers=mock_auth_header, json={'keyword': 'error'}, expected_status=200)

    @allure.story('统计')
    @allure.title('正常：状态统计')
    @pytest.mark.api
    async def test_stats_overview(self, mock_api_client, mock_auth_header):
        """正常流程：状态统计"""
        # 响应 {total,by_status,by_priority}，total 随测试数据动态变化，状态码即断言
        r = await _api(mock_api_client, 'get', '/api/tasks/stats/overview', headers=mock_auth_header, expected_status=200)

@allure.feature('系统任务')
class TestTaskAi:
    """AI 能力"""

    @allure.story('AI 指派')
    @allure.title('AI：AI 自动指派')
    @pytest.mark.api
    async def test_ai_assign(self, mock_api_client, mock_auth_header):
        """AI：AI 自动指派"""
        r = await _api(mock_api_client, 'post', '/api/tasks/1/ai-assign', headers=mock_auth_header, expected_status=200,
                       expected_fields={'task_id': 1, 'assigned_to': 'engineer-01', 'method': 'auto'})

    @allure.story('AI 分析')
    @allure.title('Redis：AI 分析缓存')
    @pytest.mark.api
    async def test_ai_analyze_cache(self, mock_api_client, mock_auth_header):
        """Redis：AI 分析缓存"""
        r = await _api(mock_api_client, 'post', '/api/ai/task/analyze', headers=mock_auth_header, json={'task_id': 1}, expected_status=200, expected_fields={'task_id': 1})

@allure.feature('系统任务')
class TestTaskFlow:
    """全链路流程"""

    @allure.story('提单与接单')
    @allure.title('提单人提交 USP 使用问题，接单人接单')
    @allure.label(SCENARIO_LABEL, 'incident-create')
    @pytest.mark.api
    async def test_submit_usp_usage_issue_and_engineer_accepts(self, mock_api_client):
        submitter, _ = role_credentials('customer')
        submitter_headers = await _auth_for_role(mock_api_client, 'customer')
        created = await _api(
            mock_api_client, 'post', '/api/tasks', step='提单人提交 USP 使用问题',
            headers=submitter_headers,
            json={'title': USP_USAGE_ISSUE, 'description': USP_USAGE_ISSUE, 'ticket_type': 'support'},
            expected_status=200,
            expected_fields={
                'title': USP_USAGE_ISSUE, 'description': USP_USAGE_ISSUE,
                'created_by': submitter, 'status': 'pending',
            },
            business_evidence=('incident-create', 'create'),
        )
        ticket_id = created.json()['id']
        assert isinstance(ticket_id, int) and ticket_id > 0

        engineer, _ = role_credentials('engineer')
        engineer_headers = await _auth_for_role(mock_api_client, 'engineer')
        await _api(
            mock_api_client, 'patch', f'/api/tasks/{ticket_id}/assign',
            step='接单人接收同一张工单', headers=engineer_headers,
            json={'assigned_to': engineer}, expected_status=200,
            expected_fields={'id': ticket_id, 'assigned_to': engineer, 'status': 'in_progress'},
            business_evidence=('incident-create', 'accept'),
        )

    @allure.story('全链路')
    @allure.title('全链路：建单→处理中→已解决→已关闭')
    @pytest.mark.api
    async def test_full_flow_create_to_closed(self, mock_api_client, mock_auth_header):
        """正常流程：建单→处理中→已解决→已关闭 全链路"""
        r = await _api(mock_api_client, 'post', '/api/tasks', step='Step 1: 创建工单', headers=mock_auth_header, json={'title': '全链路验证任务', 'description': 'flow demo', 'ticket_type': 'bug'}, expected_status=200, expected_fields={'title': '全链路验证任务'})
        task_id = r.json()['id']
        r = await _api(mock_api_client, 'patch', f'/api/tasks/{task_id}/status', step='Step 2: 流转为处理中', headers=mock_auth_header, json={'status': 'in_progress'}, expected_status=200)
        r = await _api(mock_api_client, 'patch', f'/api/tasks/{task_id}/status', step='Step 3: 流转为已解决', headers=mock_auth_header, json={'status': 'resolved'}, expected_status=200)
        r = await _api(mock_api_client, 'patch', f'/api/tasks/{task_id}/status', step='Step 4: 流转为已关闭', headers=mock_auth_header, json={'status': 'closed'}, expected_status=200, expected_fields={'status': 'closed'})
