"""我要摇人模块 - 代码驱动用例（自由函数模式）。

与 test_call.py（Excel 数据驱动）并行，代码驱动迁移版。
"""

import allure
import pytest
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment

_ROLE_CREDENTIALS = {'customer': ('customer', 'cust123'), 'engineer': ('engineer', 'eng123')}


async def _auth_for_role(client, role: str) -> dict:
    username, password = _ROLE_CREDENTIALS[role]
    r = await client.post('/api/auth/login', json={'username': username, 'password': password})
    return {'Authorization': f"Bearer {r.json()['access_token']}"}


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


@allure.feature('我要摇人')
class TestConversation:
    """会话 CRUD"""

    @allure.story('会话')
    @allure.title('正常：创建会话')
    @pytest.mark.api
    async def test_create_conversation(self, mock_api_client, mock_auth_header):
        """正常流程：创建会话"""
        await _api(mock_api_client, 'post', '/api/call/conversations', headers=mock_auth_header,
                   json={'title': 'New conversation'}, expected_status=200,
                   expected_fields={'title': 'New conversation'})

    @allure.story('会话')
    @allure.title('正常：会话列表')
    @pytest.mark.api
    async def test_list_conversations(self, mock_api_client, mock_auth_header):
        """正常流程：会话列表"""
        await _api(mock_api_client, 'get', '/api/call/conversations', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('会话')
    @allure.title('正常：会话详情')
    @pytest.mark.api
    async def test_get_conversation_detail(self, mock_api_client, mock_auth_header):
        """正常流程：会话详情"""
        await _api(mock_api_client, 'get', '/api/call/conversations/1', headers=mock_auth_header,
                   expected_status=200, expected_fields={'id': 1})

    @allure.story('会话')
    @allure.title('异常：会话不存在')
    @pytest.mark.api
    async def test_get_conversation_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：会话不存在"""
        await _api(mock_api_client, 'get', '/api/call/conversations/99999', headers=mock_auth_header,
                   expected_status=404)

    @allure.story('会话')
    @allure.title('正常：更新会话')
    @pytest.mark.api
    async def test_update_conversation(self, mock_api_client, mock_auth_header):
        """正常流程：更新会话"""
        await _api(mock_api_client, 'put', '/api/call/conversations/1', headers=mock_auth_header,
                   json={'title': 'Updated conv'}, expected_status=200,
                   expected_fields={'id': 1, 'title': 'Updated conv'})

    @allure.story('会话')
    @allure.title('异常：更新会话不存在')
    @pytest.mark.api
    async def test_update_conversation_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：会话不存在"""
        await _api(mock_api_client, 'put', '/api/call/conversations/99999', headers=mock_auth_header,
                   json={'title': 'x'}, expected_status=404)

    @allure.story('会话')
    @allure.title('数据校验：title 为空')
    @pytest.mark.api
    async def test_update_conversation_empty_title(self, mock_api_client, mock_auth_header):
        """数据校验：title 为空"""
        await _api(mock_api_client, 'put', '/api/call/conversations/1', headers=mock_auth_header,
                   json={'title': ''}, expected_status=422)

    @allure.story('会话')
    @allure.title('正常：删除会话')
    @pytest.mark.api
    async def test_delete_conversation(self, mock_api_client, mock_auth_header):
        """正常流程：删除会话"""
        await _api(mock_api_client, 'delete', '/api/call/conversations/1', headers=mock_auth_header,
                   expected_status=204)

    @allure.story('会话')
    @allure.title('异常：删除会话不存在')
    @pytest.mark.api
    async def test_delete_conversation_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：会话不存在"""
        await _api(mock_api_client, 'delete', '/api/call/conversations/99999', headers=mock_auth_header,
                   expected_status=404)


@allure.feature('我要摇人')
class TestQa:
    """AI 问答"""

    @allure.story('问答')
    @allure.title('正常：提问')
    @pytest.mark.api
    async def test_ask_question(self, mock_api_client, mock_auth_header):
        """正常流程：提问"""
        await _api(mock_api_client, 'post', '/api/call/qa/ask', headers=mock_auth_header,
                   json={'question': 'How to reset?'}, expected_status=200,
                   expected_fields={'success': True})

    @allure.story('问答')
    @allure.title('数据校验：空问题')
    @pytest.mark.api
    async def test_ask_empty_question(self, mock_api_client, mock_auth_header):
        """数据校验：空问题"""
        await _api(mock_api_client, 'post', '/api/call/qa/ask', headers=mock_auth_header,
                   json={'question': ''}, expected_status=422)

    @allure.story('问答')
    @allure.title('AI：流式问答')
    @pytest.mark.api
    async def test_ask_stream(self, mock_api_client, mock_auth_header):
        """AI：流式问答"""
        await _api(mock_api_client, 'post', '/api/call/qa/ask/stream', headers=mock_auth_header,
                   json={'question': 'Tell me'}, expected_status=200)

    @allure.story('问答')
    @allure.title('AI：AI 诊断超时降级')
    @pytest.mark.api
    async def test_ask_ai_timeout_fallback(self, mock_api_client, mock_auth_header):
        """AI：AI 诊断超时降级"""
        await _api(mock_api_client, 'post', '/api/call/qa/ask', headers=mock_auth_header,
                   json={'question': 'AI超时测试'}, expected_status=200,
                   expected_fields={'success': True})


@allure.feature('我要摇人')
class TestMessages:
    """消息"""

    @allure.story('消息')
    @allure.title('正常：发送消息')
    @pytest.mark.api
    async def test_send_message(self, mock_api_client, mock_auth_header):
        """正常流程：发送消息"""
        await _api(mock_api_client, 'post', '/api/call/messages', headers=mock_auth_header,
                   json={'content': 'Test message'}, expected_status=200)

    @allure.story('消息')
    @allure.title('正常：消息列表')
    @pytest.mark.api
    async def test_list_messages(self, mock_api_client, mock_auth_header):
        """正常流程：消息列表"""
        await _api(mock_api_client, 'get', '/api/call/messages?conversation_id=1', headers=mock_auth_header,
                   expected_status=200, expected_fields={'total': 1})

    @allure.story('消息')
    @allure.title('数据校验：缺 conversation_id')
    @pytest.mark.api
    async def test_list_messages_missing_conv(self, mock_api_client, mock_auth_header):
        """数据校验：缺 conversation_id"""
        await _api(mock_api_client, 'get', '/api/call/messages', headers=mock_auth_header,
                   expected_status=422)

    @allure.story('消息')
    @allure.title('正常：消息详情')
    @pytest.mark.api
    async def test_get_message_detail(self, mock_api_client, mock_auth_header):
        """正常流程：消息详情"""
        await _api(mock_api_client, 'get', '/api/call/messages/1', headers=mock_auth_header,
                   expected_status=200, expected_fields={'id': 1})

    @allure.story('消息')
    @allure.title('异常：消息不存在')
    @pytest.mark.api
    async def test_get_message_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：消息不存在"""
        await _api(mock_api_client, 'get', '/api/call/messages/99999', headers=mock_auth_header,
                   expected_status=404)

    @allure.story('消息')
    @allure.title('正常：更新消息')
    @pytest.mark.api
    async def test_update_message(self, mock_api_client, mock_auth_header):
        """正常流程：更新消息"""
        await _api(mock_api_client, 'put', '/api/call/messages/1', headers=mock_auth_header,
                   json={'content': 'Updated'}, expected_status=200,
                   expected_fields={'id': 1, 'content': 'Updated'})

    @allure.story('消息')
    @allure.title('异常：更新消息不存在')
    @pytest.mark.api
    async def test_update_message_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：消息不存在"""
        await _api(mock_api_client, 'put', '/api/call/messages/99999', headers=mock_auth_header,
                   json={'content': 'x'}, expected_status=404)

    @allure.story('消息')
    @allure.title('正常：删除消息')
    @pytest.mark.api
    async def test_delete_message(self, mock_api_client, mock_auth_header):
        """正常流程：删除消息"""
        await _api(mock_api_client, 'delete', '/api/call/messages/1', headers=mock_auth_header,
                   expected_status=204)

    @allure.story('消息')
    @allure.title('异常：删除消息不存在')
    @pytest.mark.api
    async def test_delete_message_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：消息不存在"""
        await _api(mock_api_client, 'delete', '/api/call/messages/99999', headers=mock_auth_header,
                   expected_status=404)


@allure.feature('我要摇人')
class TestMyTasks:
    """我的任务"""

    @allure.story('我的任务')
    @allure.title('正常：我的任务列表')
    @pytest.mark.api
    async def test_my_tasks_list(self, mock_api_client, mock_auth_header):
        """正常流程：我的任务列表"""
        await _api(mock_api_client, 'get', '/api/call/my-tasks/', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('我的任务')
    @allure.title('正常：创建我的任务')
    @pytest.mark.api
    async def test_my_tasks_create(self, mock_api_client, mock_auth_header):
        """正常流程：创建我的任务"""
        await _api(mock_api_client, 'post', '/api/call/my-tasks/', headers=mock_auth_header,
                   json={'title': 'My task'}, expected_status=200)

    @allure.story('我的任务')
    @allure.title('正常：我的任务详情')
    @pytest.mark.api
    async def test_my_tasks_detail(self, mock_api_client, mock_auth_header):
        """正常流程：我的任务详情"""
        await _api(mock_api_client, 'get', '/api/call/my-tasks/1', headers=mock_auth_header,
                   expected_status=200, expected_fields={'id': 1})


@allure.feature('我要摇人')
class TestReminder:
    """催办"""

    @allure.story('催办')
    @allure.title('正常：催办')
    @pytest.mark.api
    async def test_cuiban(self, mock_api_client, mock_auth_header):
        """正常流程：催办"""
        await _api(mock_api_client, 'post', '/api/tasks/cuiban-notification', headers=mock_auth_header,
                   json={'task_id': 1}, expected_status=200, expected_fields={'success': True})

    @allure.story('催办')
    @allure.title('数据校验：缺少 task_id')
    @pytest.mark.api
    async def test_cuiban_missing_task_id(self, mock_api_client, mock_auth_header):
        """数据校验：缺少 task_id"""
        await _api(mock_api_client, 'post', '/api/tasks/cuiban-notification', headers=mock_auth_header,
                   expected_status=422)

    @allure.story('催办')
    @allure.title('异常：催办任务不存在')
    @pytest.mark.api
    async def test_cuiban_task_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：催办任务不存在"""
        await _api(mock_api_client, 'post', '/api/tasks/cuiban-notification', headers=mock_auth_header,
                   json={'task_id': 999}, expected_status=404)

    @allure.story('催办')
    @allure.title('正常：催办（mock 暂不实现限流）')
    @pytest.mark.api
    async def test_cuiban_no_ratelimit(self, mock_api_client, mock_auth_header):
        """正常流程：催办（mock 暂不实现限流）"""
        await _api(mock_api_client, 'post', '/api/tasks/cuiban-notification', headers=mock_auth_header,
                   json={'task_id': 1}, expected_status=200, expected_fields={'success': True})

    @allure.story('催办')
    @allure.title('数据校验：催办备注超长')
    @pytest.mark.api
    async def test_cuiban_long_note(self, mock_api_client, mock_auth_header):
        """数据校验：催办备注超长"""
        await _api(mock_api_client, 'post', '/api/tasks/cuiban-notification', headers=mock_auth_header,
                   json={'task_id': 1, 'note': '催办提醒'}, expected_status=200)


@allure.feature('我要摇人')
class TestEscalation:
    """升级选人"""

    @allure.story('升级选人')
    @allure.title('正常：升级选人列表')
    @pytest.mark.api
    async def test_assignable_users(self, mock_api_client, mock_auth_header):
        """正常流程：升级选人列表"""
        await _api(mock_api_client, 'get', '/api/tasks/assignable-users', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('升级选人')
    @allure.title('正常：返回可选人员')
    @pytest.mark.api
    async def test_assignable_users_available(self, mock_api_client, mock_auth_header):
        """正常流程：返回可选人员"""
        await _api(mock_api_client, 'get', '/api/tasks/assignable-users', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('升级选人')
    @allure.title('数据校验：project_id 非法')
    @pytest.mark.api
    async def test_assignable_users_invalid_project(self, mock_api_client, mock_auth_header):
        """数据校验：project_id 非法"""
        await _api(mock_api_client, 'get', '/api/tasks/assignable-users?project_id=abc',
                   headers=mock_auth_header, expected_status=400)

    @allure.story('升级选人')
    @allure.title('正常：无可用人员')
    @pytest.mark.api
    async def test_assignable_users_empty(self, mock_api_client, mock_auth_header):
        """正常流程：无可用人员"""
        await _api(mock_api_client, 'get', '/api/tasks/assignable-users', headers=mock_auth_header,
                   expected_status=200)


@allure.feature('我要摇人')
class TestAuth:
    """认证"""

    @allure.story('认证')
    @allure.title('异常：密码错误')
    @pytest.mark.api
    async def test_login_wrong_password(self, mock_api_client):
        """异常流程：密码错误"""
        await _api(mock_api_client, 'post', '/api/auth/login',
                   json={'username': 'testadmin', 'password': 'wrong'}, expected_status=401)

    @allure.story('认证')
    @allure.title('正常：获取当前用户')
    @pytest.mark.api
    async def test_me(self, mock_api_client, mock_auth_header):
        """正常流程：获取当前用户"""
        await _api(mock_api_client, 'get', '/api/auth/me', headers=mock_auth_header,
                   expected_status=200, expected_fields={'username': 'testadmin'})

    @allure.story('认证')
    @allure.title('权限：无效 token')
    @pytest.mark.api
    async def test_me_invalid_token(self, mock_api_client):
        """权限：无效 token"""
        await _api(mock_api_client, 'get', '/api/auth/me', expected_status=401)

    @allure.story('认证')
    @allure.title('数据校验：空 password')
    @pytest.mark.api
    async def test_login_empty_password(self, mock_api_client):
        """数据校验：空 password"""
        await _api(mock_api_client, 'post', '/api/auth/login',
                   json={'username': 'testadmin', 'password': ''}, expected_status=422)


@allure.feature('我要摇人')
class TestTicket:
    """转工单"""

    @allure.story('转工单')
    @allure.title('正常：转工单（mock 暂不实现冲突检测）')
    @pytest.mark.api
    async def test_submit_ticket(self, mock_api_client, mock_auth_header):
        """正常流程：转工单（mock 暂不实现冲突检测）"""
        await _api(mock_api_client, 'post', '/api/ai/qa/submit', headers=mock_auth_header,
                   json={'conversation_id': 1}, expected_status=200,
                   expected_fields={'status': 'created'})

    @allure.story('转工单')
    @allure.title('权限：未认证提交转工单')
    @pytest.mark.api
    async def test_submit_ticket_unauthorized(self, mock_api_client):
        """权限：未认证提交转工单"""
        await _api(mock_api_client, 'post', '/api/ai/qa/submit',
                   json={'conversation_id': 1}, expected_status=401)


@allure.feature('我要摇人')
class TestFlow:
    """全链路流程"""

    @allure.story('全链路')
    @allure.title('全链路：提问→转工单→确认')
    @pytest.mark.api
    async def test_full_flow_ask_submit_ack(self, mock_api_client, mock_auth_header):
        """全链路：提问→转工单→确认"""
        r = await _api(mock_api_client, 'post', '/api/call/qa/ask', step='Step 1: 提问',
                       headers=mock_auth_header, json={'question': 'help'},
                       expected_status=200, expected_fields={'success': True})

        r = await _api(mock_api_client, 'post', '/api/ai/qa/submit', step='Step 2: 转工单',
                       headers=mock_auth_header, json={'conversation_id': 1},
                       expected_status=200, expected_fields={'status': 'created'})

        await _api(mock_api_client, 'post', '/api/ai/qa/ticket/ack', step='Step 3: 确认派单',
                   headers=mock_auth_header, json={'ticket_id': 1},
                   expected_status=200, expected_fields={'status': 'acknowledged'})
