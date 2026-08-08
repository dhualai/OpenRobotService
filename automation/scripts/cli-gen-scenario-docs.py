"""CLI: 从 api-test-cases.xlsx 生成场景设计文档（scenarios-*.md）。

用法:
    python automation/scripts/cli-gen-scenario-docs.py

覆盖 automation/docs/testing/scenarios/scenarios-call.md 与 scenarios-admin.md。
后续 Excel 用例变化时重跑即可保持文档与用例一致。
"""

import sys
from pathlib import Path

import openpyxl

XLSX = Path(__file__).resolve().parents[1] / "testdata" / "cases" / "api-test-cases.xlsx"
DOCS = Path(__file__).resolve().parents[1] / "docs" / "testing" / "scenarios"

MODULES = {
    "call": "我要摇人模块",
    "admin": "后台管理模块",
}

KIND_ORDER = [
    "正常流程", "异常流程", "权限", "状态流转", "数据校验", "全链路", "Redis", "AI", "数据库",
]


def kind_of(note: str) -> str:
    n = note.strip()
    k = n.split("：")[0].split(":")[0].split(",")[0].split("，")[0]
    return k if k in KIND_ORDER else "其他"


def gen(module: str, title: str) -> str:
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb[module]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h) if h else "" for h in rows[0]]

    cases = []
    for r in rows[1:]:
        d = dict(zip(header, r))
        note = str(d.get("note") or "").strip()
        steps = d.get("steps")
        if steps:
            api = "steps 全链路（多步串联）"
        else:
            api = f"{d.get('method')} {d.get('path')} -> {d.get('expected_status')}"
        cases.append({
            "id": str(d.get("id") or ""),
            "api": api,
            "kind": kind_of(note),
            "note": note,
            "priority": str(d.get("type") or "-"),
        })

    groups: dict = {}
    for c in cases:
        groups.setdefault(c["kind"], []).append(c)

    lines = [
        f"# {title} - 测试场景设计（实际用例清单）",
        "",
        "> 本清单由 `automation/scripts/cli-gen-scenario-docs.py` 从 "
        "`automation/testdata/cases/api-test-cases.xlsx` 自动生成，Excel 用例变化后请重跑脚本。",
        "",
        "## 覆盖统计",
        "",
        "| 覆盖类型 | 用例数 | 用例 ID |",
        "|----------|--------|---------|",
    ]
    for kind in KIND_ORDER:
        g = groups.get(kind, [])
        if g:
            ids = ", ".join(c["id"] for c in sorted(g, key=lambda c: c["id"]))
            lines.append(f"| {kind} | {len(g)} | {ids} |")
    lines.append("")

    for kind in KIND_ORDER:
        g = groups.get(kind, [])
        if not g:
            continue
        lines.append(f"## {kind}")
        lines.append("")
        lines.append("| 用例ID | 接口 | 说明 |")
        lines.append("|--------|------|------|")
        for c in sorted(g, key=lambda c: c["id"]):
            lines.append(f"| {c['id']} | `{c['api']}` | {c['note']} |")
        lines.append("")

    lines.append("## 汇总表")
    lines.append("")
    lines.append("| 用例ID | 接口 | 覆盖类型 | 说明 |")
    lines.append("|--------|------|---------|------|")
    for c in sorted(cases, key=lambda c: c["id"]):
        lines.append(f"| {c['id']} | `{c['api']}` | {c['kind']} | {c['note']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    for module, title in MODULES.items():
        doc = DOCS / f"scenarios-{module}.md"
        doc.write_text(gen(module, title), encoding="utf-8")
        print(f"generated: {doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
