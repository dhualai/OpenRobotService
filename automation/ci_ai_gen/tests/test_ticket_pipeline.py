"""Tests for ticket-driven candidate case generation."""

import json

import pytest

from automation.ci_ai_gen.ticket_pipeline import TicketCaseGenerator, extract_cases
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

CASES = json.dumps(
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


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def complete(self, system_prompt, user_prompt, max_tokens=1024):
        self.calls.append((system_prompt, user_prompt, max_tokens))
        return ANALYSIS if len(self.calls) == 1 else CASES


def _ticket() -> TicketCandidate:
    return TicketCandidate(
        id=837,
        title="限制用户授权及安装包下载权限",
        description="限制用户的 licence 和 USP 安装包下载权限。",
        task_type="feature",
        status="new",
        project_id="Leo_test",
        project_name="摇人吧服务号",
        tags=["ai_generated", "auto_case"],
    )


@pytest.mark.asyncio
async def test_generate_ticket_cases(tmp_path):
    generator = TicketCaseGenerator(FakeLLM(), output_root=tmp_path)
    result = await generator.generate(_ticket())

    assert result["ticket_id"] == 837
    assert result["case_count"] == 1
    assert result["review_status"] == "pending_review"
    output_dir = tmp_path / "837"
    assert (output_dir / "analysis.md").exists()
    assert json.loads((output_dir / "cases.json").read_text(encoding="utf-8"))[0]["id"] == "TC001"
    assert "pending_review" in (output_dir / "manifest.json").read_text(encoding="utf-8")


def test_extract_cases_from_fenced_json():
    raw = f"```json\n{CASES}\n```"
    cases = extract_cases(raw)
    assert cases[0]["title"] == "无权限用户不能下载安装包"
    assert cases[0]["source"] == "ticket"
