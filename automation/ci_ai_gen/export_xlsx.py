"""Export generated AI test cases to the platform Excel case format.

Maps the generated cases.json into the same columns used by
testdata/cases/api-test-cases.xlsx so approved cases can be merged
directly into the formal case library (load_cases / parametrize).
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

EXCEL_HEADERS = [
    "id", "module", "method", "path", "auth", "role",
    "payload", "expected_status", "expected_fields", "type", "note", "steps",
]

_STATUS_RE = re.compile(r"(?<!\d)([4-5]\d{2})(?!\d)")


def _extract_status(expected_result: str, default: int = 200) -> int:
    m = _STATUS_RE.search(expected_result or "")
    return int(m.group(1)) if m else default


def _extract_payload(case: Dict[str, Any]) -> str:
    """Best-effort JSON payload from the first step's testData."""
    steps = case.get("steps") or []
    if not steps:
        return "{}"
    data = steps[0].get("testData", "")
    try:
        parsed = json.loads(data) if isinstance(data, str) and data.strip() else data
        return json.dumps(parsed, ensure_ascii=False) if parsed not in ("", None) else "{}"
    except json.JSONDecodeError:
        return json.dumps({"testData": data}, ensure_ascii=False)


def _extract_payload_from_text(test_data) -> str:
    """Best-effort JSON payload from a raw testData string."""
    if isinstance(test_data, dict):
        return json.dumps(test_data, ensure_ascii=False)
    data = test_data or ""
    try:
        parsed = json.loads(data) if data.strip() else None
        if parsed:
            return json.dumps(parsed, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    return json.dumps({"testData": data}, ensure_ascii=False)


def case_steps_for_execution(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map a generated case's steps into executable steps for the runner.

    Each step becomes {method, path, payload, expected_status, expected_fields};
    placeholders ({{stepN.body.x}}) are preserved for the executor to resolve.
    Returns [] when the case has no request-level steps.
    """
    steps = case.get("steps") or []
    out: List[Dict[str, Any]] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        method = str(s.get("method") or "").upper()
        path = s.get("path") or ""
        if not method or not path:
            continue
        expected_result = s.get("expectedResult") or s.get("expected") or ""
        out.append({
            "method": method,
            "path": path,
            "payload": _extract_payload_from_text(s.get("testData")),
            "expected_status": _extract_status(expected_result),
            "expected_fields": {},
        })
    return out


def case_to_row(case: Dict[str, Any]) -> List[Any]:
    """Convert one generated case to an Excel row (aligned with EXCEL_HEADERS)."""
    note_parts = [case.get("title", "")]
    if case.get("req_id"):
        note_parts.insert(0, f"需求:{case['req_id']}")
    if case.get("precondition"):
        note_parts.append(f"前置:{case['precondition']}")
    expected = case.get("steps") or [{}]
    expected_result = expected[-1].get("expectedResult", "") if isinstance(expected[-1], dict) else ""
    exec_steps = case_steps_for_execution(case)
    return [
        case.get("id", "TC000"),
        case.get("module", "ai"),
        case.get("method", "GET"),
        case.get("path", ""),
        "Y",
        "admin",
        _extract_payload(case),
        _extract_status(expected_result),
        "{}",
        case.get("type", "positive"),
        " | ".join(note_parts),
        json.dumps(exec_steps, ensure_ascii=False) if exec_steps else "",
    ]


def export_cases_to_xlsx(cases: List[Dict[str, Any]], xlsx_path: Path,
                         sheet_name: str = "ai_generated") -> int:
    """Write cases to an Excel file in platform case format.

    Returns the number of rows written (excluding header).
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(EXCEL_HEADERS)
    for case in cases:
        ws.append(case_to_row(case))
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(xlsx_path))
    return len(cases)


def cases_from_file(cases_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("cases", [])
    return data if isinstance(data, list) else []


_TYPE_LABELS = {
    "positive": "正向",
    "negative": "异常",
    "edge": "边界",
    "auth": "权限",
    "flow": "状态流转",
}

_MODULE_BUCKETS = [
    ("call", ("我要摇人", "AI对话", "会话管理", "AI消息", "消息附件", "图片预览", "认证", "个人信息")),
    ("tasks", ("系统任务", "工单管理", "工单确认", "智能派单", "工单状态")),
    ("admin", ("后台管理", "用户管理", "角色", "权限管理", "日报", "仪表盘", "风险", "资源管理",
               "项目", "数据导入", "搬运效率", "责任模块", "操作记录", "报表", "微信")),
]


def module_bucket(module: str) -> str:
    """Classify a case module into call / tasks / admin / other."""
    if not module:
        return "other"
    for bucket, keywords in _MODULE_BUCKETS:
        if any(k in module for k in keywords):
            return bucket
    return "other"


_BUCKET_LABELS = {"call": "我要摇人", "tasks": "系统任务", "admin": "后台管理", "other": "其他"}


def group_cases_by_module(cases: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {"call": [], "tasks": [], "admin": [], "other": []}
    for case in cases:
        groups[module_bucket(case.get("module", ""))].append(case)
    return groups


def export_cases_to_markdown_split(cases: List[Dict[str, Any]], md_dir: Path,
                                   title: str = "AI 生成测试用例") -> Dict[str, int]:
    """Export cases.md (full) plus one Markdown file per module bucket.

    Returns {filename: case_count}.
    """
    md_dir.mkdir(parents=True, exist_ok=True)
    counts: Dict[str, int] = {}
    full_path = md_dir / "cases.md"
    counts[full_path.name] = export_cases_to_markdown(cases, full_path, title=title)

    groups = group_cases_by_module(cases)
    for bucket, bucket_cases in groups.items():
        if not bucket_cases:
            continue
        label = _BUCKET_LABELS.get(bucket, bucket)
        path = md_dir / f"cases-{bucket}.md"
        counts[path.name] = export_cases_to_markdown(
            bucket_cases, path, title=f"{title} · {label}（{len(bucket_cases)} 条）",
        )
    return counts


def export_cases_to_markdown(cases: List[Dict[str, Any]], md_path: Path,
                             title: str = "AI 生成测试用例") -> int:
    """Export cases as a human-readable Markdown document.

    Format per case: TC id + title, type, requirement, precondition,
    request (method + path), numbered steps with test data and expected
    result - readable by non-technical reviewers.
    """
    lines = [f"# {title}", "", f"共 {len(cases)} 条用例", ""]
    for case in cases:
        cid = case.get("id", "TC000")
        lines.append(f"## {cid} {case.get('title', '')}")
        lines.append("")
        lines.append(f"- **类型**：{_TYPE_LABELS.get(case.get('type'), case.get('type', ''))}")
        if case.get("req_id"):
            lines.append(f"- **需求**：{case['req_id']}")
        if case.get("module"):
            lines.append(f"- **模块**：{case['module']}")
        if case.get("precondition"):
            lines.append(f"- **前置条件**：{case['precondition']}")
        lines.append(f"- **请求**：{case.get('method', 'GET')} `{case.get('path', '')}`")
        lines.append("")
        steps = case.get("steps") or []
        if steps:
            lines.append("**步骤：**")
            lines.append("")
            for s in steps:
                sid = s.get("id", "")
                step = s.get("step", "")
                data = s.get("testData", "")
                expected = s.get("expectedResult", "")
                lines.append(f"{sid}. **操作**：{step}")
                if data:
                    lines.append(f"   - 测试数据：`{data}`")
                if expected:
                    lines.append(f"   - 预期结果：{expected}")
                lines.append("")
        else:
            lines.append("_（无步骤描述）_")
            lines.append("")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return len(cases)
