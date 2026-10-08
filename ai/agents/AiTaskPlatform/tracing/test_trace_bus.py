"""TraceBus span 树契约：父子归属、attribute 合并、settlement、add/pop 兼容。"""
import pytest

from ai.agents.AiTaskPlatform.tracing import TraceBus


def test_add_pop_flat_compatible():
    bus = TraceBus()
    bus.add("llm", "ok", elapsed_ms=12)
    bus.add("parse", "error")
    flat = bus.pop()
    assert [x["node"] for x in flat] == ["llm", "parse"]
    assert flat[0]["status"] == "ok"
    assert flat[1]["status"] == "error"
    assert "ts" in flat[0]
    assert bus.pop() == []


def test_parent_child_and_tree():
    bus = TraceBus()
    with bus.start_span("supervisor.run"):
        with bus.start_span("plan", complexity="medium"):
            bus.set_attribute("派生数", 2)
        with bus.start_span("log_analyze"):
            bus.add_event("建索引")
            bus.add_event("R1")
            bus.set_attribute("window_applied", True)
        with bus.start_span("retrieve_history"):
            bus.set_attribute("verified", "confirmed")
            bus.set_attribute("命中数", 3)
    tree = bus.tree()
    assert len(tree) == 1
    root = tree[0]
    assert root["name"] == "supervisor.run"
    assert root["status"] == "ok"
    names = [c["name"] for c in root["children"]]
    assert names == ["plan", "log_analyze", "retrieve_history"]
    plan = root["children"][0]
    assert plan["attributes"]["complexity"] == "medium"
    assert plan["attributes"]["派生数"] == 2
    log = root["children"][1]
    assert [e["name"] for e in log["events"]] == ["建索引", "R1"]
    assert log["attributes"]["window_applied"] is True
    hist = root["children"][2]
    assert hist["attributes"]["verified"] == "confirmed"
    assert hist["attributes"]["命中数"] == 3
    # 扁平 pop 仍能取出每个 span 一条
    flat = bus.pop()
    assert [x["node"] for x in flat] == [
        "plan", "log_analyze", "retrieve_history", "supervisor.run",
    ]


def test_settlement_ok_and_error():
    bus = TraceBus()
    with bus.start_span("ok_span"):
        pass
    with pytest.raises(RuntimeError):
        with bus.start_span("boom"):
            raise RuntimeError("x")
    tree = bus.tree()
    assert tree[0]["status"] == "ok"
    assert tree[1]["status"] == "error"
    assert tree[1]["elapsed_ms"] >= 0


def test_set_status_skipped_not_overwritten_on_exit():
    bus = TraceBus()
    with bus.start_span("log_analyze"):
        bus.set_status("skipped", reason="历史方案已验证")
    tree = bus.tree()
    assert tree[0]["status"] == "skipped"
    assert tree[0]["attributes"]["reason"] == "历史方案已验证"


def test_add_inside_span_becomes_child():
    bus = TraceBus()
    with bus.start_span("discuss"):
        bus.add("llm", "ok")
    tree = bus.tree()
    assert tree[0]["name"] == "discuss"
    assert tree[0]["children"][0]["name"] == "llm"
    assert tree[0]["children"][0]["status"] == "ok"


def test_reset_keeps_trace_list_identity():
    bus = TraceBus()
    alias = bus._trace
    bus.add("a", "ok")
    bus.reset()
    assert alias is bus._trace
    assert bus._trace == []
    assert bus.tree() == []


def test_nest_progress_todos_groups_log_rounds():
    from ai.agents.AiTaskPlatform.tracing import nest_progress_todos
    items = [
        {"id": "planning", "description": "规划", "capability": ""},
        {"id": 1, "description": "分析日志", "capability": "log_analyze"},
        {"id": "log_index", "description": "建索引", "capability": "log_analyze"},
        {"id": "log_r1", "description": "R1", "capability": "log_analyze"},
        {"id": 2, "description": "历史方案", "capability": "retrieve_history"},
    ]
    out = nest_progress_todos(items)
    assert [x["id"] for x in out] == ["planning", 1, 2]
    kids = out[1]["children"]
    assert [c["id"] for c in kids] == ["log_index", "log_r1"]
