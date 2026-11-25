import logging
from math import isfinite
from typing import Literal

import httpx

from app.core.config import settings


REVIEW_SIMILAR_MIN_SCORE = 0.55
logger = logging.getLogger(__name__)


async def fetch_similar_ticket_hits(task_id: int, limit: int = 10) -> tuple[list, Literal["ok", "unavailable"]]:
    try:
        url = f"{settings.AI_SERVICE_URL.rstrip('/')}/api/ai/task/tickets/similar"
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(url, params={"task_id": task_id, "limit": limit})
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict) and int(payload.get("code") or 0) == 0:
                items = (payload.get("data") or {}).get("similar") or []
                if isinstance(items, list):
                    return items, "ok"
    except Exception:
        logger.warning("相似工单向量检索不可用: task_id=%s", task_id)
    return [], "unavailable"


def filter_similar_ticket_hits(items: list, exclude_id: int, min_score: float | None = None) -> list[dict]:
    hits = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            task_id = int(item.get("task_id"))
            score = round(float(item.get("score") or 0), 4)
        except (TypeError, ValueError, OverflowError):
            continue
        if task_id <= 0 or task_id == exclude_id or task_id in seen or not isfinite(score):
            continue
        seen.add(task_id)
        if min_score is not None and score < min_score:
            continue
        hits.append({**item, "task_id": task_id, "score": score})
    return hits
