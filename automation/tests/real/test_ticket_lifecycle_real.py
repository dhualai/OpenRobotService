"""真实后端工单生命周期场景：不依赖 AI 的提单到关闭。"""

import os
import uuid

import allure
import pytest

from automation.config.models import ApiConfig
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment
from automation.src.clients.api_client import ApiClient


pytestmark = [pytest.mark.api, pytest.mark.e2e]

_BASE_URL = os.getenv("REAL_API_BASE_URL", "http://localhost:8400")
_U1 = (os.getenv("REAL_U1_USERNAME", ""), os.getenv("REAL_U1_PASSWORD", ""))
_U2 = (os.getenv("REAL_U2_USERNAME", ""), os.getenv("REAL_U2_PASSWORD", ""))


@pytest.fixture
async def real_lifecycle_client():
    if os.getenv("USE_MOCK", "1") != "0":
        pytest.skip("USE_MOCK=0 required for real backend lifecycle test")
    if not all((_U1[0], _U1[1], _U2[0], _U2[1])):
        pytest.skip("REAL_U1_USERNAME/PASSWORD and REAL_U2_USERNAME/PASSWORD are required")
    client = ApiClient(
        config=ApiConfig(base_url=_BASE_URL, timeout=30),
        raise_auth_errors=False,
    )
    await client.connect()
    yield client
    await client.close()


async def _login(client: ApiClient, username: str, password: str) -> dict:
    with allure.step(f"登录：{username}"):
        response = await client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        assert_status_code(response, 200)
        flush_assert_attachment()
        return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _call(
    client: ApiClient,
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
class TestTicketLifecycleReal:
    @allure.story("真实后端工单生命周期")
    @allure.title("真实后端：U1建单，U2处理并已解决，U1关闭")
    async def test_ticket_lifecycle_real(self, real_lifecycle_client):
        """全链路：真实后端工单生命周期，AI 问答段不纳入本用例。"""
        marker = f"AUTO-LIFECYCLE-{uuid.uuid4().hex[:8]}"
        u1_headers = await _login(real_lifecycle_client, *_U1)
        u2_headers = await _login(real_lifecycle_client, *_U2)
        ticket_id = None

        try:
            created = await _call(
                real_lifecycle_client,
                "U1",
                "创建 problem 工单",
                "post",
                "/api/tasks/",
                headers=u1_headers,
                json={
                    "title": marker,
                    "description": "真实后端工单生命周期验证，不依赖 AI 问答。",
                    "ticket_type": "problem",
                    "project_name": "摇人吧服务号-测试",
                    "project_id": "Leo_test",
                },
                expected_fields={"title": marker, "status": "new"},
            )
            ticket_id = created.json()["id"]
            created_json = created.json()
            assert created_json.get("created_by") == _U1[0] or created_json.get("created_by_name") == "自动化提单用户"

            await _call(
                real_lifecycle_client,
                "U1",
                "模拟派单给 U2",
                "put",
                f"/api/tasks/{ticket_id}",
                headers=u1_headers,
                json={"assigned_to": _U2[0]},
            )

            detail = await _call(
                real_lifecycle_client,
                "U2",
                "查看待处理工单",
                "get",
                f"/api/tasks/{ticket_id}",
                headers=u2_headers,
                expected_fields={"id": ticket_id, "status": "new"},
            )
            detail_json = detail.json()
            assert detail_json.get("assigned_to") == _U2[0] or detail_json.get("assigned_to_name") == "自动化处理人"

            steps_response = await _call(
                real_lifecycle_client,
                "U2",
                "读取 problem 阶段模板",
                "get",
                f"/api/tasks/{ticket_id}/steps",
                headers=u2_headers,
                expected_fields={"code": 0},
            )
            steps = steps_response.json()["data"]["steps"]
            assert len(steps) >= 3

            await _call(
                real_lifecycle_client,
                "U2",
                "确认接单开始处理",
                "post",
                f"/api/tasks/{ticket_id}/respond",
                headers=u2_headers,
                json={"curr_step_id": steps[0]["id"]},
                expected_fields={"status": "in_progress", "curr_step_agreed": True},
            )

            for index in range(1, len(steps)):
                await _call(
                    real_lifecycle_client,
                    "U2",
                    f"推进到阶段 {index + 1}",
                    "post",
                    f"/api/tasks/{ticket_id}/complete-step",
                    headers=u2_headers,
                    json={
                        "next_step_id": steps[index]["id"],
                        "curr_step_endtime": "2026-10-30T12:00:00",
                    },
                    expected_fields={"curr_step_id": steps[index]["id"], "curr_step_agreed": False},
                )
                await _call(
                    real_lifecycle_client,
                    "U1",
                    f"确认阶段 {index + 1}",
                    "post",
                    f"/api/tasks/{ticket_id}/respond",
                    headers=u1_headers,
                    json={"curr_step_id": steps[index]["id"]},
                    expected_fields={"curr_step_id": steps[index]["id"], "curr_step_agreed": True},
                )

            await _call(
                real_lifecycle_client,
                "U2",
                "提交已解决",
                "patch",
                f"/api/tasks/{ticket_id}/status",
                headers=u2_headers,
                json={"status": "resolved", "resolution_summary": "真实后端生命周期自动化验证完成"},
                expected_fields={"status": "resolved"},
            )

            await _call(
                real_lifecycle_client,
                "U1",
                "确认查看已解决",
                "get",
                f"/api/tasks/{ticket_id}",
                headers=u1_headers,
                expected_fields={"status": "resolved"},
            )

            await _call(
                real_lifecycle_client,
                "U1",
                "确认关闭",
                "patch",
                f"/api/tasks/{ticket_id}/status",
                headers=u1_headers,
                json={"status": "closed"},
                expected_fields={"status": "closed"},
            )
        finally:
            if ticket_id is not None:
                with allure.step("清理测试工单"):
                    response = await real_lifecycle_client.delete(
                        f"/api/tasks/{ticket_id}",
                        headers=u1_headers,
                    )
                    if response.status_code not in (200, 204, 404):
                        allure.attach(
                            response.text,
                            name="工单清理失败",
                            attachment_type=allure.attachment_type.TEXT,
                        )
