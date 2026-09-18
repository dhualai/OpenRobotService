"""完整业务链路 Mock 演示：U1 提单 -> 派单 U2 -> 处理 -> U1 关闭。"""

import json

import allure
import pytest

from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment


pytestmark = [pytest.mark.api, pytest.mark.e2e, pytest.mark.mock]

U1 = ("u1_auto", "123456")
U2 = ("u2_auto", "123456")
FIXED_QUESTION = (
    "我要在项目 Leo_test（摇人吧服务号-测试）提一个 problem 工单。"
    "指定处理人：自动化处理人。"
    "标题：自动化链路验证-Mock。"
    "问题：机器人无法启动，故障码 E1001，已尝试重启仍无效。"
    "请直接生成工单草稿，不要继续追问。"
)


async def _login(client, username: str, password: str) -> dict:
    with allure.step(f"登录：{username}"):
        response = await client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        assert_status_code(response, 200)
        flush_assert_attachment()
        return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _call(
    client,
    role: str,
    action: str,
    method: str,
    path: str,
    *,
    headers: dict | None = None,
    expected_status: int = 200,
    expected_fields: dict | None = None,
    **kwargs,
):
    with allure.step(f"{role} {action} -> {method.upper()} {path}"):
        response = await client.request(method, path, headers=headers, **kwargs)
        assert_status_code(response, expected_status)
        if expected_fields:
            assert_dict_contains_subset(response.json(), expected_fields)
        flush_assert_attachment()
        return response


@allure.feature("业务链路")
class TestCallToTicketClose:
    @allure.story("摇人问答 -> 提单 -> 派单 -> 处理 -> 关闭")
    @allure.title("Mock：U1提单，U2处理并已解决，U1关闭")
    async def test_call_qa_to_ticket_close(self, mock_api_client):
        """全链路：Mock环境下验证完整业务状态流转。"""
        u1_headers = await _login(mock_api_client, *U1)
        session_id = "sess_auto_mock_001"

        conversation = await _call(
            mock_api_client,
            "U1",
            "创建摇人会话",
            "post",
            "/api/call/conversations",
            headers=u1_headers,
            json={
                "title": "自动化链路验证",
                "user_id": "",
                "service_ticket_id": session_id,
                "scene_type": "chat",
                "metadata_": json.dumps({"ai_session_id": session_id}, ensure_ascii=False),
            },
            expected_fields={"title": "自动化链路验证"},
        )
        conversation_id = conversation.json()["id"]

        await _call(
            mock_api_client,
            "U1",
            "发送固定提问",
            "post",
            "/api/call/messages",
            headers=u1_headers,
            json={
                "conversation_id": conversation_id,
                "role": "user",
                "content": FIXED_QUESTION,
                "message_type": "text",
                "file_urls": None,
                "metadata_": None,
            },
            expected_fields={"content": FIXED_QUESTION},
        )

        stream = await _call(
            mock_api_client,
            "AI",
            "固定回复并生成草稿",
            "post",
            "/api/ai/qa/ask/stream",
            headers=u1_headers,
            json={"session_id": session_id, "query": FIXED_QUESTION},
        )
        assert "draft_ready" in stream.text
        assert "自动化处理人" in stream.text

        prepare = await _call(
            mock_api_client,
            "U1",
            "点击转工单",
            "post",
            "/api/ai/qa/ticket/prepare",
            headers=u1_headers,
            json={"session_id": session_id},
            expected_fields={"code": 0},
        )
        draft = prepare.json()["data"]["draft"]
        assert draft["type"] == "problem"
        assert draft["project_id"] == "Leo_test"
        assert draft["title"] == "自动化链路验证-Mock"
        assert "[指定处理人：自动化处理人]" in draft["description"]

        steps = await _call(
            mock_api_client,
            "U1",
            "获取 problem 协商阶段",
            "get",
            "/api/ai/qa/ticket/steps",
            headers=u1_headers,
            params={"type": "problem"},
            expected_fields={"code": 0},
        )
        assert [item["step_name"] for item in steps.json()["data"]["steps"]] == [
            "初步诊断",
            "临时解决",
            "最终解决",
        ]

        confirm = await _call(
            mock_api_client,
            "U1",
            "确认提单",
            "post",
            "/api/ai/qa/ticket/confirm",
            headers=u1_headers,
            json={"session_id": session_id, "overrides": draft, "username": U1[0]},
            expected_fields={"code": 0},
        )
        ticket_id = confirm.json()["data"]["db_id"]
        ticket = confirm.json()["data"]["ticket"]
        assert ticket["status"] == "new"
        assert ticket["created_by"] == U1[0]
        assert ticket["project_id"] == "Leo_test"
        assert ticket["assigned_to"] == U2[0]

        u2_headers = await _login(mock_api_client, *U2)
        filtered = await _call(
            mock_api_client,
            "U2",
            "查看待我处理列表",
            "post",
            "/api/tasks/filter",
            headers=u2_headers,
            json={"status": "new", "assigned_to": U2[0]},
        )
        assert any(item["id"] == ticket_id for item in filtered.json()["items"])

        await _call(
            mock_api_client,
            "U2",
            "打开工单详情",
            "get",
            f"/api/tasks/{ticket_id}",
            headers=u2_headers,
            params={"load_comments": "true"},
            expected_fields={"id": ticket_id, "assigned_to": U2[0]},
        )

        accepted = await _call(
            mock_api_client,
            "U2",
            "确认接单开始处理",
            "post",
            f"/api/tasks/{ticket_id}/respond",
            headers=u2_headers,
            json={"curr_step_id": 1},
            expected_fields={"status": "in_progress", "curr_step_agreed": True},
        )
        assert accepted.json()["curr_step_name"] == "初步诊断"

        stage_two = await _call(
            mock_api_client,
            "U2",
            "完成初步诊断阶段",
            "post",
            f"/api/tasks/{ticket_id}/complete-step",
            headers=u2_headers,
            json={"next_step_id": 2, "curr_step_endtime": "2026-09-30T12:00:00"},
            expected_fields={"curr_step_id": 2, "curr_step_agreed": False},
        )
        assert stage_two.json()["curr_step_name"] == "临时解决"

        await _call(
            mock_api_client,
            "U1",
            "确认临时解决阶段",
            "post",
            f"/api/tasks/{ticket_id}/respond",
            headers=u1_headers,
            json={"curr_step_id": 2},
            expected_fields={"curr_step_id": 2, "curr_step_agreed": True},
        )

        stage_three = await _call(
            mock_api_client,
            "U2",
            "完成临时解决阶段",
            "post",
            f"/api/tasks/{ticket_id}/complete-step",
            headers=u2_headers,
            json={"next_step_id": 3, "curr_step_endtime": "2026-10-08T12:00:00"},
            expected_fields={"curr_step_id": 3, "curr_step_agreed": False},
        )
        assert stage_three.json()["curr_step_name"] == "最终解决"

        await _call(
            mock_api_client,
            "U1",
            "确认最终解决阶段",
            "post",
            f"/api/tasks/{ticket_id}/respond",
            headers=u1_headers,
            json={"curr_step_id": 3},
            expected_fields={"curr_step_id": 3, "curr_step_agreed": True},
        )

        resolved = await _call(
            mock_api_client,
            "U2",
            "提交已解决",
            "patch",
            f"/api/tasks/{ticket_id}/status",
            headers=u2_headers,
            json={"status": "resolved", "resolution_summary": "已替换传感器模块"},
            expected_fields={"status": "resolved"},
        )
        assert resolved.json()["resolved_at"]

        await _call(
            mock_api_client,
            "U1",
            "查看已解决工单",
            "get",
            f"/api/tasks/{ticket_id}",
            headers=u1_headers,
            expected_fields={"status": "resolved"},
        )

        closed = await _call(
            mock_api_client,
            "U1",
            "确认关闭",
            "patch",
            f"/api/tasks/{ticket_id}/status",
            headers=u1_headers,
            json={"status": "closed"},
            expected_fields={"status": "closed"},
        )
        assert closed.json()["closed_at"]
