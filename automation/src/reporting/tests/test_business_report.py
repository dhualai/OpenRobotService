import json
from fnmatch import fnmatchcase
from pathlib import Path

import allure
import httpx
import pytest
import yaml

from automation.config.models import ApiConfig
from automation.src.clients.api_client import ApiClient
from automation.src.reporting.business_report import main


@pytest.mark.asyncio
async def test_business_report_redacts_login_response(monkeypatch, tmp_path):
    secrets = {
        "access_token": "synthetic-private-access-token",
        "refresh_token": "synthetic-private-refresh-token",
        "cookie": "synthetic-private-session-cookie",
        "api_key": "synthetic-private-api-key",
        "query": "synthetic-private-query-token",
        "redirect_code": "synthetic-private-redirect-code",
        "password": "synthetic-private-password",
    }
    payload = {
        "access_token": secrets["access_token"],
        "sessions": [{"refreshToken": secrets["refresh_token"], "status": "active"}],
        "credentials": {"password": secrets["password"]},
    }
    headers = {
        "Set-Cookie": f"session={secrets['cookie']}; HttpOnly",
        "X-Api-Key": secrets["api_key"],
        "Location": f"/callback?code={secrets['redirect_code']}",
        "X-Request-ID": "trace-485",
    }
    attachments = []

    def capture_attachment(body, name, **_kwargs):
        source = f"{len(attachments)}-attachment.json"
        (tmp_path / source).write_text(body, encoding="utf-8")
        attachments.append({"name": name, "source": source})

    def respond(request):
        assert request.url.params["token"] == secrets["query"]
        assert json.loads(request.content) == {"password": secrets["password"]}
        return httpx.Response(200, json=payload, headers=headers)

    monkeypatch.setattr(allure, "attach", capture_attachment)
    config = ApiConfig(base_url="http://test.local")
    async with ApiClient(config, transport=httpx.MockTransport(respond)) as api:
        response = await api.post(
            "/api/auth/login",
            params={"token": secrets["query"], "locale": "zh-CN"},
            json={"password": secrets["password"]},
        )

    assert response.json() == payload
    assert response.headers["set-cookie"] == headers["Set-Cookie"]

    attachment_data = {
        attachment["name"]: (tmp_path / attachment["source"]).read_text(encoding="utf-8")
        for attachment in attachments
    }
    logged_request = json.loads(attachment_data["Request"])
    assert logged_request["params"] == {"token": "***", "locale": "zh-CN"}
    logged_response = json.loads(attachment_data["Response"])
    assert logged_response["status_code"] == 200
    assert logged_response["url"] == "http://test.local/api/auth/login"
    assert logged_response["headers"]["location"] == "/callback"
    assert logged_response["headers"]["set-cookie"] == "***"
    assert logged_response["headers"]["x-api-key"] == "***"
    assert logged_response["headers"]["x-request-id"] == "trace-485"
    assert logged_response["body"] == {
        "access_token": "***",
        "sessions": [{"refreshToken": "***", "status": "active"}],
        "credentials": "***",
    }

    result = {
        "name": "Synthetic login scenario",
        "status": "passed",
        "steps": [
            {"name": "POST /api/auth/login", "status": "passed", "attachments": attachments}
        ],
    }
    (tmp_path / "login-result.json").write_text(json.dumps(result), encoding="utf-8")
    report_path = tmp_path / "business-report.html"
    main(["--results", str(tmp_path), "--out", str(report_path)])
    report = report_path.read_text(encoding="utf-8")
    assert "Synthetic login scenario" in report
    assert "trace-485" in report
    for secret in secrets.values():
        assert all(secret not in body for body in attachment_data.values())
        assert secret not in report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers_and_body",
    [
        {"text": "synthetic-private-response"},
        {"json": "synthetic-private-response"},
    ],
)
async def test_api_client_omits_unstructured_response_body(monkeypatch, headers_and_body):
    attachments = {}
    monkeypatch.setattr(allure, "attach", lambda body, name, **_: attachments.update({name: body}))
    transport = httpx.MockTransport(lambda _: httpx.Response(400, **headers_and_body))

    async with ApiClient(ApiConfig(base_url="http://test.local"), transport=transport) as api:
        response = await api.get("/api/example")

    assert "synthetic-private-response" in response.text
    detail = json.loads(attachments["Response"])
    assert detail["status_code"] == 400
    assert detail["body"] == "[unstructured response body omitted]"
    assert "synthetic-private-response" not in attachments["Response"]


def test_scenario_artifact_matches_allure_download_and_keeps_results_flat():
    workflow = Path(__file__).resolve().parents[4] / ".github/workflows/test-gate.yml"
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    report_job = jobs["allure-report"]
    download = next(
        step["with"] for step in report_job["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    assert "scenarios" in report_job["needs"]
    assert download["merge-multiple"] is True

    scenario_steps = jobs["scenarios"]["steps"]
    uploads = [
        step for step in scenario_steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    [allure_upload] = [
        step for step in uploads if fnmatchcase(step["with"]["name"], download["pattern"])
    ]
    assert allure_upload["if"] == "always()"
    # A directory-only upload puts its files at the artifact root, where Allure expects them.
    allure_path = allure_upload["with"]["path"]
    assert allure_path == "automation/output/allure-results-scenarios"
    assert any(f"--alluredir={allure_path}" in step.get("run", "") for step in scenario_steps)
    assert any(
        step["with"]["path"] == "automation/output/junit-scenarios.xml" for step in uploads
    )
