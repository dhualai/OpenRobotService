"""High-level browser actions for the UI business-chain scenario."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from playwright.sync_api import (
    Locator,
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    expect,
)


def default_end_time(days: int = 7) -> str:
    """Return the date format accepted by the stage DatePicker."""

    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")


class UiPageActions:
    """Wrap real page interactions without bypassing the frontend."""

    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url.rstrip("/")
        self.conversation_id: int | None = None

    def login(self, username: str, password: str) -> None:
        self.page.goto(f"{self.base_url}/login?debug=true", wait_until="domcontentloaded")
        self.page.get_by_test_id("login-username").fill(username)
        self.page.get_by_test_id("login-password").fill(password)
        self.page.get_by_test_id("login-submit").click()
        self.page.wait_for_url(lambda url: "/login" not in url, timeout=20_000)
        self._wait_for_login_success_toast()

    def start_new_conversation(self) -> None:
        self.conversation_id = None
        self.page.get_by_test_id("chat-new-conversation").click()

    def send_question(self, question: str) -> None:
        self._fill_test_id("chat-input", question)
        with self.page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.rstrip("/").endswith("/api/call/conversations")
            )
        ) as response_info:
            self.page.get_by_test_id("chat-send").first.click()
        conversation_id = response_info.value.json().get("id")
        if conversation_id is not None:
            self.conversation_id = int(conversation_id)
        expect(self.page.get_by_test_id("chat-transfer-ticket")).to_be_visible(
            timeout=30_000
        )

    def ensure_ticket_draft_modal(self) -> None:
        modal = self.page.get_by_test_id("chat-ticket-draft-modal")
        try:
            modal.wait_for(state="visible", timeout=5_000)
            return
        except PlaywrightTimeoutError:
            pass
        self.page.get_by_test_id("chat-transfer-ticket").click(force=True)
        expect(modal).to_be_visible(timeout=30_000)

    def confirm_ticket(self, expected_title: str) -> int:
        expect(self.page.get_by_test_id("chat-ticket-title")).to_have_value(
            expected_title,
            timeout=20_000,
        )
        confirm_button = self.page.get_by_test_id("chat-ticket-confirm")
        expect(confirm_button).to_be_enabled(timeout=10_000)
        response = self._click_and_wait_for_response(
            confirm_button,
            "/api/ai/qa/ticket/confirm",
        )
        if response.status != 200:
            raise AssertionError(f"确认提单失败: HTTP {response.status}")
        data = response.json().get("data") or {}
        ticket_id = data.get("db_id")
        if not ticket_id:
            raise AssertionError("确认提单响应缺少 db_id")
        return int(ticket_id)

    def open_system_tasks(self) -> None:
        self.page.get_by_test_id("nav-item-tasks").click()
        expect(self.page.get_by_test_id("tasks-search")).to_be_visible(
            timeout=20_000
        )

    def search_ticket(self, ticket_id: int, timeout_ms: int = 20_000) -> None:
        self.page.get_by_test_id("tasks-search").fill(str(ticket_id))
        expect(self.page.get_by_test_id(f"task-card-{ticket_id}")).to_be_visible(
            timeout=timeout_ms
        )

    def open_ticket(self, ticket_id: int) -> None:
        self.page.get_by_test_id(f"task-card-{ticket_id}").click()
        expect(self.page.get_by_test_id("task-status")).to_be_visible(
            timeout=20_000
        )

    def open_ticket_fresh(self, ticket_id: int) -> None:
        self.open_system_tasks()
        self.search_ticket(ticket_id)
        self.open_ticket(ticket_id)

    def accept_current_step(self) -> None:
        with self.page.expect_response(
            lambda response: "/respond" in response.url
        ) as response_info:
            self._click_test_id("task-accept")
        if response_info.value.status != 200:
            raise AssertionError(
                f"确认接单失败: HTTP {response_info.value.status}"
            )

    def complete_current_step(
        self,
        *,
        next_step_name: str | None = None,
        end_time: str | None = None,
    ) -> None:
        self._click_test_id("task-complete-step")
        step_select = self.page.get_by_test_id("task-step-next")
        expect(step_select).to_be_visible(timeout=10_000)
        if next_step_name:
            matching_options = step_select.locator("option").filter(
                has_text=next_step_name
            )
            if matching_options.count() == 0:
                available = step_select.locator("option").all_inner_texts()
                raise AssertionError(
                    f"未找到下一阶段「{next_step_name}」，可选阶段：{available}"
                )
            option_value = matching_options.first.get_attribute("value")
            if not option_value:
                raise AssertionError(f"下一阶段「{next_step_name}」缺少 option value")
            step_select.select_option(value=option_value)
        else:
            options = step_select.locator("option:not([disabled])")
            values = options.evaluate_all(
                "(nodes) => nodes.map((node) => node.value).filter(Boolean)"
            )
            if not values:
                raise AssertionError("没有可选的下一阶段")
            step_select.select_option(value=values[0])

        self._fill_date_picker("task-step-endtime", end_time or default_end_time())
        expect(self._click_target("task-step-submit")).to_be_enabled(timeout=10_000)
        with self.page.expect_response(
            lambda response: "/complete-step" in response.url
        ) as response_info:
            self._click_test_id("task-step-submit")
        if response_info.value.status != 200:
            raise AssertionError(
                f"推进阶段失败: HTTP {response_info.value.status}"
            )

    def resolve_ticket(self, summary: str) -> None:
        self._click_test_id("task-resolve")
        self._fill_test_id("task-resolution-summary", summary)
        with self.page.expect_response(
            lambda response: "/status" in response.url
        ) as response_info:
            self._click_test_id("task-resolve-confirm")
        if response_info.value.status != 200:
            raise AssertionError(
                f"提交已解决失败: HTTP {response_info.value.status}"
            )

    def close_ticket(self) -> None:
        with self.page.expect_response(
            lambda response: "/status" in response.url
        ) as response_info:
            self._click_test_id("task-close")
        if response_info.value.status != 200:
            raise AssertionError(
                f"确认关闭失败: HTTP {response_info.value.status}"
            )

    def status_text(self) -> str:
        return self.page.get_by_test_id("task-status").inner_text().strip()

    def _fill_test_id(self, test_id: str, value: str) -> None:
        target = self._input_target(test_id)
        target.fill(value)

    def _fill_date_picker(self, test_id: str, value: str) -> None:
        target = self._input_target(test_id)
        target.click()

        popup = self.page.locator(".ant-picker-dropdown:visible").last
        expect(popup).to_be_visible(timeout=10_000)

        target_date = datetime.strptime(value, "%Y-%m-%d %H:%M").date()
        date_cell = popup.locator(f'td[title="{target_date.isoformat()}"]')
        month_button = (
            popup.locator("button.ant-picker-header-next-btn")
            if target_date >= datetime.now().date()
            else popup.locator("button.ant-picker-header-prev-btn")
        )
        for _ in range(24):
            if date_cell.count():
                break
            month_button.click()
            self.page.wait_for_timeout(300)
        if not date_cell.count():
            raise AssertionError(f"日期控件中找不到目标日期：{target_date.isoformat()}")

        date_cell.first.click()
        ok_button = popup.locator(".ant-picker-ok button")
        expect(ok_button).to_be_enabled(timeout=10_000)
        ok_button.click()
        expect(popup).to_be_hidden(timeout=10_000)

    def _input_target(self, test_id: str) -> Locator:
        locator = self.page.get_by_test_id(test_id).first
        tag_name = locator.evaluate("(element) => element.tagName.toLowerCase()")
        if tag_name in {"input", "textarea"}:
            return locator
        return locator.locator("input, textarea").first

    def _wait_for_login_success_toast(self) -> None:
        toast = self.page.get_by_text("登录成功", exact=True).last
        try:
            expect(toast).to_be_visible(timeout=5_000)
        except AssertionError:
            return
        expect(toast).to_be_hidden(timeout=10_000)

    def _click_test_id(self, test_id: str) -> None:
        target = self._click_target(test_id)
        target.click()

    def _click_target(self, test_id: str) -> Locator:
        marker = self.page.get_by_test_id(test_id).first
        tag_name = marker.evaluate("(element) => element.tagName.toLowerCase()")
        target: Locator = marker
        if tag_name != "button":
            target = marker.locator("xpath=following-sibling::button[1]")
            if target.count() == 0:
                target = marker.locator("xpath=.//button[1]")
        expect(target).to_be_visible(timeout=20_000)
        return target

    def _click_and_wait_for_response(
        self,
        target: Locator,
        url_fragment: str,
        timeout_ms: int = 30_000,
    ) -> Response:
        request_seen = False
        response_holder: dict[str, Response] = {}

        def on_request(request) -> None:
            nonlocal request_seen
            if url_fragment in request.url:
                request_seen = True

        def on_response(response: Response) -> None:
            if url_fragment in response.url:
                response_holder["value"] = response

        self.page.on("request", on_request)
        self.page.on("response", on_response)
        started = time.monotonic()
        deadline = started + timeout_ms / 1000
        retried = False
        try:
            target.click()
            while time.monotonic() < deadline:
                if "value" in response_holder:
                    return response_holder["value"]
                if (
                    not request_seen
                    and not retried
                    and time.monotonic() - started >= 2
                ):
                    target.click()
                    retried = True
                self.page.wait_for_timeout(100)
            raise PlaywrightTimeoutError(
                f"点击后未收到接口响应：{url_fragment}"
            )
        finally:
            self.page.remove_listener("request", on_request)
            self.page.remove_listener("response", on_response)
