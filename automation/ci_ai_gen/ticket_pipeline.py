"""Ticket-driven candidate case generation.

This module is intentionally separate from the PRD/OpenAPI pipeline. It turns
one reviewed production ticket into candidate functional cases and a review
manifest. Generated cases are never written directly into the formal suite.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from automation.ci_ai_gen.gates import strip_code_fence
from automation.src.ticket_pipeline.models import TicketCandidate


class TextLLM(Protocol):
    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        ...


def extract_cases(text: str) -> list[dict[str, Any]]:
    """Extract a JSON case array from an LLM response."""
    blob = strip_code_fence(text).strip()
    match = re.search(r"\[[\s\S]*\]", blob)
    if match:
        blob = match.group()
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = json.loads(re.sub(r",\s*([}\]])", r"\1", blob))
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        data = data["cases"]
    if not isinstance(data, list) or not data:
        raise ValueError("ticket case output must be a non-empty JSON array")

    cases: list[dict[str, Any]] = []
    for index, case in enumerate(data, 1):
        if not isinstance(case, dict):
            raise ValueError(f"case {index} is not an object")
        title = str(case.get("title", "")).strip()
        if not title:
            raise ValueError(f"case {index} missing title")
        normalized = dict(case)
        normalized.setdefault("id", f"TC{index:03d}")
        normalized.setdefault("type", "positive")
        normalized.setdefault("priority", "P1")
        normalized.setdefault("precondition", "")
        normalized.setdefault("steps", [])
        normalized["source"] = "ticket"
        cases.append(normalized)
    return cases


def _render_cases_md(ticket: TicketCandidate, cases: list[dict[str, Any]]) -> str:
    lines = [
        f"# 工单 {ticket.id} 候选功能测试用例",
        "",
        f"- 标题：{ticket.title}",
        f"- 类型：{ticket.task_type}",
        f"- 项目：{ticket.project_id} / {ticket.project_name}",
        "- 状态：pending_review",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"## {case.get('id')} {case.get('title')}",
                "",
                f"- 类型：{case.get('type')}",
                f"- 优先级：{case.get('priority')}",
                f"- 前置条件：{case.get('precondition') or '-'}",
                "",
            ]
        )
        for step in case.get("steps") or []:
            if not isinstance(step, dict):
                continue
            lines.append(
                f"{step.get('id', '?')}. {step.get('step', '')} "
                f"| 数据：{step.get('testData', '-')} "
                f"| 预期：{step.get('expectedResult', '-')}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class TicketCaseGenerator:
    """Generate candidate functional cases for one ticket."""

    def __init__(
        self,
        llm: TextLLM,
        *,
        prompts_dir: Path | None = None,
        output_root: Path | None = None,
    ) -> None:
        automation_root = Path(__file__).resolve().parents[1]
        self.llm = llm
        self.prompts_dir = prompts_dir or Path(__file__).resolve().parent / "prompts"
        self.output_root = output_root or automation_root / "references" / "generated-cases" / "tickets"

    def _prompt(self, name: str) -> str:
        return (self.prompts_dir / name).read_text(encoding="utf-8")

    @staticmethod
    def _ticket_payload(ticket: TicketCandidate) -> str:
        return json.dumps(
            {
                "id": ticket.id,
                "title": ticket.title,
                "description": ticket.description,
                "task_type": ticket.task_type,
                "status": ticket.status,
                "project_id": ticket.project_id,
                "project_name": ticket.project_name,
                "tags": ticket.tags,
            },
            ensure_ascii=False,
            indent=2,
        )

    async def generate(self, ticket: TicketCandidate) -> dict[str, Any]:
        ticket_payload = self._ticket_payload(ticket)
        analysis = await self.llm.complete(
            self._prompt("ticket_analysis.md"),
            f"## 工单\n{ticket_payload}",
            max_tokens=4096,
        )
        cases_raw = await self.llm.complete(
            self._prompt("ticket_case_gen.md"),
            f"## 工单\n{ticket_payload}\n\n## 测试分析\n{analysis}",
            max_tokens=8192,
        )
        cases = extract_cases(cases_raw)

        output_dir = self.output_root / str(ticket.id)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "analysis.md").write_text(analysis, encoding="utf-8")
        (output_dir / "cases.json").write_text(
            json.dumps(cases, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "cases.md").write_text(
            _render_cases_md(ticket, cases),
            encoding="utf-8",
        )
        manifest = {
            "ticket_id": ticket.id,
            "ticket_type": ticket.task_type,
            "project_id": ticket.project_id,
            "project_name": ticket.project_name,
            "source_title": ticket.title,
            "case_count": len(cases),
            "review_status": "pending_review",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "ticket_case_generator",
        }
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            **manifest,
            "output_dir": str(output_dir),
            "cases": cases,
        }
