"""L3 LLM-as-judge client and rubric scoring.

The judge client talks to any OpenAI-compatible chat-completions API
(openai SDK, already an automation dependency), independent of ai.core.
Generic configuration: LLM_API_KEY / LLM_BASE_URL / LLM_MODEL.
DeepSeek-specific variables remain as a backward-compatible fallback.
Missing config or SDK raises JudgeUnavailableError so callers can skip
gracefully.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"


class JudgeUnavailableError(Exception):
    """Raised when the judge LLM cannot be configured/initialized."""


def _load_env(project_root: Optional[str] = None) -> None:
    """Load ai/.env (fallback automation/.env) without overriding set vars."""
    candidates = []
    if project_root:
        candidates.append(os.path.join(project_root, "ai", ".env"))
        candidates.append(os.path.join(project_root, "automation", ".env"))
    candidates.append(".env")
    try:
        from dotenv import load_dotenv

        for path in candidates:
            if path and os.path.exists(path):
                load_dotenv(path, override=False)
                break
    except ImportError:
        pass


class LLMJudgeClient:
    """Minimal OpenAI-compatible judge client (DeepSeek by default)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        temperature: float = 0.0,
        default_headers: Optional[Dict[str, str]] = None,
    ):
        try:
            from openai import AsyncOpenAI
        except ImportError as e:  # pragma: no cover - env dependent
            raise JudgeUnavailableError(f"openai SDK not installed: {e}")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            default_headers=default_headers,
        )
        self.model = model
        self.temperature = temperature

    @classmethod
    def from_env(cls, project_root: Optional[str] = None) -> "LLMJudgeClient":
        _load_env(project_root)
        api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise JudgeUnavailableError("LLM_API_KEY or DEEPSEEK_API_KEY not configured")
        headers: Dict[str, str] = {}
        raw_headers = os.getenv("LLM_EXTRA_HEADERS_JSON")
        if raw_headers:
            headers.update(json.loads(raw_headers))
        session_id = os.getenv("LLM_SESSION_ID")
        if session_id:
            headers["x-opencode-session"] = session_id
        user_agent = os.getenv("LLM_USER_AGENT")
        if user_agent:
            headers["User-Agent"] = user_agent
        return cls(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            model=os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
            default_headers=headers or None,
        )

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
    ) -> str:
        """Single-turn completion, returns raw text."""
        resp = await self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content or ""

    async def close(self) -> None:
        await self._client.close()


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    fenced = re.search(r"\{[\s\S]*\}", text)
    if not fenced:
        return None
    try:
        return json.loads(fenced.group())
    except json.JSONDecodeError:
        return None


async def judge_rubric(
    question: str,
    answer: str,
    rubric: str,
    judge: LLMJudgeClient,
) -> Dict[str, Any]:
    """Score an answer against a plain-English rubric (1-5).

    Returns {"score": float, "reason": str}. Score is 0.0 when the judge
    output cannot be parsed.
    """
    system_prompt = (
        "你是一个严格的 AI 质量评审员。根据给定的评分标准（rubric）对回答打分，"
        "只输出 JSON：{\"score\": 1到5的整数, \"reason\": \"一句话理由\"}。"
    )
    user_prompt = (
        f"## 评分标准（rubric）\n{rubric}\n\n"
        f"## 用户问题\n{question}\n\n"
        f"## AI 回答\n{answer}\n\n"
        "请打分："
    )
    raw = await judge.complete(system_prompt, user_prompt)
    parsed = _extract_json(raw)
    if not parsed:
        return {"score": 0.0, "reason": f"judge output unparsable: {raw[:120]}"}
    try:
        score = float(parsed.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    return {"score": max(0.0, min(5.0, score)), "reason": parsed.get("reason", "")}
