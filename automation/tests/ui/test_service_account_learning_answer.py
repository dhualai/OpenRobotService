"""Submit answers to the confirmed course with the staging learner account."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from automation.src.ui_regression.page_actions import UiPageActions


pytestmark = [pytest.mark.ui, pytest.mark.e2e]
COURSE_ID = "1bf21e0f66a14c2db18d18a7cbf3ce6c"
COURSE_TITLE = "按钮路径交互优化：转工单入口的反馈闭环"
COURSE_VERSION = 1
ARTICLE_LIST = "/t/api/tasks/learning/articles"


def _capture(page, origin, path, method="GET"):
    return page.expect_response(
        lambda response: urlsplit(response.url).netloc == origin
        and urlsplit(response.url).path == path and response.request.method == method,
        timeout=20_000,
    )


def _data(response):
    assert response.ok, f"Learner service returned HTTP {response.status}"
    payload = response.json()
    return payload.get("data", payload)


def test_confirmed_course_submission_scoring_and_progress(ui_regression_runtime):
    output = os.getenv("UI_LEARNER_EVIDENCE_OUTPUT")
    if os.getenv("UI_REGRESSION_DEPLOYED") != "1" or not output:
        pytest.skip("Requires the opted-in deployed test site learner run")

    runtime = ui_regression_runtime
    page = runtime.u1_page
    origin = urlsplit(runtime.gateway_url).netloc
    landing = f"{runtime.gateway_url}/tasks/learning"
    course_url = f"{landing}/{COURSE_ID}"
    article_route = f"{ARTICLE_LIST}/{COURSE_ID}"
    evidence = {
        "course_id": COURSE_ID, "course_title": COURSE_TITLE, "course_version": COURSE_VERSION,
        "total": 0, "submitted": 0, "scored": 0, "correct": 0, "persisted": 0,
        "list_progress": "unknown", "detail_progress": "unknown", "outcome": "failed",
    }

    try:
        UiPageActions(page, runtime.gateway_url).login(runtime.u1_username, runtime.u1_password)
        with _capture(page, origin, article_route) as captured:
            page.goto(course_url, wait_until="domcontentloaded")
        detail = _data(captured.value)
        assert detail["article"]["id"] == COURSE_ID
        assert detail["article"]["active_version"] == COURSE_VERSION
        questions = detail["questions"]
        assert len(questions) == 2
        evidence["total"] = len(questions)

        for index, question in enumerate(questions):
            page.locator(".learner-progress-dot").nth(index).click()
            option = page.locator(".learner-option").first
            selected = option.locator(".learner-option__key").inner_text().strip()
            option.click()
            with _capture(page, origin, f"{article_route}/answer", "POST") as captured:
                page.locator(".learner-quiz__submit").click()
            evidence["submit_http_status"] = captured.value.status
            result = _data(captured.value)
            evidence["submitted"] += 1
            submission = captured.value.request.post_data_json
            assert submission.get("question_id") == question["id"]
            assert submission.get("answers") == [selected]
            correct = result["is_correct"]
            expected = set(result["correct_answer"]) == {selected}
            assert correct is expected, "Score disagreed with the grading key returned after submission"
            expect(page.locator(".learner-quiz__explanation-title")).to_contain_text(
                "回答正确" if expected else "答案不对",
            )
            evidence["scored"] += 1
            evidence["correct"] += int(correct)

        with _capture(page, origin, ARTICLE_LIST) as captured:
            page.goto(landing, wait_until="domcontentloaded")
        items = _data(captured.value)["items"]
        course = next(item for item in items if item["id"] == COURSE_ID)
        evidence["persisted"] = course["answered_count"]
        card = page.locator("article.learner-card").filter(
            has=page.get_by_role("heading", name=COURSE_TITLE, exact=True),
        )
        expect(card).to_contain_text("已完成")
        assert course["answered_count"] == course["total_count"] == evidence["total"]
        assert course["correct_count"] == evidence["correct"]
        evidence["list_progress"] = "passed"

        with _capture(page, origin, article_route) as captured:
            page.goto(course_url, wait_until="domcontentloaded")
        reopened = _data(captured.value)
        assert reopened["article"]["active_version"] == COURSE_VERSION
        assert all(question["has_answer"] for question in reopened["questions"])
        footer = page.locator(".learner-footer__count")
        expect(footer).to_be_visible()
        shown = re.fullmatch(r"(\d+)\s*/\s*(\d+)", footer.inner_text().strip())
        if shown:
            evidence["detail_answered"] = int(shown[1])
        evidence["detail_progress"] = "failed"
        expect(footer).to_contain_text(f'{evidence["total"]} / {evidence["total"]}')
        evidence["detail_progress"] = "passed"
        evidence["outcome"] = "passed"
    finally:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
