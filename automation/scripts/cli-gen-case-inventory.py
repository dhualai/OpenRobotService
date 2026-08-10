"""CLI: 从代码驱动用例（自由函数）生成用例清单文档。

用法:
    python automation/scripts/cli-gen-case-inventory.py [--module tasks]

约定（与 design-code-driven-migration.md 一致）：
- class 级别：@allure.feature("<模块>")
- 函数级别：@allure.story("<场景>") + @allure.title("<标题>")
- docstring 第一行：覆盖类型：说明（正常流程/异常流程/权限/状态流转/数据校验/全链路/Redis/AI/数据库）
- 函数体内第一处 mock_api_client.<method>("<path>") 作为接口
"""

import ast
import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"
DOCS_DIR = Path(__file__).resolve().parents[1] / "docs" / "testing"

_COVER_KINDS = ["正常流程", "异常流程", "权限", "状态流转", "数据校验", "全链路", "Redis", "AI", "数据库"]

_REQ_RE = re.compile(r'mock_api_client\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')

ALLURE = {"feature", "story", "title"}


def _get_decorator_value(decorator) -> str:
    try:
        if isinstance(decorator, ast.Call) and decorator.args:
            arg = decorator.args[0]
            if isinstance(arg, ast.Constant):
                return str(arg.value)
    except Exception:
        pass
    return ""


def _dec_attr(decorator):
    """Get the attribute name of a decorator like @allure.feature(...) -> 'feature'."""
    if isinstance(decorator, ast.Call):
        return getattr(decorator.func, "attr", None)
    return getattr(decorator, "attr", None)


def _collect(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    cases = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not node.name.startswith("Test"):
            continue
        feature = ""
        for dec in node.decorator_list:
            if _dec_attr(dec) == "feature":
                feature = _get_decorator_value(dec)
        for fn in node.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not fn.name.startswith("test_"):
                continue
            story = title = ""
            for dec in fn.decorator_list:
                attr = _dec_attr(dec)
                if attr in ALLURE:
                    val = _get_decorator_value(dec)
                    if attr == "story":
                        story = val
                    elif attr == "title":
                        title = val
            doc = ast.get_docstring(fn) or ""
            doc_first = doc.splitlines()[0].strip() if doc else ""
            kind = doc_first.split("：")[0].split(":")[0].split(",")[0].split("，")[0]
            if kind not in _COVER_KINDS:
                kind = "其他"
            reqs = [f"{m.upper()} {p}" for m, p in _REQ_RE.findall(fn.body and ast.unparse(fn) or "")]
            api = reqs[0] if reqs else "-"
            cases.append({
                "id": fn.name, "feature": feature, "story": story, "title": title,
                "kind": kind, "note": doc_first, "api": api,
            })
    return cases


def gen_markdown(module: str, cases: list) -> str:
    lines = [
        f"# {module} 模块 - 用例清单（代码驱动）",
        "",
        f"> 由 `automation/scripts/cli-gen-case-inventory.py` 从测试代码自动生成，共 {len(cases)} 条。",
        "",
        "| 用例 | 功能组 | 场景 | 标题 | 覆盖类型 | 接口 |",
        "|------|--------|------|------|----------|------|",
    ]
    for c in sorted(cases, key=lambda x: x["id"]):
        lines.append(f"| {c['id']} | {c['feature']} | {c['story']} | {c['title']} | {c['kind']} | `{c['api']}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    module = sys.argv[sys.argv.index("--module") + 1] if "--module" in sys.argv else "tasks"
    target = TESTS_DIR / module
    if not target.is_dir():
        print(f"module dir not found: {target}")
        return 1
    cases = []
    for f in sorted(target.glob("test_*_code.py")):
        cases.extend(_collect(f))
    if not cases:
        print(f"no code-driven cases found under {target}")
        return 1
    doc = DOCS_DIR / f"case-inventory-{module}.md"
    doc.write_text(gen_markdown(module, cases), encoding="utf-8")
    print(f"generated: {doc} ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
