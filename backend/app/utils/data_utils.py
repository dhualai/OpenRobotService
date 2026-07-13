import json
from typing import Any, Optional, List


def safe_json_loads(json_str: Optional[str]) -> Optional[Any]:
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None


def safe_json_dumps(data: Optional[Any]) -> Optional[str]:
    if data is None:
        return None
    try:
        return json.dumps(data)
    except (TypeError, ValueError):
        return None


def generate_content_preview(content: str, max_length: int = 100) -> str:
    if not content:
        return ""
    if len(content) <= max_length:
        return content
    return content[:max_length] + "..."


def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def sanitize_input(text: Optional[str], max_length: Optional[int] = None) -> str:
    if text is None:
        return ""
    text = str(text).strip()
    if max_length and len(text) > max_length:
        text = text[:max_length]
    return text