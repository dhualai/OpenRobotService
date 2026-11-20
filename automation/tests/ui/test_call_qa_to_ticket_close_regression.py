"""Complete UI regression: U1 submits, U2 resolves, U1 closes."""

from __future__ import annotations

import secrets
from datetime import datetime

import allure
import pytest
from playwright.sync_api import expect

from automation.src.ui_regression.capture import NetworkCapture
from automation.src.ui_regression.cleanup import CleanupManager
from automation.src.ui_regression.db_cleanup import DatabaseCleanup
from automation.src.ui_regression.page_actions import UiPageActions

from .conftest import UiRegressionRuntime


pytestmark = [pytest.mark.ui, pytest.mark.e2e]


@allure.feature("微信 H5 自动化回归")
@allure.story("摇人问答到工单关闭")
@allure.title("测试环境：U1提单，U2处理并已解决，U1关闭")
def test_call_qa_to_ticket_close_regression(ui_regression_runtime: UiRegressionRuntime):
    runtime = ui_regression_runtime
    run_id = f"ui-{datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    ticket_title = f"自动化链路验证-{run_id}"
    question = (
        "我要给 Leo_test 提 problem，指定处理人 u2_auto，"
        f"标题：{ticket_title}，直接生成工单草稿，不要追问。"
    )

    u1 = UiPageActions(runtime.u1_page, runtime.gateway_url)
    u2 = UiPageActions(runtime.u2_page, runtime.gateway_url)
    capture_u1 = NetworkCapture(runtime.u1_page)
    capture_u2 = NetworkCapture(runtime.u2_page)
    ticket_id: int | None = None

    try:
        with capture_u1.step("S01", "U1", "登录"):
            u1.login(runtime.u1_username, runtime.u1_password)

        with capture_u1.step("S02", "U1", "新建会话"):
            u1.start_new_conversation()

        with capture_u1.step("S03", "U1", "发送固定问题"):
            u1.send_question(question)

        with capture_u1.step("S04", "U1", "打开工单草稿"):
            u1.ensure_ticket_draft_modal()

        with capture_u1.step("S05", "U1", "确认提交工单"):
            ticket_id = u1.confirm_ticket(ticket_title)

        with capture_u2.step("S07", "U2", "登录并进入系统任务"):
            u2.login(runtime.u2_username, runtime.u2_password)
            u2.open_system_tasks()

        with capture_u2.step("S08", "U2", "搜索本次工单"):
            u2.search_ticket(ticket_id, timeout_ms=90_000)

        with capture_u2.step("S09", "U2", "打开工单详情"):
            u2.open_ticket(ticket_id)

        with capture_u2.step("S10", "U2", "确认接单（new -> in_progress）"):
            u2.accept_current_step()
            expect(u2.page.get_by_test_id("task-status")).to_have_attribute(
                "data-status",
                "in_progress",
            )

        with capture_u2.step("S11", "U2", "完成初步诊断阶段（等待 U1 确认）"):
            u2.complete_current_step(next_step_name="临时解决")

        with capture_u1.step("S12", "U1", "打开工单并确认临时解决（当前阶段已一致）"):
            u1.open_system_tasks()
            u1.search_ticket(ticket_id)
            u1.open_ticket(ticket_id)
            u1.accept_current_step()

        with capture_u2.step("S13", "U2", "完成临时解决阶段（等待 U1 确认）"):
            u2.open_ticket_fresh(ticket_id)
            u2.complete_current_step(next_step_name="最终解决")

        with capture_u1.step("S14", "U1", "确认最终解决阶段（当前阶段已一致）"):
            u1.open_ticket_fresh(ticket_id)
            u1.accept_current_step()

        with capture_u2.step("S15", "U2", "提交已解决（in_progress -> resolved）"):
            u2.open_ticket_fresh(ticket_id)
            u2.resolve_ticket("自动化回归验证完成，问题已解决。")
            expect(u2.page.get_by_test_id("task-status")).to_have_attribute(
                "data-status",
                "resolved",
            )

        with capture_u1.step("S16", "U1", "确认工单已解决（resolved）"):
            u1.open_ticket_fresh(ticket_id)
            expect(u1.page.get_by_test_id("task-status")).to_have_attribute(
                "data-status",
                "resolved",
            )

        with capture_u1.step("S17", "U1", "确认关闭（resolved -> closed）"):
            u1.close_ticket()
            expect(u1.page.get_by_test_id("task-status")).to_have_attribute(
                "data-status",
                "closed",
            )
    finally:
        db_cleanup = (
            DatabaseCleanup(runtime.db_cleanup_config)
            if runtime.db_cleanup_config is not None
            else None
        )
        cleanup = CleanupManager(
            runtime.backend_url,
            admin_username=runtime.cleanup_username,
            admin_password=runtime.cleanup_password,
            u1_username=runtime.u1_username,
            u1_password=runtime.u1_password,
            db_cleanup=db_cleanup,
        )
        with allure.step("清理本次测试数据"):
            result = cleanup.cleanup(
                ticket_id=ticket_id,
                conversation_id=u1.conversation_id,
            )
            if result.warnings:
                allure.attach(
                    "\n".join(
                        f"{warning.resource}: {warning.message}"
                        for warning in result.warnings
                    ),
                    name="清理告警",
                    attachment_type=allure.attachment_type.TEXT,
                )
