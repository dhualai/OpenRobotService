"""Real-safe API smoke tests sharing the UI regression environment."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import allure
import httpx
import pytest

from .conftest import UiRegressionRuntime


pytestmark = [pytest.mark.smoke, pytest.mark.api]


@dataclass(frozen=True)
class U1SmokeSession:
    headers: dict[str, str]
    token_type: str
    has_access_token: bool


class AssertionCollector:
    def __init__(self):
        self.results: list[dict] = []

    def equal(self, name: str, expected, actual) -> None:
        passed = expected == actual
        self.results.append(
            {
                "name": name,
                "expected": expected,
                "actual": actual,
                "result": "通过" if passed else "失败",
            }
        )
        assert passed, f"{name} 期望 {expected!r}，实际 {actual!r}"

    def truthy(self, name: str, actual) -> None:
        passed = bool(actual)
        self.results.append(
            {
                "name": name,
                "expected": "非空/为真",
                "actual": bool(actual),
                "result": "通过" if passed else "失败",
            }
        )
        assert passed, f"{name} 期望非空/为真"

    def attach(self) -> None:
        allure.attach(
            json.dumps(self.results, ensure_ascii=False, indent=2),
            name="断言结果",
            attachment_type=allure.attachment_type.JSON,
        )


@contextmanager
def _assertions() -> Iterator[AssertionCollector]:
    collector = AssertionCollector()
    try:
        yield collector
    finally:
        collector.attach()


@pytest.fixture(scope="module")
def real_safe_client(
    ui_regression_runtime: UiRegressionRuntime,
) -> httpx.Client:
    with httpx.Client(
        base_url=ui_regression_runtime.backend_url,
        timeout=20,
    ) as client:
        yield client


_U1_SMOKE_SESSION: U1SmokeSession | None = None


def _get_u1_smoke_session(
    client: httpx.Client,
    runtime: UiRegressionRuntime,
) -> U1SmokeSession:
    global _U1_SMOKE_SESSION
    if _U1_SMOKE_SESSION is not None:
        return _U1_SMOKE_SESSION

    with _assertions() as assertions:
        response = _request(
            client,
            "POST",
            "/api/auth/login",
            expected_status=200,
            assertions=assertions,
            json={
                "username": runtime.u1_username,
                "password": runtime.u1_password,
            },
        )
        payload = response.json()
        access_token = payload.get("access_token")
        token_type = payload.get("token_type", "")
        _attach_response_summary(
            {
                "token_type": token_type,
                "has_access_token": bool(access_token),
            }
        )
        assertions.truthy("access_token 非空", access_token)
        assertions.equal("token_type", "bearer", token_type)
        _U1_SMOKE_SESSION = U1SmokeSession(
            headers={"Authorization": f"Bearer {access_token}"},
            token_type=token_type,
            has_access_token=True,
        )
        return _U1_SMOKE_SESSION


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    expected_status: int,
    assertions: AssertionCollector | None = None,
    **kwargs,
) -> httpx.Response:
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)

    with allure.step(
        f"接口：{method} {path} -> HTTP {response.status_code}"
    ):
        allure.attach(
            json.dumps(
                {
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
                ensure_ascii=False,
                indent=2,
            ),
            name="接口结果",
            attachment_type=allure.attachment_type.JSON,
        )
        if assertions is not None:
            assertions.equal(
                "HTTP 状态码",
                expected_status,
                response.status_code,
            )
    return response


def _attach_response_summary(summary: dict) -> None:
    allure.attach(
        json.dumps(summary, ensure_ascii=False, indent=2),
        name="响应摘要",
        attachment_type=allure.attachment_type.JSON,
    )


@allure.feature("真实测试环境 Smoke")
@allure.story("环境")
@allure.title("正常：测试后端健康检查")
def test_health_smoke(real_safe_client: httpx.Client):
    with _assertions() as assertions:
        response = _request(
            real_safe_client,
            "GET",
            "/api/health",
            expected_status=200,
            assertions=assertions,
        )
        status = response.json().get("status")
        _attach_response_summary({"status": status})
        assertions.equal("响应字段 status", "healthy", status)


@allure.feature("真实测试环境 Smoke")
@allure.story("登录")
@allure.title("正常：U1 登录成功")
def test_u1_login_smoke(
    real_safe_client: httpx.Client,
    ui_regression_runtime: UiRegressionRuntime,
):
    session = _get_u1_smoke_session(real_safe_client, ui_regression_runtime)
    assert session.has_access_token
    assert session.token_type == "bearer"


@allure.feature("真实测试环境 Smoke")
@allure.story("登录")
@allure.title("正常：U1 获取当前用户")
def test_u1_me_smoke(
    real_safe_client: httpx.Client,
    ui_regression_runtime: UiRegressionRuntime,
):
    session = _get_u1_smoke_session(real_safe_client, ui_regression_runtime)
    with _assertions() as assertions:
        response = _request(
            real_safe_client,
            "GET",
            "/api/auth/me",
            headers=session.headers,
            expected_status=200,
            assertions=assertions,
        )
        username = response.json().get("username")
        _attach_response_summary({"username": username})
        assertions.equal(
            "响应字段 username",
            ui_regression_runtime.u1_username,
            username,
        )


@allure.feature("真实测试环境 Smoke")
@allure.story("摇人问答")
@allure.title("正常：U1 查询摇人会话列表")
def test_call_conversations_smoke(
    real_safe_client: httpx.Client,
    ui_regression_runtime: UiRegressionRuntime,
):
    session = _get_u1_smoke_session(real_safe_client, ui_regression_runtime)
    with _assertions() as assertions:
        response = _request(
            real_safe_client,
            "GET",
            "/api/call/conversations?scene_type=chat&limit=10",
            headers=session.headers,
            expected_status=200,
            assertions=assertions,
        )
        payload = response.json()
        _attach_response_summary(
            {"result_type": type(payload).__name__, "result_count": len(payload)}
        )
        assertions.equal("响应类型", "list", type(payload).__name__)


@allure.feature("真实测试环境 Smoke")
@allure.story("系统任务")
@allure.title("正常：U1 查询可访问工单")
def test_task_filter_smoke(
    real_safe_client: httpx.Client,
    ui_regression_runtime: UiRegressionRuntime,
):
    session = _get_u1_smoke_session(real_safe_client, ui_regression_runtime)
    with _assertions() as assertions:
        response = _request(
            real_safe_client,
            "POST",
            "/api/tasks/filter",
            headers=session.headers,
            expected_status=200,
            assertions=assertions,
            json={"page": 1, "size": 10},
        )
        payload = response.json()
        _attach_response_summary(
            {
                "item_count": len(payload.get("items", [])),
                "total": payload.get("total"),
            }
        )
        assertions.equal(
            "响应字段 items 类型",
            "list",
            type(payload.get("items")).__name__,
        )
        assertions.equal(
            "响应字段 total 类型",
            "int",
            type(payload.get("total")).__name__,
        )


@allure.feature("真实测试环境 Smoke")
@allure.story("系统任务")
@allure.title("正常：U1 查询可指派人员")
def test_assignable_users_smoke(
    real_safe_client: httpx.Client,
    ui_regression_runtime: UiRegressionRuntime,
):
    session = _get_u1_smoke_session(real_safe_client, ui_regression_runtime)
    with _assertions() as assertions:
        response = _request(
            real_safe_client,
            "GET",
            "/api/tasks/assignable-users?skip=0&limit=10",
            headers=session.headers,
            expected_status=200,
            assertions=assertions,
        )
        payload = response.json()
        _attach_response_summary(
            {"result_type": type(payload).__name__, "result_count": len(payload)}
        )
        assertions.equal("响应类型", "list", type(payload).__name__)
