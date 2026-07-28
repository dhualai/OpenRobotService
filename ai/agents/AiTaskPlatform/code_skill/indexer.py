"""代码索引器 — 静态分析源码 → 提取函数签名 + 调用关系

Python: AST 解析，提取 async def / def / class
TypeScript/TSX: 正则提取 function / const xxx = () => / export
"""

import ast
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.code_skill.schemas import FunctionRef

logger = get_logger("TASK_AGENT")

# ── 常量 ──

_PY_EXTS = {".py"}
_TS_EXTS = {".ts", ".tsx"}
_SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".claude", "dist", "build", "logs", "__pycache__", "test_results",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", "htmlcov",
    "uploads", "old", ".idea", ".vscode", "qdrant", "kb",
}

# ── Python AST 提取 ──


def _extract_python(file_path: Path) -> List[FunctionRef]:
    """从单个 Python 文件提取所有函数/类定义"""
    results = []
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, Exception):
        return results

    rel_path = str(file_path)

    # 收集文件中所有函数调用名（用于构建调用图）
    all_calls: Dict[int, Set[str]] = {}

    class _CallVisitor(ast.NodeVisitor):
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name):
                calls = all_calls.setdefault(node.lineno, set())
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls = all_calls.setdefault(node.lineno, set())
                calls.add(node.func.attr)
            self.generic_visit(node)

    _CallVisitor().visit(tree)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 提取签名
            args_str = _format_py_args(node.args)
            returns = ""
            if node.returns:
                returns = f" -> {ast.unparse(node.returns)}"
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            signature = f"{prefix} {node.name}({args_str}){returns}:"

            # 提取 docstring
            docstring = ast.get_docstring(node) or ""
            docstring = docstring[:200].replace("\n", " ")

            # 提取调用关系
            calls = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        calls.add(child.func.id)
                    elif isinstance(child.func, ast.Attribute):
                        calls.add(child.func.attr)

            results.append(FunctionRef(
                name=node.name,
                file_path=rel_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                signature=signature,
                docstring=docstring,
                calls=sorted(calls)[:10],
                language="python",
            ))

        elif isinstance(node, ast.ClassDef):
            signature = f"class {node.name}"
            bases = [ast.unparse(b) for b in node.bases] if node.bases else []
            if bases:
                signature += f"({', '.join(bases)})"
            docstring = ast.get_docstring(node) or ""
            docstring = docstring[:200].replace("\n", " ")

            results.append(FunctionRef(
                name=node.name,
                file_path=rel_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                signature=signature,
                docstring=docstring,
                calls=[],
                language="python",
            ))

    return results


def _format_py_args(args: ast.arguments) -> str:
    """格式化 Python 函数参数列表"""
    parts = []
    for a in args.args:
        part = a.arg
        if a.annotation:
            ann = ast.unparse(a.annotation)
            # 简化复杂类型标注
            if len(ann) > 30:
                ann = ann.split("[")[0] + "[...]"
            part += f": {ann}"
        parts.append(part)
    return ", ".join(parts)


# ── TypeScript 正则提取 ──


_TS_FUNC_RE = re.compile(
    r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)'  # function foo(args)
)
_TS_ARROW_RE = re.compile(
    r'(?:export\s+)?(?:const|let)\s+(\w+)\s*[:=]\s*(?:async\s*)?\([^)]*\)\s*=>'  # const foo = () =>
)
_TS_CLASS_RE = re.compile(
    r'(?:export\s+)?class\s+(\w+)'
)
_TS_JSDOC_RE = re.compile(r'/\*\*([\s\S]*?)\*/')


def _extract_typescript(file_path: Path) -> List[FunctionRef]:
    """从单个 TypeScript/TSX 文件提取函数/类定义"""
    results = []
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return results

    rel_path = str(file_path)
    lines = source.split("\n")

    for m in _TS_FUNC_RE.finditer(source):
        name = m.group(1)
        args = m.group(2)[:100]
        line_no = source[:m.start()].count("\n") + 1
        doc = _extract_jsdoc_before(source, m.start(), lines)

        results.append(FunctionRef(
            name=name, file_path=rel_path,
            line_start=line_no, line_end=line_no + 5,
            signature=f"function {name}({args})",
            docstring=doc or "(无)", language="typescript",
        ))

    for m in _TS_ARROW_RE.finditer(source):
        name = m.group(1)
        line_no = source[:m.start()].count("\n") + 1
        doc = _extract_jsdoc_before(source, m.start(), lines)

        results.append(FunctionRef(
            name=name, file_path=rel_path,
            line_start=line_no, line_end=line_no + 3,
            signature=f"const {name} = (...) => {{ ... }}",
            docstring=doc or "(无)", language="typescript",
        ))

    for m in _TS_CLASS_RE.finditer(source):
        name = m.group(1)
        line_no = source[:m.start()].count("\n") + 1

        results.append(FunctionRef(
            name=name, file_path=rel_path,
            line_start=line_no, line_end=line_no + 10,
            signature=f"class {name}",
            docstring="(TypeScript 类)", language="typescript",
        ))

    return results


def _extract_jsdoc_before(source: str, pos: int, lines: List[str]) -> str:
    """提取函数前面的 JSDoc 注释"""
    before = source[:pos].rstrip()
    m = re.search(r'/\*\*([\s\S]*?)\*/\s*$', before)
    if m:
        doc = m.group(1).strip()
        # 去掉每行开头的 *
        cleaned = re.sub(r'\n\s*\*\s?', ' ', doc)
        return cleaned[:200]
    return ""


# ── 索引器 ──


class CodeIndexer:
    """全量代码索引器 — 离线运行一次"""

    def __init__(self, root_paths: List[str] = None):
        # 默认扫 AI + backend + frontend 三个模块
        self.root_paths = root_paths or [
            str(Path(__file__).resolve().parent.parent.parent),  # ai/
        ]
        self._functions: List[FunctionRef] = []
        self._name_index: Dict[str, List[int]] = {}  # 函数名 → 函数索引列表
        self._file_index: Dict[str, List[int]] = {}  # 文件路径 → 函数索引列表

    def build(self) -> "CodeIndexer":
        """扫描所有源码文件 → 提取函数 → 建索引 → 构建调用图"""
        t0 = time.perf_counter()
        self._functions = []
        logger.info(f"CodeIndexer: 开始扫描 {len(self.root_paths)} 个根路径...")

        for root in self.root_paths:
            root_p = Path(root)
            if not root_p.is_dir():
                logger.warning(f"CodeIndexer: {root} 不存在")
                continue

            for file_path in root_p.rglob("*"):
                if not file_path.is_file():
                    continue
                # 跳过隐藏目录和工具目录
                if any(p in _SKIP_DIRS for p in file_path.parts):
                    continue

                ext = file_path.suffix.lower()
                if ext in _PY_EXTS:
                    funcs = _extract_python(file_path)
                elif ext in _TS_EXTS:
                    funcs = _extract_typescript(file_path)
                else:
                    continue

                for f in funcs:
                    idx = len(self._functions)
                    self._functions.append(f)
                    self._name_index.setdefault(f.name, []).append(idx)
                    self._file_index.setdefault(f.file_path, []).append(idx)

        # 构建调用图（第二遍：填充 called_by）
        self._build_call_graph()

        elapsed = time.perf_counter() - t0
        logger.info(
            f"CodeIndexer: 完成 — {len(self._functions)} 个函数/类, "
            f"覆盖 {len(self._file_index)} 个文件 ({elapsed:.1f}s)"
        )
        return self

    def _build_call_graph(self):
        """构建反向调用关系（called_by）"""
        name_to_indices: Dict[str, List[int]] = {}
        for i, f in enumerate(self._functions):
            name_to_indices.setdefault(f.name, []).append(i)

        for i, f in enumerate(self._functions):
            for callee_name in f.calls:
                if callee_name in name_to_indices:
                    for callee_idx in name_to_indices[callee_name]:
                        if i not in self._functions[callee_idx].called_by:
                            self._functions[callee_idx].called_by.append(f.name)

    def search_by_name(self, name: str) -> List[FunctionRef]:
        """按函数名精确搜索"""
        indices = self._name_index.get(name, [])
        return [self._functions[i] for i in indices]

    def search_by_keyword(self, keyword: str) -> List[FunctionRef]:
        """按关键词搜索（函数名 + docstring + 文件路径）"""
        kw_lower = keyword.lower()
        results = []
        for f in self._functions:
            if kw_lower in f.name.lower():
                results.append(f)
            elif kw_lower in f.docstring.lower():
                results.append(f)
            elif kw_lower in f.file_path.lower():
                results.append(f)
        return results[:30]

    def expand_call_graph(self, func: FunctionRef, depth: int = 1) -> tuple:
        """沿调用图展开上下游"""
        upstream = []
        downstream = []
        seen = {func.name}
        for caller_name in func.called_by[:5]:
            for f in self.search_by_name(caller_name):
                if f.name not in seen:
                    upstream.append(f)
                    seen.add(f.name)
        for callee_name in func.calls[:10]:
            for f in self.search_by_name(callee_name):
                if f.name not in seen:
                    downstream.append(f)
                    seen.add(f.name)
        return upstream, downstream

    def save(self, path: str):
        """保存索引到 JSON"""
        data = {
            "root_paths": self.root_paths,
            "functions": [
                {
                    "name": f.name, "file_path": f.file_path,
                    "line_start": f.line_start, "line_end": f.line_end,
                    "signature": f.signature, "docstring": f.docstring,
                    "calls": f.calls, "called_by": f.called_by, "language": f.language,
                }
                for f in self._functions
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        logger.info(f"CodeIndexer: 索引已保存 → {path}")

    @classmethod
    def load(cls, path: str) -> "CodeIndexer":
        """从 JSON 文件加载索引"""
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        indexer = cls(root_paths=data["root_paths"])
        indexer._functions = []
        for d in data["functions"]:
            indexer._functions.append(FunctionRef(**d))
        # 重建索引
        for i, f in enumerate(indexer._functions):
            indexer._name_index.setdefault(f.name, []).append(i)
            indexer._file_index.setdefault(f.file_path, []).append(i)
        return indexer

    @property
    def function_count(self) -> int:
        return len(self._functions)

    @property
    def file_count(self) -> int:
        return len(self._file_index)
