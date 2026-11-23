import json
from unittest.mock import AsyncMock

import allure
import httpx
import pytest

from automation.src.reporting import business_report, standalone_report
from automation.src.reporting.business_scenarios import (
    EVIDENCE_ATTACHMENT,
    SCENARIO_LABEL,
    SOURCE_LABELS,
    record_business_evidence,
)
from automation.src.reporting.report_data import SOURCES_FILE
from automation.tests.real import test_ticket_scenarios as real_cases


def _write_case(root, name, scenario, status, steps, *, feature=None, message=None, uid=None):
    labels = [{"name": SCENARIO_LABEL, "value": scenario}] if scenario else []
    if feature:
        labels.append({"name": "feature", "value": feature})
    attachments = []
    for key, http_status, value in steps:
        filename = f"{name}-{key}-attachment.json"
        (root / filename).write_text(json.dumps({
            "scenario": scenario, "step": key, "http_status": http_status, "value": value,
        }), encoding="utf-8")
        attachments.append({"name": EVIDENCE_ATTACHMENT, "source": filename})
    filename = f"{name}-result.json"
    (root / filename).write_text(json.dumps({
        "uuid": uid, "name": name, "status": status, "labels": labels,
        "steps": [{"name": "API 步骤", "attachments": attachments}],
        "statusDetails": {"message": message},
    }, ensure_ascii=False), encoding="utf-8")
    return filename


def _scenario_html(report, scenario):
    fragment = report.split(f'id="scenario-{scenario}"', 1)[1]
    return fragment.split("</details>", 1)[0]


def test_business_report_shows_evidence_roles_and_localizes_source_groups(tmp_path):
    incident = _write_case(
        tmp_path, "故障转工单", "incident-create", "passed",
        [("ask", 200, "accepted"), ("create", 200, "created"), ("confirm", 200, "acknowledged")],
        uid="a0",
    )
    pause = _write_case(
        tmp_path, "工单未暂停", "work-order-pause", "failed",
        [("accept", 200, "in_progress"), ("request", 200, "pending_requested"),
         ("confirm", 200, "in_progress")],
        feature="Observed work orders", uid="a1",
    )
    collected_skip = _write_case(tmp_path, "模拟套件收集后跳过", "work-order-pause", "skipped", [])
    admin = _write_case(
        tmp_path, "后台权限检查", "admin-permissions", "passed", [("access", 403, None)],
        feature="后台管理",
    )
    db = _write_case(
        tmp_path, "知识库检查", None, "skipped", [], feature="DB checks",
        message="XFAIL\nAssertionError",
    )
    _write_case(tmp_path, "本地用例", None, "passed", [])
    (tmp_path / SOURCES_FILE).write_text(json.dumps({
        incident: SOURCE_LABELS["quality-api"], pause: SOURCE_LABELS["scenarios"],
        collected_skip: SOURCE_LABELS["quality-api"], admin: SOURCE_LABELS["quality-api"],
        db: SOURCE_LABELS["db"],
    }, ensure_ascii=False), encoding="utf-8")

    business = tmp_path / "business.html"
    standalone = tmp_path / "standalone.html"
    business_report.main(["--results", str(tmp_path), "--out", str(business)])
    standalone_report.main(["--results", str(tmp_path), "--out", str(standalone)])
    html = business.read_text(encoding="utf-8")
    assert "业务场景一览" in html
    incident_html = _scenario_html(html, "incident-create")
    assert "模拟通过" in incident_html
    assert "<th>角色</th><th>动作</th><th>预期结果</th><th>实际结果</th>" in incident_html
    assert "<td>报障人</td><td>提交转工单请求</td><td>系统创建工单</td>" in incident_html
    assert "已创建工单（HTTP 200）" in incident_html
    assert 'href="#case-a0"' in incident_html and 'id="case-a0"' in html

    pause_html = _scenario_html(html, "work-order-pause")
    assert 'class="business-status failure">失败' in pause_html
    assert "<td>提单人</td><td>确认暂停</td><td>工单进入已暂停</td>" in pause_html
    assert '<td class="failure">处理中（HTTP 200）</td>' in pause_html
    assert "系统拒绝访问（HTTP 403）" in _scenario_html(html, "admin-permissions")

    groups = business_report.collect(tmp_path)
    assert set(groups) == {
        "模拟接口测试", "真实工单流程（观察项）", "后台管理",
        "知识库数据库检查（观察项）", "来源未标注",
    }
    assert "Observed work orders" not in html and "DB checks" not in html
    assert "预期失败（计入跳过）" in html
    assert '<div class="err">XFAIL' not in html
    offline = standalone.read_text(encoding="utf-8")
    assert "真实工单流程（观察项）" in offline
    assert "知识库数据库检查（观察项）" in offline


def test_report_keeps_skipped_missing_and_incomplete_scenarios_unverified(tmp_path):
    incident = _write_case(
        tmp_path, "报障", "incident-create", "passed", [("ask", 200, "accepted")]
    )
    pause = _write_case(
        tmp_path, "暂停", "work-order-pause", "skipped", [], message="USE_MOCK=0 required"
    )
    (tmp_path / SOURCES_FILE).write_text(json.dumps({
        incident: SOURCE_LABELS["quality-api"], pause: SOURCE_LABELS["scenarios"],
    }), encoding="utf-8")
    output = tmp_path / "business.html"
    business_report.main(["--results", str(tmp_path), "--out", str(output)])
    html = output.read_text(encoding="utf-8")

    incident_html = _scenario_html(html, "incident-create")
    assert "未验证" in incident_html and "步骤证据不完整" in incident_html
    assert "未记录实际结果" in incident_html
    assert "本次没有启用真实后端验证" in _scenario_html(html, "work-order-pause")
    admin_html = _scenario_html(html, "admin-permissions")
    assert "未验证" in admin_html and "模拟接口测试（计划）" in admin_html


def test_evidence_omits_unrecognized_response_values(monkeypatch):
    attached = []
    monkeypatch.setattr(allure, "attach", lambda data, **kwargs: attached.append(data))
    token = "synthetic-secret-in-provider-status"
    response = httpx.Response(200, json={"status": token, "access_token": token})

    record_business_evidence("incident-create", "create", response)

    assert token not in attached[0]
    assert json.loads(attached[0]) == {
        "scenario": "incident-create", "step": "create", "http_status": 200, "value": None,
    }


@pytest.mark.asyncio
async def test_real_pause_case_records_all_asserted_states(monkeypatch):
    attached = []
    monkeypatch.setattr(allure, "attach", lambda data, **kwargs: attached.append(json.loads(data)))
    for name, value in {
        "_login": {"Authorization": "synthetic"},
        "_me_id": "worker",
        "_create_ticket": 13,
        "_assign": None,
        "_steps": [{"id": 2}],
        "_retire_ticket": None,
    }.items():
        monkeypatch.setattr(real_cases, name, AsyncMock(return_value=value))
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json={"status": "in_progress"})
    client.patch.side_effect = [
        httpx.Response(200, json={"status": "pending_requested"}),
        httpx.Response(200, json={"status": "pending"}),
    ]

    await real_cases.TestTicketScenarioExceptionStates().test_pause_request_then_confirm(client)

    assert [(e["step"], e["value"]) for e in attached] == [
        ("accept", "in_progress"), ("request", "pending_requested"), ("confirm", "pending"),
    ]
