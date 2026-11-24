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
ARTICLE_LIST = "/t/api/tasks/learning/articles"
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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
    context = runtime.browser.new_context(
        viewport={"width": 430, "height": 932}, locale="zh-CN",
    )
    page = context.new_page()
    user = UiPageActions(page, runtime.gateway_url)
    origin = urlsplit(runtime.gateway_url).netloc
    observed = {}
    landing = f"{runtime.gateway_url}/tasks/learning"
    list_visible = False
    course_id = ""
    title = ""
    article_version = None
    question_ids = []
    payloads = {}

    def record(response):
        url = urlsplit(response.url)
        if (
            url.netloc != origin or response.request.method != "GET"
            or "/api/" not in url.path or not re.search(r"learn|course|lesson", url.path, re.I)
            or not response.ok
        ):
            return
        try:
            payload = response.json()
            fields = sorted(_source_fields(payload))
        except (ValueError, TypeError):
            return
        observed[url.path] = fields
        payloads[url.path] = payload.get("data", payload) if isinstance(payload, dict) else payload

    user.login(runtime.u1_username, runtime.u1_password)
    page.on("response", record)
    try:
        page.goto(landing, wait_until="domcontentloaded")
        try:
            page.get_by_text("学习中心", exact=True).first.wait_for(timeout=12_000)
            list_visible = True
            page.locator("article.learner-card").first.wait_for(timeout=10_000)
            listing = payloads.get(ARTICLE_LIST, {})
            items = listing.get("items", []) if isinstance(listing, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                candidate = item.get("id")
                if isinstance(candidate, str) and IDENTIFIER.fullmatch(candidate):
                    course_id = candidate
                    title = str(item.get("title", ""))[:150]
                    article_version = item.get("active_version")
                    break
            if course_id:
                route = f"{ARTICLE_LIST}/{course_id}"
                with page.expect_response(
                    lambda response: urlsplit(response.url).path == route, timeout=12_000,
                ):
                    page.goto(f"{landing}/{course_id}", wait_until="domcontentloaded")
                detail = payloads.get(route, {})
                for question in detail.get("questions", []) if isinstance(detail, dict) else []:
                    key = question.get("id") if isinstance(question, dict) else None
                    if isinstance(key, str) and IDENTIFIER.fullmatch(key):
                        question_ids.append(key)
        except PlaywrightTimeoutError:
            pass
    finally:
        page.remove_listener("response", record)
        context.close()
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            "learning_center_visible": list_visible,
            "course_id": course_id,
            "course_title": title,
            "article_version": article_version,
            "question_ids": question_ids,
            "observed_source_fields": observed,
        }, ensure_ascii=False), encoding="utf-8")
