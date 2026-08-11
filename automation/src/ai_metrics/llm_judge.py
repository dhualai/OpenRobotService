"""L3 LLM-as-judge client and rubric scoring.

The judge client talks to the DeepSeek OpenAI-compatible API directly
(openai SDK, already an automation dependency), independent of ai.core.
Configuration mirrors ai/.env: DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL /
DEEPSEEK_MODEL. Missing config or SDK raises JudgeUnavailableError so
callers can skip gracefully.
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
    ):
        try:
            from openai import AsyncOpenAI
        except ImportError as e:  # pragma: no cover - env dependent
            raise JudgeUnavailableError(f"openai SDK not installed: {e}")
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.temperature = temperature

    @classmethod
    def from_env(cls, project_root: Optional[str] = None) -> "LLMJudgeClient":
        _load_env(project_root)
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
        if not api_key:
            raise JudgeUnavailableError("DEEPSEEK_API_KEY not configured")
        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
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
