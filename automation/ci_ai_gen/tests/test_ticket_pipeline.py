"""Unit tests for the ticket-driven case generator."""

import json
import py_compile

import pytest

from automation.ci_ai_gen.ticket_pipeline import (
    TicketCaseGenerator,
    extract_cases,
)
from automation.src.ticket_pipeline.models import TicketCandidate

ANALYSIS = """# 工单概述
权限限制需求

# 功能点清单
- REQ-01 限制用户授权

# 业务规则
- 无权限用户不能下载安装包

# 状态与权限
- 普通用户无权限

# 测试重点
- 权限校验

# 风险与边界
- 白名单用户

# 待确认问题
- 限制范围待确认
"""

_CASES_JSON = json.dumps(
    [
        {
            "id": "TC001",
            "title": "已授权用户可查看 licence",
            "type": "positive",
            "module": "用户授权",
            "requests": [
                {
                    "step": "Step 1: 查询任务",
                    "method": "GET",
                    "path": "/api/tasks/1",
                    "expected_status": 200,
                    "expected_fields": {"id": 1},
                }
            ],
        }
    ],
    ensure_ascii=False,
)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, system_prompt, user_prompt, max_tokens=1024):
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


def _ticket():
    return TicketCandidate(
        id=837,
        title="限制用户授权及安装包下载权限",
        description="需求描述",
        task_type="feature",
        status="new",
        project_id="Leo_test",
        project_name="摇人吧服务号",
    )


def test_extract_cases_normalizes_defaults():
    cases = extract_cases('[{"title": "标题"}]')

    assert cases[0]["id"] == "TC001"
    assert cases[0]["type"] == "positive"
    assert cases[0]["priority"] == "P1"
    assert cases[0]["requests"] == []
    assert cases[0]["source"] == "ticket"


def test_extract_cases_rejects_empty_output():
    with pytest.raises(ValueError, match="non-empty JSON array"):
        extract_cases("[]")


def test_extract_cases_from_fenced_json():
    raw = f"```json\n{_CASES_JSON}\n```"
    cases = extract_cases(raw)
    assert cases[0]["title"] == "已授权用户可查看 licence"
    assert cases[0]["source"] == "ticket"


def test_extract_cases_keeps_narrative_steps():
    raw = json.dumps(
        [
            {
                "id": "TC001",
                "req_id": "REQ-01",
                "module": "权限",
                "title": "无权限用户不能下载安装包",
                "type": "permission",
                "priority": "P0",
                "precondition": "普通用户已登录",
                "steps": [
                    {
                        "id": 1,
                        "step": "请求安装包下载",
                        "testData": "普通用户",
                        "expectedResult": "拒绝访问",
                    }
                ],
            }
        ],
        ensure_ascii=False,
    )

    cases = extract_cases(raw)

    assert cases[0]["precondition"] == "普通用户已登录"
    assert cases[0]["steps"][0]["expectedResult"] == "拒绝访问"
    assert cases[0]["requests"] == []


@pytest.mark.asyncio
async def test_generate_writes_executable_test_script(tmp_path):
    llm = FakeLLM(["分析内容", _CASES_JSON])
    generator = TicketCaseGenerator(
        llm=llm,
        output_root=tmp_path,
        spec={"paths": {"/api/tasks/{task_id}": {}}},
        endpoints=[{"method": "GET", "path": "/api/tasks/{task_id}", "summary": "查询任务"}],
    )

    result = await generator.generate(_ticket())

    output_dir = tmp_path / "837"
    assert (output_dir / "analysis.md").is_file()
    assert (output_dir / "cases.json").is_file()
    assert (output_dir / "cases.md").is_file()
    assert (output_dir / "test_gen.py").is_file()

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["review_status"] == "pending_review"
    assert manifest["case_count"] == 1
    assert manifest["request_count"] == 1
    assert manifest["spec_issues"] == []
    assert result["ticket_id"] == 837

    py_compile.compile(str(output_dir / "test_gen.py"), doraise=True)
    script = (output_dir / "test_gen.py").read_text(encoding="utf-8")
    assert "def test_tc001" in script
    assert "/api/tasks/1" in script


@pytest.mark.asyncio
async def test_generate_ticket_cases(tmp_path):
    generator = TicketCaseGenerator(FakeLLM([ANALYSIS, _CASES_JSON]), output_root=tmp_path)
    result = await generator.generate(_ticket())

    assert result["ticket_id"] == 837
    assert result["case_count"] == 1
    assert result["review_status"] == "pending_review"
    output_dir = tmp_path / "837"
    assert (output_dir / "analysis.md").exists()
    assert json.loads((output_dir / "cases.json").read_text(encoding="utf-8"))[0]["id"] == "TC001"
    assert "pending_review" in (output_dir / "manifest.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_generate_records_spec_issues_for_unknown_paths(tmp_path):
    llm = FakeLLM(["分析内容", _CASES_JSON])
    generator = TicketCaseGenerator(
        llm=llm,
        output_root=tmp_path,
        spec={"paths": {"/api/licence": {}}},
    )

    await generator.generate(_ticket())

    manifest = json.loads((tmp_path / "837" / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["spec_issues"]) == 1
    assert "/api/tasks/1" in manifest["spec_issues"][0]


@pytest.mark.asyncio
async def test_generate_passes_endpoint_list_to_case_prompt(tmp_path):
    llm = FakeLLM(["分析内容", _CASES_JSON])
    generator = TicketCaseGenerator(
        llm=llm,
        output_root=tmp_path,
        endpoints=[{"method": "GET", "path": "/api/tasks/{task_id}", "summary": "查询任务"}],
    )

    await generator.generate(_ticket())

    case_prompt = llm.calls[1][1]
    assert "GET /api/tasks/{task_id}" in case_prompt
    assert "查询任务" in case_prompt


@pytest.mark.asyncio
async def test_generate_without_endpoints_still_works(tmp_path):
    llm = FakeLLM(["分析内容", _CASES_JSON])
    generator = TicketCaseGenerator(llm=llm, output_root=tmp_path)

    result = await generator.generate(_ticket())

    assert result["case_count"] == 1
    assert (tmp_path / "837" / "test_gen.py").is_file()
