"""Structural gates for AI-generated artifacts (pure functions, testable).

Each check returns a list of violation messages; empty list == pass.
"""

import json
import re
from typing import Any, Dict, List, Optional

_ANALYSIS_HEADINGS = ["需求概述", "测试范围", "测试重点", "测试策略"]
_PRD_ANALYSIS_HEADINGS = ["需求概述", "功能点清单", "状态流转", "权限矩阵", "测试策略"]
_REQ_LINE = re.compile(
    r'(?:'
    r'requests\.(get|post|put|patch|delete)\s*\(\s*(?:f|rf)?(["\'])(.*?)\2'
    r'|'
    r'_api\(\s*[\w.]+\s*,\s*(["\'])(get|post|put|patch|delete)\4\s*,\s*(["\'])(.*?)\6'
    r')',
    re.DOTALL,
)
_PATH_PLACEHOLDER = re.compile(r"\{[^{}]+\}")
_STEP_PLACEHOLDER = re.compile(r"\{\{\s*step(\d+)\s*\.")


def check_analysis(text: str, prd_mode: bool = False) -> List[str]:
    """Verify the analysis doc has the required top-level headings.

    Interface mode requires 需求概述/测试范围/测试重点/测试策略;
    PRD mode requires 需求概述/功能点清单/状态流转/权限矩阵/测试策略.
    Heading levels are matched loosely (# through ######).
    """
    if not text or not text.strip():
        return ["analysis is empty"]
    required = _PRD_ANALYSIS_HEADINGS if prd_mode else _ANALYSIS_HEADINGS
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE)
    return [f"missing required heading: {h}" for h in required
            if not any(x.strip().startswith(h) for x in headings)]


def strip_code_fence(text: str) -> str:
    """Strip a markdown fenced code block, keeping only its inner content."""
    m = re.search(r"```[a-zA-Z]*\s*\n([\s\S]*?)```", text)
    return m.group(1) if m else text


def _strip_json_comments(blob: str) -> str:
    """Best-effort JSONC -> JSON: drop // line comments and trailing commas."""
    lines = []
    for line in blob.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        lines.append(re.sub(r",\s*([}\]])", r"\1", line))
    return "\n".join(lines)


def check_cases(text: str) -> tuple:
    """Validate the cases JSON. Returns (issues, parsed_cases_or_None).

    Tolerates fenced code blocks and JSONC comments from the LLM.
    """
    if not text or not text.strip():
        return ["cases output is empty"], None
    fenced = re.search(r"\[[\s\S]*\]", text)
    blob = fenced.group() if fenced else text.strip()
    try:
        cases = json.loads(blob)
    except json.JSONDecodeError:
        try:
            cases = json.loads(_strip_json_comments(blob))
        except json.JSONDecodeError as e:
            return [f"invalid JSON: {e}"], None
    if not isinstance(cases, list):
        return ["cases must be a JSON array"], None
    if not cases:
        return ["cases array is empty"], cases
    issues: List[str] = []
    seen_ids = set()
    for case in cases:
        issues.extend(_normalize_case(case))
        issues.extend(_check_case_steps(case))
        cid = case.get("id")
        if cid in seen_ids:
            issues.append(f"duplicate case id: {cid}")
        seen_ids.add(cid)
    return issues, cases


def _check_case_steps(case: Dict[str, Any]) -> List[str]:
    """Validate request-level steps for flow cases (executable full flows).

    A flow case must have >= 2 steps, each with method + path, and any
    {{stepN.body.x}} placeholder must reference an earlier step.
    """
    if not isinstance(case, dict):
        return []
    cid = case.get("id", "?")
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps:
        return []
    issues: List[str] = []
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            issues.append(f"step {i} in {cid} is not an object")
            continue
        if case.get("type") == "flow" and (not s.get("method") or not s.get("path")):
            issues.append(f"flow case {cid} step {i} missing method/path (request-level step required)")
        blob = f"{s.get('path', '')} {s.get('testData', '')}"
        for m in _STEP_PLACEHOLDER.finditer(blob):
            ref = int(m.group(1))
            if ref >= i:
                issues.append(f"case {cid} step {i} references step{ref} which is not executed yet")
    if case.get("type") == "flow" and len(steps) < 2:
        issues.append(f"flow case {cid} needs at least 2 steps to be a full flow")
    return issues


def _normalize_case(case: Dict[str, Any]) -> List[str]:
    issues = []
    if not isinstance(case, dict):
        return ["case is not an object"]
    if not isinstance(case.get("id"), str) or not re.fullmatch(r"TC\d{3,}", case["id"]):
        issues.append(f"invalid case id: {case.get('id')!r}")
    for field in ("title", "method", "path", "precondition"):
        if not case.get(field):
            issues.append(f"missing field {field} in {case.get('id', '?')}")
    if case.get("type") not in ("positive", "negative", "edge", "auth", "flow"):
        issues.append(f"invalid type {case.get('type')!r} in {case.get('id', '?')}")
    if not isinstance(case.get("steps"), list) or not case["steps"]:
        issues.append(f"missing steps in {case.get('id', '?')}")
    return issues


def _normalize_path(path: str) -> str:
    path = path.split("?", 1)[0].rstrip("/")
    return _PATH_PLACEHOLDER.sub("{}", path)


def spec_path_set(spec: dict) -> set:
    """Normalized set of spec paths (placeholders collapsed)."""
    return {_normalize_path(p) for p in spec.get("paths", {})}


def extract_script_paths(text: str) -> List[str]:
    """Extract URL path literals used in requests / _api calls."""
    paths = []
    for match in _REQ_LINE.finditer(text):
        literal = match.group(3) or match.group(7)
        literal = literal.replace("{BASE_URL}", "")
        literal = literal.strip()
        if not literal.startswith("/"):
            continue
        paths.append(literal)
    return paths


def _segments(path: str) -> List[str]:
    return [s for s in path.split("/") if s]


def _path_matches(actual: str, spec_template: str) -> bool:
    """Compare an actual URL path against a spec template.

    Template placeholder segments ("{}" after normalization) match any
    single segment, so /api/v1/tasks/1 matches /api/v1/tasks/{task_id}.
    """
    a = _segments(_normalize_path(actual))
    s = _segments(_normalize_path(spec_template))
    if len(a) != len(s):
        return False
    for actual_seg, template_seg in zip(a, s):
        if template_seg == "{}":
            continue
        if actual_seg != template_seg:
            return False
    return True


def check_script_paths(script_text: str, spec: dict) -> List[str]:
    """Every URL path used in the script must match a spec path template."""
    templates = list(spec.get("paths", {}).keys())
    if not templates:
        return []
    issues = []
    for path in extract_script_paths(script_text):
        if not any(_path_matches(path, t) for t in templates):
            issues.append(f"script uses path not in spec: {path!r}")
    return issues


def check_script(script_text: str, spec: dict) -> List[str]:
    """Structural script checks (framework-standard shape: httpx + _api + allure + assertions)."""
    if not script_text or not script_text.strip():
        return ["script output is empty"]
    issues = check_script_paths(script_text, spec)
    if "def test_" not in script_text:
        issues.append("script has no test functions (missing 'def test_')")
    if "httpx" not in script_text:
        issues.append("script does not use httpx (framework standard client)")
    if "async def _api" not in script_text:
        issues.append("script is missing the _api() step helper")
    if "allure.step" not in script_text:
        issues.append("script has no allure step integration")
    if "assert_status_code" not in script_text:
        issues.append("script does not use framework assertions (assert_status_code)")
    if "@allure.feature" not in script_text:
        issues.append("script is missing @allure.feature decorator")
    if "requests" in script_text:
        issues.append("script uses requests; framework standard is httpx")
    return issues


REQ_PATTERN = re.compile(r"REQ-\d+")


def extract_req_ids(text: str) -> List[str]:
    """Extract REQ-xx ids from an analysis document, deduplicated in order."""
    seen = set()
    ids = []
    for m in REQ_PATTERN.finditer(text or ""):
        if m.group() not in seen:
            seen.add(m.group())
            ids.append(m.group())
    return ids


def check_cases_req_coverage(cases: Optional[List[dict]], req_ids: List[str]) -> List[str]:
    """Every REQ feature point must have at least one test case with req_id."""
    if not req_ids:
        return []
    if not cases:
        return [f"no cases produced for {len(req_ids)} feature points"]
    covered = {c.get("req_id") for c in cases if isinstance(c, dict) and c.get("req_id")}
    missing = [r for r in req_ids if r not in covered]
    if missing:
        return [f"feature point(s) without test case: {', '.join(missing)}"]
    return []
