"""Business acceptance scenarios and the response evidence they can display."""

from __future__ import annotations

import json
from dataclasses import dataclass


SCENARIO_LABEL = "businessScenario"
EVIDENCE_ATTACHMENT = "业务验收证据"
USP_USAGE_ISSUE = "USP 使用问题"

SOURCE_LABELS = {
    "quality-infra": "框架与基础设施",
    "quality-api": "模拟接口测试",
    "real": "真实后端冒烟检查",
    "contract": "接口契约检查",
    "db": "知识库数据库检查（观察项）",
    "scenarios": "真实工单流程（观察项）",
}
OBSERVATION_SOURCES = (SOURCE_LABELS["db"], SOURCE_LABELS["scenarios"])

BUSINESS_VALUES = {
    "in_progress": "处理中",
    "pending_requested": "等待提单人确认暂停",
    "pending": "已暂停",
    "new": "待处理",
    "resolved": "已解决",
    "closed": "已关闭",
    "canceled": "已撤回",
}


@dataclass(frozen=True)
class BusinessStep:
    role: str
    action: str
    expected: str
    http_status: int = 200
    field: str | None = None
    expected_value: str | None = None


@dataclass(frozen=True)
class BusinessScenario:
    module: str
    title: str
    source: str
    steps: dict[str, BusinessStep]


SCENARIOS = {
    "incident-create": BusinessScenario(
        "提单与接单", "提单人提交 USP 使用问题，接单人接单", SOURCE_LABELS["quality-api"],
        {
            "create": BusinessStep(
                "提单人", f"提交「{USP_USAGE_ISSUE}」", f"系统创建「{USP_USAGE_ISSUE}」工单",
                field="title", expected_value=USP_USAGE_ISSUE,
            ),
            "accept": BusinessStep(
                "接单人", "接收同一张工单", "工单进入处理中",
                field="status", expected_value="in_progress",
            ),
        },
    ),
    "work-order-pause": BusinessScenario(
        "工单处理", "处理人请求暂停，提单人确认", SOURCE_LABELS["scenarios"],
        {
            "accept": BusinessStep(
                "工单处理人", "接单", "工单进入处理中",
                field="status", expected_value="in_progress",
            ),
            "request": BusinessStep(
                "工单处理人", "请求暂停工单", "工单等待提单人确认暂停",
                field="status", expected_value="pending_requested",
            ),
            "confirm": BusinessStep(
                "提单人", "确认暂停", "工单进入已暂停",
                field="status", expected_value="pending",
            ),
        },
    ),
    "admin-permissions": BusinessScenario(
        "后台权限", "普通工程师访问后台用户管理时被拒绝", SOURCE_LABELS["quality-api"],
        {
            "access": BusinessStep(
                "已登录工程师", "打开后台用户列表", "系统拒绝访问", http_status=403,
            ),
        },
    ),
}


def record_business_evidence(scenario_id: str, step_id: str, response) -> None:
    """Attach only the status and an allowlisted business value before assertions."""
    import allure

    step = SCENARIOS[scenario_id].steps[step_id]
    value = None
    if step.field:
        try:
            body = response.json()
        except ValueError:
            body = None
        raw = body.get(step.field) if isinstance(body, dict) else None
        if step.field == "title" and raw == step.expected_value:
            value = step.expected_value
        elif step.field == "status" and isinstance(raw, str) and raw in BUSINESS_VALUES:
            value = raw
    allure.attach(
        json.dumps({
            "scenario": scenario_id,
            "step": step_id,
            "http_status": response.status_code,
            "value": value,
        }, ensure_ascii=False),
        name=EVIDENCE_ATTACHMENT,
        attachment_type=allure.attachment_type.JSON,
    )


def describe_actual(step: BusinessStep, evidence: dict) -> tuple[str, bool | None]:
    http_status = evidence.get("http_status")
    if type(http_status) is not int or not 100 <= http_status <= 599:
        return "未记录实际结果", None
    value = evidence.get("value")
    known_value = BUSINESS_VALUES.get(value) if isinstance(value, str) else None
    if step.field == "title":
        matches = http_status == step.http_status and value == step.expected_value
        observation = f"已创建「{step.expected_value}」工单" if matches else "建单结果与预期不符"
    elif step.field:
        observation = known_value or "未取得可判定的业务状态"
        matches = http_status == step.http_status and value == step.expected_value
    else:
        observation = {
            200: "系统允许访问", 401: "系统要求登录", 403: "系统拒绝访问",
        }.get(http_status, "系统返回响应")
        matches = http_status == step.http_status
    return f"{observation}（HTTP {http_status}）", matches
