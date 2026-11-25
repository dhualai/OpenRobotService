"""Playwright acceptance case based on the service account's tested workflow."""

import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime

import allure
import pytest
from playwright.sync_api import expect

from automation.src.reporting.business_scenarios import SCENARIOS, record_check_outcome
from automation.src.ui_regression.cleanup import CleanupManager
from automation.src.ui_regression.db_cleanup import DatabaseCleanup
from automation.src.ui_regression.page_actions import UiPageActions, default_end_time

from .conftest import UiRegressionRuntime


pytestmark = [pytest.mark.ui, pytest.mark.e2e]
SCENARIO_ID = "service-ticket-lifecycle"
PROJECT = "摇人吧服务号-测试"


@contextmanager
def _step(step_id):
    step = SCENARIOS[SCENARIO_ID].steps[step_id]
    with allure.step(f"{step_id} [{step.role}] {step.action}"):
        yield
        record_check_outcome(SCENARIO_ID, step_id, "passed")


def _prepare_draft(page, title, description):
    modal = page.get_by_test_id("chat-ticket-draft-modal")
    project = modal.get_by_role(
        "button", name=re.compile(rf"^{re.escape(PROJECT)}\s*▾?$"),
    )
    expect(project).to_be_visible(timeout=20_000)
    expect(modal.get_by_role("checkbox", name=re.compile("我的项目不在"))).not_to_be_checked()
    ticket_type = modal.get_by_role("combobox").filter(
        has=page.locator('option[value="problem"]'),
    )
    ticket_type.select_option("problem")
    stage = modal.get_by_role("combobox").filter(
        has=page.locator("option").filter(has_text=re.compile(r"^初步诊断$")),
    )
    stage.select_option(label="初步诊断", timeout=20_000)
    modal.get_by_test_id("chat-ticket-title").fill(title)
    modal.get_by_test_id("chat-ticket-description").fill(description)
    expect(project).to_be_visible()
    expect(ticket_type).to_have_value("problem")
    expect(stage.locator("option:checked")).to_have_text("初步诊断")


def _expect_status(user, status):
    expect(user.page.get_by_test_id("task-status")).to_have_attribute(
        "data-status", status, timeout=20_000,
    )


def _expect_stage(user, stage, agreement):
    expect(user.page.get_by_test_id("task-current-step")).to_contain_text(stage, timeout=20_000)
    expect(user.page.get_by_text(agreement, exact=True)).to_be_visible(timeout=20_000)


def _reopen(user, ticket_id):
    user.page.goto(f"{user.base_url}/tasks/{ticket_id}", wait_until="domcontentloaded")
    expect(user.page.get_by_test_id("task-status")).to_be_visible(timeout=20_000)


@allure.feature("摇人吧服务号")
@allure.title("测试环境双角色工单闭环")
def test_service_account_ticket_lifecycle(ui_regression_runtime: UiRegressionRuntime):
    runtime = ui_regression_runtime
    assignee = os.getenv("UI_REGRESSION_U2_DISPLAY_NAME")
    if not assignee:
        pytest.skip("UI_REGRESSION_U2_DISPLAY_NAME is required")
    run_id = f"ui-{datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    title = f"自动化链路验证-{run_id}"
    description = (
        f"[指定处理人：{assignee}]\n"
        "USP 使用问题。模拟机器人无法启动，故障码 E1001，已尝试重启仍无效。"
        "仅用于测试，无现场设备。"
    )
    question = (
        f"请为项目 Leo_test（{PROJECT}）生成报障（problem）工单草稿。"
        f"标题：{title}。{description} 请直接生成工单草稿。"
    )
    resolution = "测试环境双角色回归，模拟故障已解决。"
    reporter = UiPageActions(runtime.u1_page, runtime.gateway_url)
    handler = UiPageActions(runtime.u2_page, runtime.gateway_url)
    ticket_id = None

    try:
        with _step("S01"):
            reporter.login(runtime.u1_username, runtime.u1_password)
            expect(reporter.page.get_by_test_id("nav-item-tasks")).to_be_visible()
        with _step("S02"):
            reporter.start_new_conversation()
            expect(reporter.page.get_by_test_id("chat-send").first).to_be_disabled()
        with _step("S03"):
            reporter.send_question(question)
            assert reporter.conversation_id is not None
        with _step("S04"):
            reporter.ensure_ticket_draft_modal(project_name=PROJECT)
            _prepare_draft(reporter.page, title, description)
        with _step("S05"):
            ticket_id = reporter.confirm_ticket(title)
            overview = reporter.page.get_by_test_id(f"chat-ticket-overview-{ticket_id}")
            expect(overview).to_be_visible(timeout=20_000)
        with _step("S06"):
            expect(overview.get_by_text(f"已派单 · {assignee}", exact=True)).to_be_visible(
                timeout=90_000,
            )
        with _step("S07"):
            handler.login(runtime.u2_username, runtime.u2_password)
            handler.open_system_tasks()
            handler.page.get_by_role("button", name=re.compile(r"^待我处理(?:\s*\d+)?$")).click()
        with _step("S08"):
            handler.search_ticket(ticket_id, timeout_ms=45_000)
        with _step("S09"):
            handler.open_ticket(ticket_id)
            expect(handler.page.get_by_test_id("task-assignee")).to_have_text(assignee)
            _expect_status(handler, "new")
            _expect_stage(handler, "初步诊断", "待你确认")
        with _step("S10"):
            handler.accept_current_step()
            _expect_status(handler, "in_progress")
            _expect_stage(handler, "初步诊断", "已达成一致")
        with _step("S11"):
            handler.complete_current_step(next_step_name="临时解决", end_time=default_end_time(2))
            _expect_stage(handler, "临时解决", "待对方确认")
        with _step("S12"):
            _reopen(reporter, ticket_id)
            _expect_stage(reporter, "临时解决", "待你确认")
            reporter.accept_current_step()
            _expect_stage(reporter, "临时解决", "已达成一致")
        with _step("S13"):
            _reopen(handler, ticket_id)
            _expect_stage(handler, "临时解决", "已达成一致")
            handler.complete_current_step(next_step_name="最终解决", end_time=default_end_time(3))
            _expect_stage(handler, "最终解决", "待对方确认")
        with _step("S14"):
            _reopen(reporter, ticket_id)
            _expect_stage(reporter, "最终解决", "待你确认")
            reporter.accept_current_step()
            _expect_stage(reporter, "最终解决", "已达成一致")
        with _step("S15"):
            _reopen(handler, ticket_id)
            _expect_stage(handler, "最终解决", "已达成一致")
            handler.resolve_ticket(resolution)
            _expect_status(handler, "resolved")
        with _step("S16"):
            _reopen(reporter, ticket_id)
            _expect_status(reporter, "resolved")
            expect(reporter.page.get_by_text(resolution, exact=True).first).to_be_visible()
        with _step("S17"):
            reporter.close_ticket()
            _reopen(reporter, ticket_id)
            _expect_status(reporter, "closed")
    finally:
        if ticket_id is not None or reporter.conversation_id is not None:
            cleanup = CleanupManager(
                runtime.backend_url,
                admin_username=runtime.cleanup_username,
                admin_password=runtime.cleanup_password,
                u1_username=runtime.u1_username,
                u1_password=runtime.u1_password,
                db_cleanup=DatabaseCleanup(runtime.db_cleanup_config) if runtime.db_cleanup_config else None,
            )
            try:
                with allure.step("清理本次测试数据"):
                    result = cleanup.cleanup(
                        ticket_id=None if os.getenv("UI_REGRESSION_DEPLOYED") == "1" else ticket_id,
                        conversation_id=reporter.conversation_id,
                    )
                    if result.warnings:
                        allure.attach("部分测试数据待清理", name="清理告警")
            finally:
                cleanup.client.close()
