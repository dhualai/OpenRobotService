"""Deterministic model-response replay for one fixed business scenario."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class ReplayMismatchError(ValueError):
    """Raised when a model request does not belong to the fixed scenario."""


@dataclass(frozen=True)
class ReplayResult:
    """A deterministic model reply."""

    kind: str
    content: str
    run_id: str


class ReplayEngine:
    """Match OpenAI-compatible requests and return fixed model responses."""

    def __init__(self, replay_file: str | Path):
        self.replay_file = Path(replay_file)
        self._data = json.loads(self.replay_file.read_text(encoding="utf-8"))
        self._query_re = re.compile(self._data["query_pattern"])
        self._run_id_re = re.compile(self._data["run_id_pattern"])

    @property
    def case_id(self) -> str:
        return str(self._data["case_id"])

    def reply(self, messages: Iterable[dict[str, Any]]) -> ReplayResult:
        text = self._messages_text(messages)
        match = self._query_re.search(text)
        if not match:
            raise ReplayMismatchError("fixed query marker was not found")

        run_id_match = self._run_id_re.search(text)
        if not run_id_match:
            raise ReplayMismatchError("run_id marker was not found")
        run_id = run_id_match.group("run_id")

        kind = self._response_kind(text)
        content = self._render_response(kind, run_id)
        return ReplayResult(kind=kind, content=content, run_id=run_id)

    def _response_kind(self, text: str) -> str:
        if "你是意图分类器" in text:
            return "intent"
        if "你是检索查询改写器" in text:
            return "rewrite"
        if "你是工单信息架构师" in text:
            return "fields"
        if "请根据以下对话和诊断过程，生成结构化工单" in text:
            return "ticket"
        if "只输出标题" in text:
            return "title"
        if "ticket_intent" in text or "提交工单" in text or "提单门槛" in text:
            return "main"
        raise ReplayMismatchError("unsupported model prompt")

    def _render_response(self, kind: str, run_id: str) -> str:
        responses = self._data["responses"]
        if kind == "intent":
            return str(responses["intent"])
        if kind == "rewrite":
            return str(responses["rewrite"]).format(run_id=run_id)
        if kind == "title":
            return str(responses["title"]).format(run_id=run_id)
        if kind in {"fields", "ticket"}:
            return json.dumps(
                self._replace_run_id(responses[kind], run_id),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        if kind == "main":
            state = json.dumps(
                self._replace_run_id(responses["main_state"], run_id),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            message = str(responses["main_message"]).format(run_id=run_id)
            return f"{state}\n{message}"
        raise ReplayMismatchError(f"unsupported response kind: {kind}")

    @classmethod
    def _replace_run_id(cls, value: Any, run_id: str) -> Any:
        if isinstance(value, str):
            return value.format(run_id=run_id)
        if isinstance(value, list):
            return [cls._replace_run_id(item, run_id) for item in value]
        if isinstance(value, dict):
            return {
                key: cls._replace_run_id(item, run_id)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _messages_text(messages: Iterable[dict[str, Any]]) -> str:
        parts: list[str] = []
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, str):
                parts.append(content)
                continue
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
        return "\n".join(parts)
