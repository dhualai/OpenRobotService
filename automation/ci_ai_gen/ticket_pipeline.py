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
from typing import Any, Optional, Protocol

from automation.ci_ai_gen.gates import strip_code_fence
from automation.src.ticket_pipeline.models import TicketCandidate
from automation.src.ticket_pipeline.script_renderer import (
    render_script,
    validate_case_requests,
)


class TextLLM(Protocol):
    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        ...


def extract_cases(text: str) -> list[dict[str, Any]]:
    """Extract a JSON case array from an LLM response.

    Tolerates fenced blocks, trailing commas, and mid-output truncation
    (the incomplete trailing case is dropped with a best-effort salvage).
    """
    blob = strip_code_fence(text).strip()
    if not blob.startswith("["):
        match = re.search(r"\[[\s\S]*", blob)
        if match:
            blob = match.group()
    data: Any = None
    for candidate in (blob, re.sub(r",\s*([}\]])", r"\1", blob)):
        try:
            data = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        data = _salvage_truncated_array(blob)
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
        normalized.setdefault("requests", [])
        normalized["source"] = "ticket"
        cases.append(normalized)
    return cases


def _salvage_truncated_array(blob: str) -> Optional[list[dict[str, Any]]]:
    """Keep the leading complete objects of a truncated JSON array.

    Walks `},` boundaries from the tail backwards and returns the first
    prefix that parses as a non-empty array. Returns None when nothing
    can be salvaged.
    """
    idx = blob.rfind("},")
    while idx != -1:
        candidate = blob[: idx + 1] + "]"
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            idx = blob.rfind("},", 0, idx)
            continue
        if isinstance(data, list) and data:
            return data
        idx = blob.rfind("},", 0, idx)
    return None


def _extract_req_ids(analysis: str) -> list[str]:
    """Extract ordered unique REQ ids from an analysis document."""
    ids: list[str] = []
    seen: set[str] = set()
    for match in re.findall(r"REQ-\d+", analysis or ""):
        if match not in seen:
            seen.add(match)
            ids.append(match)
    return ids


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
        requests = [item for item in (case.get("requests") or []) if isinstance(item, dict)]
        if requests:
            lines.append("接口调用：")
            lines.append("")
            for request in requests:
                lines.append(
                    f"- `{str(request.get('method', 'GET')).upper()} {request.get('path', '')}`"
                    f" → 期望 {request.get('expected_status', 200)}"
                    f"（{request.get('step') or '未命名步骤'}）"
                )
            lines.append("")
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
        spec: Optional[dict[str, Any]] = None,
        endpoints: Optional[list[dict[str, Any]]] = None,
        case_batch_size: int = 5,
    ) -> None:
        automation_root = Path(__file__).resolve().parents[1]
        self.llm = llm
        self.prompts_dir = prompts_dir or Path(__file__).resolve().parent / "prompts"
        self.output_root = output_root or automation_root / "references" / "generated-cases" / "tickets"
        self.spec = spec
        self.endpoints = endpoints or []
        self.case_batch_size = case_batch_size

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

    def _endpoint_brief(self) -> str:
        if not self.endpoints:
            return "（未提供接口清单）"
        lines = []
        for endpoint in self.endpoints:
            if not isinstance(endpoint, dict):
                continue
            method = str(endpoint.get("method", "")).upper()
            path = str(endpoint.get("path", ""))
            if not method or not path:
                continue
            summary = str(endpoint.get("summary") or "").strip()
            params = [
                str(item.get("name"))
                for item in (endpoint.get("params") or [])
                if isinstance(item, dict) and item.get("name")
            ]
            detail = f"- {method} {path}"
            if summary:
                detail += f" — {summary}"
            if params:
                detail += f"（参数：{', '.join(params)}）"
            lines.append(detail)
        return "\n".join(lines) if lines else "（未提供接口清单）"

    async def generate(self, ticket: TicketCandidate) -> dict[str, Any]:
        ticket_payload = self._ticket_payload(ticket)
        analysis = await self.llm.complete(
            self._prompt("ticket_analysis.md"),
            f"## 工单\n{ticket_payload}",
            max_tokens=4096,
        )

        # 按功能点分批生成：一次输出全部用例容易超出单次 token 上限被截断
        req_ids = _extract_req_ids(analysis)
        if req_ids:
            batches = [
                req_ids[i : i + self.case_batch_size]
                for i in range(0, len(req_ids), self.case_batch_size)
            ]
        else:
            batches = [[]]
        merged: list[dict[str, Any]] = []
        for batch in batches:
            scope = "、".join(batch) if batch else "（全部功能点）"
            cases_raw = await self.llm.complete(
                self._prompt("ticket_case_gen.md"),
                f"## 工单\n{ticket_payload}\n\n## 测试分析\n{analysis}\n\n"
                f"## 接口清单\n{self._endpoint_brief()}\n\n"
                f"## 本批功能点\n仅生成以下 REQ 的用例，不得生成其他 REQ 的用例：{scope}",
                max_tokens=8192,
            )
            merged.extend(extract_cases(cases_raw))

        # 全局重编号：各批可能重复使用 TC001 等 id，重名后确定性去重
        seen_ids: set[str] = set()
        next_seq = 1
        for case in merged:
            cid = case.get("id")
            if cid in seen_ids:
                while f"TC{next_seq:03d}" in seen_ids:
                    next_seq += 1
                case["id"] = f"TC{next_seq:03d}"
                next_seq += 1
            seen_ids.add(case.get("id"))
        cases = merged

        spec_issues = validate_case_requests(cases, self.spec)
        script = render_script(ticket, cases)

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
        (output_dir / "test_gen.py").write_text(script, encoding="utf-8")
        manifest = {
            "ticket_id": ticket.id,
            "ticket_type": ticket.task_type,
            "project_id": ticket.project_id,
            "project_name": ticket.project_name,
            "source_title": ticket.title,
            "case_count": len(cases),
            "request_count": sum(len(case.get("requests") or []) for case in cases),
            "review_status": "pending_review",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "ticket_case_generator",
            "spec_issues": spec_issues,
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
