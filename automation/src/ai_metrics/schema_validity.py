"""L1 deterministic schema validation for AI agent outputs.

Schema format: {"dot.path.field": expected_type_or_enum}

Expected type may be:
  - a string type name: "str" | "int" | "float" | "bool" | "list" | "dict" | "any"
  - a list of allowed values (enum check)

Dot paths resolve through nested dicts and list items, e.g.
"agent_state.hypotheses" -> data["agent_state"]["hypotheses"].
"""

from typing import Any, Dict, List

_KNOWN_TYPES = ("str", "int", "float", "bool", "list", "dict", "any")


def resolve_path(data: Any, path: str) -> Any:
    """Resolve a dot path inside nested dict/list structure.

    Returns None if any segment is missing (None is a valid value,
    so callers should use a sentinel when distinguishing missing).
    """
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx >= len(current):
                return None
            current = current[idx]
        else:
            return None
    return current


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "any":
        return value is not None
    if value is None:
        return False
    if expected == "str":
        return isinstance(value, str)
    if expected == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "bool":
        return isinstance(value, bool)
    if expected == "list":
        return isinstance(value, list)
    if expected == "dict":
        return isinstance(value, dict)
    return False


def check_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """Validate data against schema.

    Returns a list of violation messages (empty list == valid).

    Examples:
        check_schema({"message": "hi"}, {"message": "str"}) == []
        check_schema({"action": "ask"}, {"action": ["ask", "answer"]}) == []
        check_schema({"action": "submit"}, {"action": ["ask", "answer"]}) != []
    """
    violations: List[str] = []
    for path, expected in schema.items():
        value = resolve_path(data, path)
        if expected == "any":
            continue
        if value is None:
            violations.append(f"{path}: missing or null, expected {expected!r}")
            continue
        if isinstance(expected, list):
            if value not in expected:
                violations.append(f"{path}: value {value!r} not in allowed {expected}")
        elif isinstance(expected, str):
            if expected not in _KNOWN_TYPES:
                violations.append(f"{path}: unknown type spec {expected!r}")
            elif not _type_matches(value, expected):
                violations.append(f"{path}: expected {expected}, got {type(value).__name__}({value!r})")
        else:
            violations.append(f"{path}: invalid schema spec {expected!r}")
    return violations
