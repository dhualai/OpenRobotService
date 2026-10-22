"""Unit tests for the deterministic ticket script renderer."""

import json

from automation.src.ticket_pipeline.models import TicketCandidate
from automation.src.ticket_pipeline.script_renderer import (
    collect_request_paths,
    render_script,
    validate_case_requests,
)


def _ticket():
    return TicketCandidate(
        id=837,
        title="限制用户授权及安装包下载权限",
        description="描述",
        task_type="feature",
        status="new",
        project_id="Leo_test",
        project_name="摇人吧服务号",
    )


def _case(case_id="TC001", requests=None):
    return {
        "id": case_id,
        "title": "已授权用户可查看 licence",
        "type": "positive",
        "module": "用户授权",
        "requests": requests
        if requests is not None
        else [
            {
                "step": "Step 1: 登录",
                "method": "POST",
                "path": "/api/auth/login",
                "body": {"username": "testadmin", "password": "admin123"},
                "expected_status": 200,
                "auth": False,
            },
            {
                "step": "Step 2: 查询任务",
                "method": "GET",
                "path": "/api/tasks/1",
                "expected_status": 200,
                "expected_fields": {"id": 1},
            },
        ],
    }


def test_render_script_produces_importable_module():
    script = render_script(_ticket(), [_case()])

    assert "import httpx" in script
    assert "import allure" in script
    assert "async def _api(" in script
    assert "@allure.feature(" in script
    assert "@pytest.mark.api" in script
    assert "assert_status_code" in script
    assert "def test_tc001(self, client, auth, cleanup):" in script


def test_render_script_omits_auth_fixture_when_not_needed():
    script = render_script(
        _ticket(),
        [
            _case(
                requests=[
                    {
                        "method": "GET",
                        "path": "/api/tasks",
                        "expected_status": 200,
                        "auth": False,
                    }
                ]
            )
        ],
    )

    assert "def test_tc001(self, client):" in script
    assert "headers=auth" not in script


def test_render_script_uses_auth_headers_when_request_needs_auth():
    script = render_script(_ticket(), [_case()])

    assert "def test_tc001(self, client, auth, cleanup):" in script
    assert "headers=auth" in script


def test_render_script_requires_credentials_for_a_real_backend():
    script = render_script(_ticket(), [_case()])

    assert "TEST_USERNAME / TEST_PASSWORD are required" in script
    assert 'os.getenv("TEST_USERNAME")' in script
    assert 'os.getenv("TEST_PASSWORD")' in script


def test_render_script_omits_cleanup_fixture_without_post():
    script = render_script(
        _ticket(),
        [
            _case(
                requests=[
                    {
                        "method": "GET",
                        "path": "/api/tasks",
                        "expected_status": 200,
                        "auth": False,
                    }
                ]
            )
        ],
    )

    assert "def test_tc001(self, client):" in script


def test_render_script_uses_soft_field_assertions():
    script = render_script(_ticket(), [_case()])

    assert "STRICT_FIELDS" in script
    assert "assert_dict_contains_subset(payload, expected_fields)" in script
    assert "软断言" in script


def test_render_script_records_created_resources_for_cleanup():
    script = render_script(_ticket(), [_case()])

    assert "_CREATED.append" in script
    assert "async def cleanup(client):" in script
    assert 'client.delete(f"{path}/{resource_id}"' in script


def test_render_script_embeds_body_and_expected_fields():
    script = render_script(_ticket(), [_case()])

    assert "json=" in script
    assert "expected_fields=" in script
    assert "expected_status=200" in script


def test_render_script_escapes_quotes_in_titles():
    case = _case()
    case["title"] = 'he said "hi"'
    script = render_script(_ticket(), [case])

    assert 'he said \\"hi\\"' in script


def test_render_script_escapes_triple_quotes_in_ticket_title():
    ticket = _ticket()
    ticket.title = 'bad """ title'
    script = render_script(ticket, [_case()])

    assert '""" bad' not in script
    assert "'''" in script


def test_render_script_skips_cases_without_requests():
    script = render_script(_ticket(), [{"id": "TC001", "title": "空用例"}])

    assert "def test_" not in script


def test_collect_request_paths_deduplicates():
    cases = [
        _case(requests=[{"method": "GET", "path": "/api/tasks"}]),
        _case(
            case_id="TC002",
            requests=[
                {"method": "GET", "path": "/api/tasks"},
                {"method": "GET", "path": "/api/admin/users"},
            ],
        ),
    ]

    assert collect_request_paths(cases) == ["/api/tasks", "/api/admin/users"]


def test_validate_case_requests_accepts_spec_paths():
    spec = {"paths": {"/api/tasks": {}, "/api/tasks/{task_id}": {}}}
    cases = [
        _case(
            requests=[
                {"method": "GET", "path": "/api/tasks"},
                {"method": "GET", "path": "/api/tasks/42"},
            ]
        )
    ]

    assert validate_case_requests(cases, spec) == []


def test_validate_case_requests_rejects_unknown_path():
    spec = {"paths": {"/api/tasks": {}}}
    cases = [_case(requests=[{"method": "GET", "path": "/api/licence"}])]

    issues = validate_case_requests(cases, spec)

    assert len(issues) == 1
    assert "/api/licence" in issues[0]


def test_validate_case_requests_without_spec_is_permissive():
    assert validate_case_requests([_case()], None) == []
    assert validate_case_requests([_case()], {"paths": {}}) == []


def test_rendered_script_is_valid_python():
    import py_compile
    import tempfile
    from pathlib import Path

    script = render_script(_ticket(), [_case()])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test_gen.py"
        path.write_text(script, encoding="utf-8")
        py_compile.compile(str(path), doraise=True)


def test_rendered_script_json_payload_is_parseable():
    script = render_script(
        _ticket(),
        [
            _case(
                requests=[
                    {
                        "method": "POST",
                        "path": "/api/tasks",
                        "body": {"title": "新工单"},
                        "expected_status": 201,
                    }
                ]
            )
        ],
    )

    marker = 'json={"title"'
    start = script.index(marker) + len("json=")
    end = script.index(",\n", start)
    payload = script[start:end].strip().rstrip(",")
    assert json.loads(payload) == {"title": "新工单"}
