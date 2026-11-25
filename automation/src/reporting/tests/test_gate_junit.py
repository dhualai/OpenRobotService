import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from automation.src.reporting import business_report, gate_junit, gate_verdict


def test_export_only_stable_case_ids_and_statuses(tmp_path):
    secret = "synthetic-junit-param-and-message"
    source = tmp_path / "junit-real.xml"
    destination = tmp_path / "safe-junit" / source.name
    source.write_text(
        f'<testsuites><testsuite name="{secret}">'
        f'<properties><property name="env" value="{secret}"/></properties>'
        f'<testcase name="login[{secret}]" classname="{secret}" file="{secret}" time="3">'
        f'<failure message="{secret}">{secret}</failure><system-out>{secret}</system-out></testcase>'
        f'<testcase name="login[{secret}]" classname="{secret}" file="{secret}">'
        f'<error message="{secret}">{secret}</error></testcase>'
        f'<testcase name="optional[{secret}]"><skipped message="{secret}"/></testcase>'
        f'<testcase name="ok[{secret}]"><system-err>{secret}</system-err></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )

    gate_junit.export_junit(source, destination)
    payload = destination.read_text(encoding="utf-8")
    assert secret not in payload
    root = ET.fromstring(payload)
    assert root.tag == "testsuite" and not root.attrib
    cases = list(root)
    assert [list(case.attrib) for case in cases] == [["name"]] * 4
    assert cases[0].get("name") == cases[1].get("name")
    assert len({case.get("name") for case in cases}) == 3
    assert [[child.tag for child in case] for case in cases] == [
        ["failure"], ["error"], ["skipped"], [],
    ]
    assert all(not node.attrib and not node.text for case in cases for node in case)
    assert gate_verdict._count_junit_cases(root) == gate_verdict._count_junit_cases(
        ET.parse(source).getroot()
    )

    gate_junit.export_junit(source, destination)
    assert destination.read_text(encoding="utf-8") == payload


def test_export_keeps_valid_evidence_when_other_input_is_broken(tmp_path, capsys):
    source = tmp_path / "raw"
    source.mkdir()
    (source / "junit-real.xml").write_text('<testsuite><testcase name="ok"/></testsuite>')
    (source / "junit-contract.xml").write_text("<synthetic-private-parse-fragment")
    destination = tmp_path / "safe"
    destination.mkdir()
    stale = destination / "junit-contract.xml"
    stale.write_text('<testsuite><testcase name="old"/></testsuite>')

    assert gate_junit.main([
        "--input-dir", str(source), "--output-dir", str(destination), "real", "contract",
    ]) == 1
    assert "synthetic-private-parse-fragment" not in capsys.readouterr().out
    assert not stale.exists()
    assert (destination / "junit-real.xml").is_file()


HTTP_FAILURE_CASES = """
import os

import allure
import httpx
import pytest

from automation.config.models import ApiConfig
from automation.src.assertions.response import assert_error_response, assert_json_response
from automation.src.clients.api_client import ApiClient
from automation.src.reporting.api_step import api_step


@allure.feature("Login diagnostics")
@pytest.mark.asyncio
async def test_login_status_mismatch():
    def respond(_request):
        return httpx.Response(201, json={"access_token": os.environ["GATE_SYNTHETIC_TOKEN"]})

    async with ApiClient(ApiConfig(base_url="http://test.local"), transport=httpx.MockTransport(respond)) as api:
        await api_step(api, "POST", "/api/auth/login", expected_status=200)


@allure.feature("Login diagnostics")
def test_invalid_json():
    assert_json_response(httpx.Response(200, text=os.environ["GATE_SYNTHETIC_TOKEN"]))


@allure.feature("Login diagnostics")
def test_error_code_mismatch():
    payload = {"error_code": os.environ["GATE_SYNTHETIC_TOKEN"]}
    assert_error_response(httpx.Response(400, json=payload), 400, error_code="BAD_REQUEST")


@allure.feature("Login diagnostics")
@pytest.mark.asyncio
async def test_auth_exception():
    def respond(_request):
        return httpx.Response(401, json={"access_token": os.environ["GATE_SYNTHETIC_TOKEN"]})

    async with ApiClient(
        ApiConfig(base_url="http://test.local"),
        transport=httpx.MockTransport(respond),
        raise_auth_errors=True,
    ) as api:
        await api.post("/api/auth/login")
"""


def test_http_failure_artifacts_do_not_expose_response_token(tmp_path):
    token = "synthetic-token-for-http-failure-regression"
    pytest_file = tmp_path / "test_http_failure_probe.py"
    pytest_file.write_text(HTTP_FAILURE_CASES, encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    raw_junit = tmp_path / "junit-real.xml"
    allure_dir = tmp_path / "allure"

    repository = Path(__file__).resolve().parents[4]
    completed = subprocess.run(
        [
            sys.executable, "-B", "-m", "pytest", "-c", str(config), "-p",
            "no:cacheprovider", "-q", str(pytest_file),
            f"--junitxml={raw_junit}", f"--alluredir={allure_dir}",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "GATE_SYNTHETIC_TOKEN": token,
            "PYTHONPATH": str(repository),
            "PYTEST_ADDOPTS": "",
        },
        capture_output=True,
        timeout=40,
    )
    assert completed.returncode == 1
    assert token.encode() not in completed.stdout + completed.stderr
    assert token.encode() not in raw_junit.read_bytes()
    assert b"Expected status 200, got 201" in raw_junit.read_bytes()

    safe_junit = tmp_path / "safe-junit" / raw_junit.name
    gate_junit.export_junit(raw_junit, safe_junit)
    assert gate_verdict._count_junit_cases(ET.parse(safe_junit).getroot()) == {"failed": 4}
    assert token.encode() not in safe_junit.read_bytes()
    assert b"Expected status" not in safe_junit.read_bytes()

    files = [path for path in allure_dir.iterdir() if path.is_file()]
    assert len(list(allure_dir.glob("*-result.json"))) == 4
    assert all(token.encode() not in path.read_bytes() for path in files)
    assert any(b"Expected status 200, got 201" in path.read_bytes() for path in files)

    report = tmp_path / "failure-business.html"
    business_report.main(["--results", str(allure_dir), "--out", str(report)])
    html = report.read_text(encoding="utf-8")
    assert token not in html
    assert "Expected status 200, got 201" in html
