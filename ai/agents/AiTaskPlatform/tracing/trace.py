"""流程埋点（从 pipeline.py 拆分，独立成模块）

职责：
  - 定义可追踪的流程节点常量 NODE_*
  - TraceBus: 请求级追踪。旧 API add()/pop() 仍返回扁平 list；
    新 API start_span/add_event/set_status 产出 parent/child span 树。

不依赖任何 AI 客户端，纯记录。AiTaskAgent 持有 TraceBus 实例即可。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


class Node:
    """可追踪的流程节点（供测试 Agent 对照）"""
    OVERHEAD = "overhead"          # 端点路由 + 客户端初始化
    LOAD_CONTEXT = "load_context"  # 加载工单上下文
    RETRIEVE = "retrieve"          # 三路并行分析
    ATTACHMENT = "attachment"      # 附件分析
    KNOWLEDGE = "knowledge"        # 历史工单检索
    BUILD_PROMPT = "build_prompt"  # Prompt 构建
    LLM = "llm"                    # LLM 调用
    PARSE = "parse"                # 结果解析
    DISCUSS = "discuss"            # @AI 讨论
    COMMENT = "comment"            # 写 task_comments
    SUBMIT = "submit"              # 方案提交


def _now_ms() -> float:
    return time.perf_counter() * 1000


@dataclass
class Span:
    """单个遥测 span：可挂 attribute / event / 子 span。"""
    span_id: str
    name: str
    parent_id: Optional[str] = None
    status: str = "running"  # running | ok | error | skipped
    start_ts: float = 0.0
    end_ts: float = 0.0
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    child_ids: List[str] = field(default_factory=list)

    def elapsed_ms(self) -> int:
        end = self.end_ts or _now_ms()
        return max(0, round(end - self.start_ts))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "span_id": self.span_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms(),
            "attributes": dict(self.attributes),
            "events": list(self.events),
            "children": [],  # 由 TraceBus.tree() 填嵌套
        }


class TraceBus:
    """请求级埋点容器。每次对外请求前 reset，结束后 pop 全量。

    兼容：add()/pop() 仍是扁平 list[{node,status,ts,...}]。
    新契约：start_span 上下文管理器 + add_event + set_status/set_attribute。
    """

    def __init__(self):
        self._trace: list = []
        self._spans: Dict[str, Span] = {}
        self._roots: List[str] = []
        self._stack: List[str] = []
        self._seq = 0

    def _current_id(self) -> Optional[str]:
        return self._stack[-1] if self._stack else None

    def _current_span(self) -> Optional[Span]:
        sid = self._current_id()
        return self._spans.get(sid) if sid else None

    def _new_span(self, name: str, attributes: Optional[dict] = None) -> Span:
        self._seq += 1
        parent_id = self._current_id()
        span = Span(
            span_id=f"s{self._seq}",
            name=str(name),
            parent_id=parent_id,
            status="running",
            start_ts=_now_ms(),
            attributes=dict(attributes or {}),
        )
        self._spans[span.span_id] = span
        if parent_id and parent_id in self._spans:
            self._spans[parent_id].child_ids.append(span.span_id)
        else:
            self._roots.append(span.span_id)
        return span

    def _append_flat(self, node: str, status: str, span: Optional[Span] = None, **kwargs) -> dict:
        entry = {"node": node, "status": status, "ts": round(_now_ms())}
        if span is not None:
            entry["span_id"] = span.span_id
            if span.parent_id:
                entry["parent_id"] = span.parent_id
            if span.elapsed_ms():
                entry["elapsed_ms"] = span.elapsed_ms()
        extra = dict(kwargs)
        extra.pop("node", None)
        extra.pop("status", None)
        extra.pop("ts", None)
        entry.update(extra)
        self._trace.append(entry)
        return entry

    def add(self, node: str, status: str, **kwargs):
        """追加一条追踪记录。status: ok | error | skipped（兼容旧调用）。"""
        span = self._new_span(node, attributes={k: v for k, v in kwargs.items() if k != "elapsed_ms"})
        span.status = status or "ok"
        span.end_ts = _now_ms()
        if "elapsed_ms" in kwargs:
            try:
                span.end_ts = span.start_ts + float(kwargs["elapsed_ms"])
            except (TypeError, ValueError):
                pass
        self._append_flat(node, span.status, span=span, **kwargs)

    def reset(self):
        """清空（新请求开始）。原地 clear，保持 pipeline 对 _trace 的别名有效。"""
        self._trace.clear()
        self._spans.clear()
        self._roots.clear()
        self._stack.clear()
        self._seq = 0

    def pop(self) -> list:
        """取出全部追踪记录并清空（每次请求独立）。仍返回扁平 list。"""
        trace = list(self._trace)
        self.reset()
        return trace

    @contextmanager
    def start_span(self, name: str, **attributes) -> Iterator[Span]:
        """开启一个 span，退出时结算 status（成功 ok，未捕获异常 error）。"""
        span = self._new_span(name, attributes=attributes)
        self._stack.append(span.span_id)
        try:
            yield span
            if span.status == "running":
                span.status = "ok"
        except Exception:
            if span.status == "running":
                span.status = "error"
            raise
        finally:
            span.end_ts = _now_ms()
            if self._stack and self._stack[-1] == span.span_id:
                self._stack.pop()
            self._append_flat(name, span.status, span=span, **span.attributes)

    def add_event(self, name: str, **attributes) -> None:
        """给当前 span 加一个事件（如 建索引 / R1）。无当前 span 则忽略。"""
        span = self._current_span()
        if span is None:
            return
        span.events.append({
            "name": str(name),
            "ts": round(_now_ms()),
            "attributes": dict(attributes),
        })

    def set_status(self, status: str, **attributes) -> None:
        """覆盖当前 span 的结算状态（ok / error / skipped）。"""
        span = self._current_span()
        if span is None:
            return
        span.status = str(status or span.status)
        if attributes:
            span.attributes.update(attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        span = self._current_span()
        if span is None:
            return
        span.attributes[str(key)] = value

    def tree(self) -> List[Dict[str, Any]]:
        """当前 span 树快照（含尚未 pop 的 running 节点）。"""
        def _walk(span_id: str) -> Dict[str, Any]:
            span = self._spans[span_id]
            node = span.to_dict()
            node["children"] = [_walk(cid) for cid in span.child_ids if cid in self._spans]
            return node
        return [_walk(rid) for rid in self._roots if rid in self._spans]


def _known_ids(items: list) -> set:
    found = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if it.get("id") is not None:
            found.add(str(it.get("id")))
        found |= _known_ids(it.get("children") or [])
    return found


# span 名 → 过程区显示名。能力名不列在这里：前端 CAP_LABELS 兜底，行自带 description 优先。
_SPAN_LABELS = {
    "supervisor.run": "分析",
    "plan": "规划排查步骤",
}
# span 名 → 可能对应它的行 id/capability（plan 这一段的行 id 是 planning）
_SPAN_ROW_ALIASES = {
    "plan": ("planning", "plan"),
}


def _event_child(ev: Any, cap: str, known: set, skip_name: str = "") -> Optional[dict]:
    """span 事件 → 过程区子节点。行里已有的步骤 id 不重复挂。"""
    if not isinstance(ev, dict):
        return None
    name = str(ev.get("name") or "").strip()
    if not name or name == skip_name:
        return None
    attrs = ev.get("attributes") if isinstance(ev.get("attributes"), dict) else {}
    eid = str(attrs.get("id") or "") or f"ev_{cap}_{ev.get('ts')}_{name}"
    if eid in known:
        return None
    known.add(eid)
    status = str(attrs.get("status") or "completed")
    return {
        "id": eid,
        "description": name,
        "status": "in_progress" if status in ("running", "in_progress") else "completed",
        "capability": cap,
    }


def _span_forest(span_tree: list, rows: list) -> list:
    """按真实 span 层级重排行，span 的 events 作为叶子子节点。

    过程区因此与埋点同构：`supervisor.run`（分析）→ `plan`（规划）+ 各能力 → 各能力的
    子步骤/事件。span 名匹配不到行的（如 supervisor.run / plan）生成合成节点；匹配不到
    span 的行留在顶层，不丢。能力 span 与 plan 是兄弟——埋点里它们同属 run 下，这里照实
    呈现，不为了好看的层级去搬动。
    """
    pool = [dict(r) for r in rows]
    known = _known_ids(rows)
    seq = 0

    def _claim(span_name: str) -> Optional[dict]:
        keys = {span_name, *_SPAN_ROW_ALIASES.get(span_name, ())}
        for i, row in enumerate(pool):
            if str(row.get("capability") or "") in keys or str(row.get("id") or "") in keys:
                return pool.pop(i)
        return None

    def _build(span: dict) -> Optional[dict]:
        nonlocal seq
        seq += 1
        name = str(span.get("name") or "")
        row = _claim(name) if name else None
        row = row or {}
        kids = [dict(c) for c in row.get("children") or []]
        kids += [n for n in (_build(c) for c in span.get("children") or [] if isinstance(c, dict)) if n]
        for ev in span.get("events") or []:
            child = _event_child(ev, name, known, skip_name=str(row.get("description") or ""))
            if child is not None:
                kids.append(child)
        # 既没占到行又没有子节点/事件的 span（如刚开跑的 run）不输出，免得留个空壳
        if not row and not kids:
            return None
        node: Dict[str, Any] = {
            "id": row.get("id") if row.get("id") is not None else f"sp{seq}",
            "description": row.get("description") or _SPAN_LABELS.get(name) or name,
            "capability": row.get("capability") or name,
            # 行自带状态优先：那是 supervisor 的权威声明，span 只用来给合成节点定状态
            "status": row.get("status") or ("in_progress" if str(span.get("status")) == "running" else "completed"),
        }
        if row.get("phase"):
            node["phase"] = row["phase"]
        if kids:
            node["children"] = kids
        return node

    out = [n for n in (_build(s) for s in span_tree or [] if isinstance(s, dict)) if n]
    out.extend(pool)
    return out


def nest_progress_todos(items: list, span_tree: list | None = None) -> list:
    """把子步骤挂到所属能力下面，并补上 span 事件，过程区呈父子树。"""
    rows = [dict(x) for x in (items or []) if isinstance(x, dict)]
    if not rows:
        return []
    by_id = {str(it.get("id")): it for it in rows if it.get("id") is not None}
    explicit = []
    rest = []
    for it in rows:
        pid = it.get("parent_id")
        if pid is not None and str(pid) in by_id and str(pid) != str(it.get("id")):
            explicit.append(it)
        else:
            rest.append(it)
    parents: list = []
    children: list = []
    _child_prefixes = ("log_index", "log_r", "log_file_", "log_retry_")
    for it in rest:
        iid = str(it.get("id") or "")
        if iid == "log_index" or iid.startswith(_child_prefixes):
            children.append(it)
        else:
            parents.append(it)
    attached = False
    out = []
    for p in parents:
        item = dict(p)
        cap = str(item.get("capability") or "")
        if not attached and (cap == "log_analyze" or str(item.get("id")) == "log_analyze"):
            kids = [
                c for c in children
                if str(c.get("capability") or "log_analyze") == "log_analyze"
            ]
            if kids:
                item["children"] = kids
                attached = True
        out.append(item)
    if not attached:
        out.extend(children)
    if explicit:
        placed = {str(it.get("id")) for it in out}
        for kid in explicit:
            parent = next((p for p in out if str(p.get("id")) == str(kid.get("parent_id"))), None)
            if parent is None or str(kid.get("id")) in placed:
                if str(kid.get("id")) not in placed:
                    out.append(kid)
                continue
            parent.setdefault("children", []).append(kid)
    if span_tree:
        out = _span_forest(span_tree, out)
    return out
