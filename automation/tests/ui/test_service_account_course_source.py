"""Read-only probe for the origin of a published test course's questions."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation.src.ui_regression.page_actions import UiPageActions


pytestmark = [pytest.mark.ui, pytest.mark.e2e]
SOURCE_FIELDS = {
    "question_id", "questionId", "source_question_id", "sourceQuestionId",
    "bank_question_id", "bankQuestionId", "article_id", "articleId",
    "article_version", "articleVersion",
}


def _source_fields(payload):
    found = set()
    if isinstance(payload, dict):
        found.update(SOURCE_FIELDS.intersection(payload))
        for value in payload.values():
            found.update(_source_fields(value))
    elif isinstance(payload, list):
        for item in payload:
            found.update(_source_fields(item))
    return found


def test_published_course_question_source(ui_regression_runtime):
    output = os.getenv("UI_COURSE_PROBE_OUTPUT")
    if os.getenv("UI_REGRESSION_DEPLOYED") != "1" or not output:
        pytest.skip("The course probe requires the deployed test site")

    runtime = ui_regression_runtime
    user = UiPageActions(runtime.u1_page, runtime.gateway_url)
    page = user.page
    origin = urlsplit(runtime.gateway_url).netloc
    observed = {}
    landing = f"{runtime.gateway_url}/tasks/learning"
    list_visible = False
    course_id = ""

    def record(response):
        url = urlsplit(response.url)
        if (
            url.netloc != origin or response.request.method != "GET"
            or "/api/" not in url.path or not re.search(r"learn|course|lesson", url.path, re.I)
            or not response.ok
        ):
            return
        try:
            fields = sorted(_source_fields(response.json()))
        except (ValueError, TypeError):
            return
        observed[url.path] = fields

    user.login(runtime.u1_username, runtime.u1_password)
    page.on("response", record)
    try:
        page.goto(landing, wait_until="domcontentloaded")
        try:
            page.get_by_text("学习中心", exact=True).first.wait_for(timeout=12_000)
            list_visible = True
            pattern = re.compile(r"^/t/app/tasks/learning/([A-Za-z0-9_-]{1,64})/?$")
            for href in page.locator("a[href]").evaluate_all("(links) => links.map(link => link.href)"):
                parsed = urlsplit(href)
                match = pattern.fullmatch(parsed.path)
                if parsed.netloc == origin and match:
                    course_id = match.group(1)
                    page.goto(f"{landing}/{course_id}", wait_until="networkidle", timeout=20_000)
                    break
        except PlaywrightTimeoutError:
            pass
    finally:
        page.remove_listener("response", record)
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            "learning_center_visible": list_visible,
            "course_id": course_id,
            "observed_source_fields": observed,
        }, ensure_ascii=False), encoding="utf-8")
