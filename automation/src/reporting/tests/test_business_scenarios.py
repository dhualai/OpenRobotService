import json
from unittest.mock import AsyncMock

import allure
import httpx
import pytest

from automation.src.reporting import business_report, standalone_report
from automation.src.reporting.gate_verdict import merge_allure_artifacts
from automation.src.reporting.business_scenarios import (
    EVIDENCE_ATTACHMENT,
    SCENARIO_LABEL,
    SCENARIOS,
    SOURCE_LABELS,
    USP_USAGE_ISSUE,
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


@pytest.mark.parametrize(("last_status", "expected"), [
    ("passed", "通过"),
    ("failed", "失败"),
    ("missing", "未验证"),
])
def test_service_ticket_history_checks_all_steps_and_never_counts_towards_gate(
    tmp_path, last_status, expected,
):
    history = tmp_path / "history"
    history.mkdir()
    token = "secret-from-a-ui-assertion"
    scenario = "service-ticket-lifecycle"
    steps = []
    for key in SCENARIOS[scenario].steps:
        status = last_status if key == "S17" else "passed"
        fixture = f"abcdef{key[1:]}-attachment.json"
        (history / fixture).write_text(json.dumps({
            "step_id": key, "assertion": {"result": "通过", "actual": token},
        }), encoding="utf-8")
        steps.append({
            "name": f"{key} 步骤", "status": status,
            "attachments": [
                {"name": f"{key}-断言与响应摘要", "source": fixture},
            ] if status != "missing" else [],
        })
    name = "test_call_qa_to_ticket_close_regression"
    payload = {
        "name": "测试环境双角色工单闭环", "fullName": f"tests.ui.{name}#{name}",
        "status": "passed", "steps": steps, "start": 1795423036000,
        "statusDetails": {"message": token},
    }
    (history / "case-result.json").write_text(json.dumps(payload), encoding="utf-8")
    (history / "other-result.json").write_text(json.dumps({
        **payload, "fullName": "unrelated", "start": payload["start"] + 10,
    }), encoding="utf-8")
    report = tmp_path / "business.html"
    business_report.main([
        "--results", str(tmp_path / "empty"), "--expect-gate",
        "--service-account-ui-results", str(history), "--out", str(report),
    ])

    html = report.read_text(encoding="utf-8")
    card = _scenario_html(html, scenario)
    assert f'">{expected}</span>' in card
    assert "测试站独立实跑" in card and "UTC+8" in card
    assert card.count("<tr>") == 18
    assert ("再次打开仍显示已关闭" in card) == (last_status == "passed")
    assert "Test Gate 判定：未验收" in html and "结果总数 0" in html
    assert token not in html
    assert "未验证" in _scenario_html(html, "service-learning-progress")


def test_service_account_rule_artifact_shows_only_outcomes_and_keeps_source(tmp_path):
    artifacts = tmp_path / "artifacts"
    observer = artifacts / "allure-results-service-account-rules"
    observer.mkdir(parents=True)
    scenario = "service-knowledge-finance"
    token = "secret-from-backend-rule"
    name = _write_case(
        observer, "归档规则", scenario, "passed",
        [(key, None, None) for key in SCENARIOS[scenario].steps],
        uid="a0",
    )
    for attachment in observer.glob("*-attachment.json"):
        data = json.loads(attachment.read_text(encoding="utf-8"))
        data.update(outcome="passed", raw=token)
        attachment.write_text(json.dumps(data), encoding="utf-8")
    merged = tmp_path / "merged"
    merge_allure_artifacts(artifacts, merged)
    sources = json.loads((merged / SOURCES_FILE).read_text(encoding="utf-8"))
    output = tmp_path / "business.html"
    business_report.main(["--results", str(merged), "--out", str(output)])

    html = output.read_text(encoding="utf-8")
    assert sources[name] == SOURCE_LABELS["service-account-rules"]
    card = _scenario_html(html, scenario)
    assert "服务号后台规则检查（观察项）" in card
    assert 'class="business-status success">通过' in card
    assert "<th>角色</th><th>动作</th><th>预期结果</th><th>实际结果</th>" in card
    assert 'href="#case-a0"' in card and 'id="case-a0"' in html
    assert "知识已发布，三类奖励可申领" in card
    assert token not in html


def test_business_overview_lists_service_account_flows_and_preserves_source_groups(tmp_path):
    incident = _write_case(
        tmp_path, "提单与接单", "incident-create", "passed",
        [("create", 200, USP_USAGE_ISSUE), ("accept", 200, "in_progress")],
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
    overview = html.split("业务场景一览", 1)[1].split("</section>", 1)[0]
    assert overview.count('class="business-scenario"') == 5
    assert overview.index('id="scenario-service-ticket-lifecycle"') < overview.index(
        'id="scenario-service-knowledge-finance"'
    )
    for scenario_id in ("incident-create", "work-order-pause", "admin-permissions"):
        assert f'id="scenario-{scenario_id}"' not in html
    assert 'id="case-a0"' in html and 'id="case-a1"' in html
    assert "后台权限检查" in html

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
    finance = _write_case(
        tmp_path, "归档规则", "service-knowledge-finance", "passed", []
    )
    challenge = _write_case(
        tmp_path, "高价值问题", "service-challenge", "skipped", []
    )
    (tmp_path / SOURCES_FILE).write_text(json.dumps({
        finance: SOURCE_LABELS["service-account-rules"],
        challenge: SOURCE_LABELS["service-account-rules"],
    }), encoding="utf-8")
    output = tmp_path / "business.html"
    business_report.main(["--results", str(tmp_path), "--out", str(output)])
    html = output.read_text(encoding="utf-8")

    finance_html = _scenario_html(html, "service-knowledge-finance")
    assert "未验证" in finance_html and "步骤证据不完整" in finance_html
    assert "未记录实际结果" in finance_html
    assert "本次用例已跳过" in _scenario_html(html, "service-challenge")
    progress_html = _scenario_html(html, "service-learning-progress")
    assert "未验证" in progress_html and "服务号测试站页面（计划）" in progress_html


def test_evidence_omits_unrecognized_response_values(monkeypatch):
    attached = []
    monkeypatch.setattr(allure, "attach", lambda data, **kwargs: attached.append(data))
    token = "synthetic-secret-in-provider-status"
    response = httpx.Response(200, json={"title": token, "access_token": token})

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
