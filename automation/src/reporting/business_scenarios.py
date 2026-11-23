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
    "service-account-rules": "服务号后台规则检查（观察项）",
    "service-account-ui": "服务号测试站页面",
}
OBSERVATION_SOURCES = tuple(
    SOURCE_LABELS[key] for key in ("db", "scenarios", "service-account-rules")
)

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
    passed_result: str | None = None


@dataclass(frozen=True)
class BusinessScenario:
    module: str
    title: str
    source: str
    steps: dict[str, BusinessStep]
    flow_title: str = ""
    planned_steps: tuple[tuple[BusinessStep, str], ...] = ()


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


def checked_step(role: str, action: str, expected: str, actual: str) -> BusinessStep:
    return BusinessStep(role, action, expected, passed_result=actual)


SERVICE_ACCOUNT_SCENARIOS = {
    "service-ticket-lifecycle": BusinessScenario(
        "服务号工单", "提单人报障，处理人解决，提单人验收关闭", SOURCE_LABELS["service-account-ui"],
        {
            f"S{number:02}": checked_step(*step)
            for number, step in enumerate((
                ("提单人", "登录", "进入测试工作台", "工作台已打开"),
                ("提单人", "新建会话", "打开空白咨询会话", "咨询输入区就绪"),
                ("提单人", "发送模拟故障", "建立独立的报障会话", "报障会话已创建"),
                ("提单人", "核对工单草稿", "项目为摇人吧服务号测试，类型为报障，起始阶段为初步诊断", "项目、报障类型和初步诊断阶段已核对"),
                ("提单人", "确认提交工单", "生成工单编号并显示概览卡片", "已建单，概览卡片可见"),
                ("系统", "自动派单", "派给测试处理人", "工单显示已派单"),
                ("处理人", "登录并进入待我处理", "可以查找本人待处理工单", "已进入待我处理列表"),
                ("处理人", "查找已指派工单", "列表包含刚提交的同一张工单", "以同一编号找到工单"),
                ("处理人", "查看待接单工单", "处于待处理和初步诊断阶段，等待处理人确认", "待处理状态及当前阶段一致"),
                ("处理人", "确认接单", "工单进入处理中，初步诊断达成一致", "已接单，进入处理中"),
                ("处理人", "完成初步诊断", "进入临时解决，等待提单人确认", "临时解决阶段已开启"),
                ("提单人", "确认临时解决", "临时解决阶段达成一致", "双方对临时解决达成一致"),
                ("处理人", "完成临时解决", "进入最终解决，等待提单人确认", "最终解决阶段已开启"),
                ("提单人", "确认最终解决", "最终解决阶段达成一致", "双方对最终解决达成一致"),
                ("处理人", "提交解决说明", "工单变为已解决", "状态已变为已解决"),
                ("提单人", "验收解决结果", "可查看已解决状态和处理说明", "已核对状态和处理说明"),
                ("提单人", "关闭并重新打开工单", "状态仍为已关闭", "再次打开仍显示已关闭"),
            ), 1)
        },
        flow_title="提单 → 派单 → 处理 → 关闭",
    ),
    "service-knowledge-finance": BusinessScenario(
        "服务号知识", "工单知识归档、发布与奖励审核", SOURCE_LABELS["service-account-rules"],
        {
            "content": checked_step("内容审核人", "批准归档内容", "知识继续等待发布，三类奖励待财务审核", "知识未发布，三类奖励均已进入待审核"),
            "approve": checked_step("财务审核人", "批准整组奖励并重复提交", "发布知识，奖励可申领，重复批准不重复发放", "知识已发布，三类奖励可申领，重复提交无新增"),
            "reject": checked_step("财务审核人", "在独立工单中拒绝奖励", "知识不发布，奖励不进入可申领", "知识未发布，奖励已拒绝"),
        },
        flow_title="知识沉淀 → 内容审核 → 财务审核 → 奖励",
    ),
    "service-learning": BusinessScenario(
        "服务号学习", "整理知识、审核出题与后台答题预览", SOURCE_LABELS["service-account-rules"],
        {
            "redact": checked_step("内容管理员", "提交知识素材供模型提炼", "交给模型前先脱敏，原稿需人工确认", "模型收到脱敏素材，生成建议待人工采纳"),
            "generate": checked_step("内容管理员", "确认文章并生成草稿题", "题目等待审核，预览隐藏标准答案和源材料", "已生成草稿题，预览隐去答案和源材料"),
            "enable": checked_step("内容审核人", "筛选可用题目", "人工审核、已启用且匹配文章当前版本时才可用", "可用题目符合审核、启用和文章版本要求"),
            "score": checked_step("后台预览者", "提交多选题答案", "答案集合完全匹配才得分，预览不保存成绩", "少选未得分，重复选项被拒绝，完整答案得分，预览未留存"),
        },
        flow_title="选材 → AI 出题 → 审核启用 → 学习答题",
        planned_steps=(
            (
                BusinessStep("学员", "打开课程、答题并刷新", "成绩和课程进度可在重新打开后查看"),
                SOURCE_LABELS["service-account-ui"],
            ),
        ),
    ),
    "service-challenge": BusinessScenario(
        "服务号高价值问题", "发布问题、作答点赞、评选与奖励入审", SOURCE_LABELS["service-account-rules"],
        {
            "publish": checked_step("专区管理员", "发布独立的高价值问题", "授权管理员发布成功，普通用户被拒绝", "普通用户被拒绝，授权管理员已独立发布"),
            "settle": checked_step("用户与系统", "作答点赞，截止后评选", "按答案点赞选出最佳答案，奖励待财务审核且只入账一次", "选出了获赞答案，唯一一条奖励记录待财务审核"),
        },
        flow_title="高价值问题 → 用户解答 → 评选 → 奖励",
    ),
}
SCENARIOS.update(SERVICE_ACCOUNT_SCENARIOS)


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


def record_check_outcome(scenario_id: str, step_id: str, outcome: str) -> None:
    """Record the assertion result without a backend error or response body."""
    import allure

    allure.attach(
        json.dumps({"scenario": scenario_id, "step": step_id, "outcome": outcome}),
        name=EVIDENCE_ATTACHMENT,
        attachment_type=allure.attachment_type.JSON,
    )


def describe_actual(step: BusinessStep, evidence: dict) -> tuple[str, bool | None]:
    if step.passed_result is not None:
        outcome = evidence.get("outcome")
        return {
            "passed": (step.passed_result, True),
            "failed": ("断言未通过", False),
            "broken": ("检查中断", False),
            "skipped": ("本步未执行", None),
        }.get(outcome if isinstance(outcome, str) else "", ("未记录实际结果", None))
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
