"""真实后端业务场景用例（场景 ③④⑦）。

对应用户确认的 10 条业务场景：
- ③ 撤回 / 暂停 / 无法解决 三种异常状态 + 权限收窄
- ④ 打回重开（仅 resolved 可打回）
- ⑦ 评论 + 已读回执

运行前提：
    USE_MOCK=0，后端跑在 8400，MySQL 可用。

    USE_MOCK=0 pytest automation/tests/real/test_ticket_scenarios.py -v

环境变量：
    REAL_API_BASE_URL   默认 http://localhost:8400
    REAL_U1_USERNAME/PASSWORD   提单人（管理员）    默认 admin / usp2026@EP
    REAL_U2_USERNAME/PASSWORD   处理人（管理员种子）默认 automation_bot / Automation@2026
    REAL_U3_USERNAME/PASSWORD   普通第三方（非管理员）默认 plain_user / Passw0rd!
"""

import os
import uuid

import allure
import pytest

from automation.config.models import ApiConfig
from automation.src.assertions import assert_status_code
from automation.src.clients.api_client import ApiClient

pytestmark = [pytest.mark.api, pytest.mark.e2e]

_BASE_URL = os.getenv("REAL_API_BASE_URL", "http://localhost:8400")
_U1 = (
    os.getenv("REAL_U1_USERNAME", "admin"),
    os.getenv("REAL_U1_PASSWORD", "usp2026@EP"),
)
_U2 = (
    os.getenv("REAL_U2_USERNAME", "automation_bot"),
    os.getenv("REAL_U2_PASSWORD", "Automation@2026"),
)
_U3 = (
    os.getenv("REAL_U3_USERNAME", "plain_user"),
    os.getenv("REAL_U3_PASSWORD", "Passw0rd!"),
)

_PROJECT = {"project_name": "摇人吧服务号-测试", "project_id": "Leo_test"}


@pytest.fixture
async def client():
    if os.getenv("USE_MOCK", "1") != "0":
        pytest.skip("USE_MOCK=0 required；业务场景用例需要真实后端")
    api = ApiClient(
        config=ApiConfig(base_url=_BASE_URL, timeout=60),
        raise_auth_errors=False,
    )
    await api.connect()
    yield api
    await api.close()


async def _login(api: ApiClient, cred: tuple[str, str]) -> dict:
    username, password = cred
    with allure.step(f"登录：{username}"):
        response = await api.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        assert_status_code(response, 200)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _me_id(api: ApiClient, headers: dict) -> str:
    response = await api.get("/api/auth/me", headers=headers)
    assert_status_code(response, 200)
    return response.json()["id"]


async def _create_ticket(api: ApiClient, headers: dict, tag: str) -> int:
    title = f"AUTO-SCENARIO-{tag}-{uuid.uuid4().hex[:8]}"
    with allure.step(f"提单：{title}"):
        response = await api.post(
            "/api/tasks/",
            headers=headers,
            json={
                "title": title,
                "description": "业务场景自动化验证，可安全删除。",
                "ticket_type": "problem",
                **_PROJECT,
            },
        )
        assert_status_code(response, 200)
        return response.json()["id"]


async def _assign(api: ApiClient, headers: dict, task_id: int, user_id: str) -> None:
    with allure.step(f"派单给 {user_id}"):
        response = await api.put(
            f"/api/tasks/{task_id}", headers=headers, json={"assigned_to": user_id}
        )
        assert_status_code(response, 200)


async def _steps(api: ApiClient, headers: dict, task_id: int) -> list[dict]:
    response = await api.get(f"/api/tasks/{task_id}/steps", headers=headers)
    assert_status_code(response, 200)
    return response.json()["data"]["steps"]


async def _set_status(
    api: ApiClient,
    headers: dict,
    task_id: int,
    status: str,
    expected: int,
    **extra,
):
    """状态流转。部分流转带必填理由字段：
    resolved → resolution_summary；pending_requested → pause_reason；
    unsolvable_requested → unsolvable_reason；驳回 → reject_reason。
    """
    with allure.step(f"状态流转 → {status}（期望 {expected}）"):
        response = await api.patch(
            f"/api/tasks/{task_id}/status",
            headers=headers,
            json={"status": status, **extra},
        )
        assert_status_code(response, expected)
        return response


async def _retire_ticket(api: ApiClient, headers: dict, task_id: int) -> None:
    """作废测试工单。

    不使用 ``DELETE /api/tasks/{id}``：后端删除时会把 task_operation_logs.task_id
    置空，而该列是 NOT NULL，导致 IntegrityError 1048 → 接口返回 500
    （任何产生过操作日志的工单都删不掉）。这里改为撤回，保持库内可追溯。
    """
    with allure.step("作废测试工单（撤回）"):
        response = await api.patch(
            f"/api/tasks/{task_id}/status",
            headers=headers,
            json={"status": "canceled"},
        )
        if response.status_code not in (200, 400, 403):
            allure.attach(
                f"PATCH canceled 返回 {response.status_code}：{response.text[:300]}",
                name="作废告警",
                attachment_type=allure.attachment_type.TEXT,
            )


@allure.feature("业务场景")
class TestTicketScenarioExceptionStates:
    """场景③：撤回 / 暂停 / 无法解决 三种异常状态与权限规则。"""

    @allure.story("撤回")
    @allure.title("场景③-1：处理人不可撤回，仅提单人可撤回")
    async def test_cancel_only_creator(self, client):
        """状态流转：处理人撤回被拒，提单人撤回成功。"""
        u1 = await _login(client, _U1)
        u3 = await _login(client, _U3)
        u3_id = await _me_id(client, u3)
        task_id = await _create_ticket(client, u1, "cancel")
        try:
            await _assign(client, u1, task_id, u3_id)

            # 处理人（非管理员）撤回 → 403
            await _set_status(client, u3, task_id, "canceled", 403)

            # 提单人撤回 → 200
            response = await _set_status(client, u1, task_id, "canceled", 200)
            assert response.json()["status"] == "canceled"
        finally:
            await _retire_ticket(client, u1, task_id)

    @allure.story("暂停")
    @allure.title("场景③-2：处理人请求暂停 → 提单人确认 → 已暂停")
    async def test_pause_request_then_confirm(self, client):
        """状态流转：in_progress → pending_requested → pending。"""
        u1 = await _login(client, _U1)
        u3 = await _login(client, _U3)
        u3_id = await _me_id(client, u3)
        task_id = await _create_ticket(client, u1, "pause")
        try:
            await _assign(client, u1, task_id, u3_id)
            steps = await _steps(client, u1, task_id)
            with allure.step("处理人接单"):
                response = await client.post(
                    f"/api/tasks/{task_id}/respond",
                    headers=u3,
                    json={"curr_step_id": steps[0]["id"]},
                )
                assert_status_code(response, 200)
                assert response.json()["status"] == "in_progress"

            # 处理人请求暂停（必填 pause_reason）
            await _set_status(
                client,
                u3,
                task_id,
                "pending_requested",
                200,
                pause_reason="自动化验证：等待外部资料补充",
            )

            # 提单人确认暂停
            response = await _set_status(client, u1, task_id, "pending", 200)
            assert response.json()["status"] == "pending"
        finally:
            await _retire_ticket(client, u1, task_id)

    @allure.story("暂停")
    @allure.title("场景③-3：非处理中的工单不可请求暂停")
    async def test_pause_rejected_when_not_in_progress(self, client):
        """状态流转：new 状态请求暂停 → 400。"""
        u1 = await _login(client, _U1)
        u3 = await _login(client, _U3)
        u3_id = await _me_id(client, u3)
        task_id = await _create_ticket(client, u1, "pause-bad")
        try:
            await _assign(client, u1, task_id, u3_id)
            await _set_status(client, u3, task_id, "pending_requested", 400)
        finally:
            await _retire_ticket(client, u1, task_id)

    @allure.story("无法解决")
    @allure.title("场景③-4：处理人请求无法解决 → 提单人确认 → 恢复处理")
    async def test_unsolvable_then_restore(self, client):
        """状态流转：in_progress → unsolvable_requested → unsolvable → in_progress。"""
        u1 = await _login(client, _U1)
        u3 = await _login(client, _U3)
        u3_id = await _me_id(client, u3)
        task_id = await _create_ticket(client, u1, "unsolvable")
        try:
            await _assign(client, u1, task_id, u3_id)
            steps = await _steps(client, u1, task_id)
            with allure.step("处理人接单"):
                response = await client.post(
                    f"/api/tasks/{task_id}/respond",
                    headers=u3,
                    json={"curr_step_id": steps[0]["id"]},
                )
                assert_status_code(response, 200)

            await _set_status(
                client,
                u3,
                task_id,
                "unsolvable_requested",
                200,
                unsolvable_reason="自动化验证：现场处理失败，无法解决",
            )
            response = await _set_status(client, u1, task_id, "unsolvable", 200)
            assert response.json()["status"] == "unsolvable"

            # 处理人恢复处理
            response = await _set_status(client, u3, task_id, "in_progress", 200)
            assert response.json()["status"] == "in_progress"
        finally:
            await _retire_ticket(client, u1, task_id)


@allure.feature("业务场景")
class TestTicketScenarioReopen:
    """场景④：打回重开。"""

    @allure.story("打回重开")
    @allure.title("场景④：非已解决状态不可打回，已解决可打回")
    async def test_reopen_only_from_resolved(self, client):
        """状态流转：new 打回 → 400；resolved 打回 → 200 回到 in_progress。"""
        u1 = await _login(client, _U1)
        u3 = await _login(client, _U3)
        u3_id = await _me_id(client, u3)
        task_id = await _create_ticket(client, u1, "reopen")
        try:
            await _assign(client, u1, task_id, u3_id)
            steps = await _steps(client, u1, task_id)

            # new 状态打回 → 400
            with allure.step("new 状态打回（期望 400）"):
                response = await client.post(
                    f"/api/tasks/{task_id}/reopen-step",
                    headers=u1,
                    json={
                        "curr_step_id": steps[0]["id"],
                        "curr_step_endtime": "2026-12-31T12:00:00",
                    },
                )
                assert_status_code(response, 400)

            # 走完整流程到 resolved
            with allure.step("处理人接单"):
                response = await client.post(
                    f"/api/tasks/{task_id}/respond",
                    headers=u3,
                    json={"curr_step_id": steps[0]["id"]},
                )
                assert_status_code(response, 200)
            for index in range(1, len(steps)):
                with allure.step(f"推进到阶段 {index + 1}"):
                    response = await client.post(
                        f"/api/tasks/{task_id}/complete-step",
                        headers=u3,
                        json={
                            "next_step_id": steps[index]["id"],
                            "curr_step_endtime": "2026-12-31T12:00:00",
                        },
                    )
                    assert_status_code(response, 200)
                with allure.step(f"提单人确认阶段 {index + 1}"):
                    response = await client.post(
                        f"/api/tasks/{task_id}/respond",
                        headers=u1,
                        json={"curr_step_id": steps[index]["id"]},
                    )
                    assert_status_code(response, 200)

            await _set_status(
                client,
                u3,
                task_id,
                "resolved",
                200,
                resolution_summary="真实后端业务场景自动化验证完成",
            )

            # resolved 打回 → 200
            with allure.step("已解决打回（期望 200）"):
                response = await client.post(
                    f"/api/tasks/{task_id}/reopen-step",
                    headers=u1,
                    json={
                        "curr_step_id": steps[0]["id"],
                        "curr_step_endtime": "2026-12-31T12:00:00",
                    },
                )
                assert_status_code(response, 200)
                assert response.json()["status"] == "in_progress"
        finally:
            await _retire_ticket(client, u1, task_id)


@allure.feature("业务场景")
class TestTicketScenarioComment:
    """场景⑦：评论与已读回执。"""

    @allure.story("评论")
    @allure.title("场景⑦：添加评论 → 对方可见 → 已读回执可查")
    async def test_comment_and_read_receipt(self, client):
        """评论：纯文字评论落库、第三方可见、已读上报后可查到读名单。

        附件上传未纳入：本地未启动 MinIO，`POST /comments/attachments` 依赖对象存储。
        """
        u1 = await _login(client, _U1)
        u3 = await _login(client, _U3)
        u3_id = await _me_id(client, u3)
        task_id = await _create_ticket(client, u1, "comment")
        try:
            await _assign(client, u1, task_id, u3_id)

            with allure.step("提单人添加评论"):
                response = await client.post(
                    f"/api/tasks/{task_id}/comments",
                    headers=u1,
                    json={"content": "业务场景自动化：评论验证", "is_public": True},
                )
                assert_status_code(response, 200)
                comment_id = response.json()["id"]

            with allure.step("普通用户可读取该评论"):
                response = await client.get(
                    f"/api/tasks/{task_id}/comments", headers=u3
                )
                assert_status_code(response, 200)
                payload = response.json()
                items = payload.get("items") if isinstance(payload, dict) else payload
                assert any(item["id"] == comment_id for item in items)

            with allure.step("普通用户上报已读"):
                response = await client.post(
                    f"/api/tasks/{task_id}/comments/read",
                    headers=u3,
                    json={"comment_ids": [comment_id], "last_read_comment_id": comment_id},
                )
                assert_status_code(response, 200)

            with allure.step("查询已读名单"):
                response = await client.get(
                    f"/api/tasks/{task_id}/comments/{comment_id}/read", headers=u1
                )
                assert_status_code(response, 200)
        finally:
            await _retire_ticket(client, u1, task_id)
