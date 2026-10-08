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


def nest_progress_todos(items: list) -> list:
    """把日志分析子步骤（建索引 / R1..Rn）挂到所属能力下面，过程区呈 span 树。"""
    rows = [dict(x) for x in (items or []) if isinstance(x, dict)]
    if not rows:
        return []
    parents: list = []
    children: list = []
    for it in rows:
        iid = str(it.get("id") or "")
        if iid == "log_index" or iid.startswith("log_r"):
            children.append(it)
        else:
            parents.append(it)
    if not children:
        return rows
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
    return out
