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


def test_nest_progress_todos_honors_parent_and_span_events():
    from ai.agents.AiTaskPlatform.tracing import nest_progress_todos
    items = [
        {"id": "hist", "description": "查历史方案", "capability": "retrieve_history"},
        {"id": "note", "description": "命中已验证方案", "capability": "retrieve_history", "parent_id": "hist"},
        {"id": "log_r1", "description": "R1", "capability": "log_analyze"},
        {"id": "logs", "description": "分析日志", "capability": "log_analyze"},
    ]
    tree = [{
        "name": "retrieve_history",
        "events": [
            {"name": "retrieve", "ts": 1, "attributes": {"count": 2}},
            {"name": "R1", "ts": 2, "attributes": {"id": "log_r1"}},
        ],
        "children": [],
    }]
    out = nest_progress_todos(items, span_tree=tree)
    hist = next(x for x in out if x["id"] == "hist")
    child_ids = [c["id"] for c in hist["children"]]
    assert "note" in child_ids
    assert any(c["description"] == "retrieve" for c in hist["children"])
    assert "log_r1" not in child_ids
    logs = next(x for x in out if x["id"] == "logs")
    assert [c["id"] for c in logs["children"]] == ["log_r1"]


def _supervisor_tree(log_status="running"):
    """真实埋点形状：supervisor.run →（plan + 各能力）。能力 span 是 plan 的兄弟。"""
    return [{
        "span_id": "s1", "name": "supervisor.run", "status": "running",
        "events": [], "children": [
            {"span_id": "s2", "name": "plan", "status": "ok",
             "events": [], "children": []},
            {"span_id": "s3", "name": "log_analyze", "status": log_status,
             "events": [{"name": "R1", "ts": 2, "attributes": {"id": "log_r1"}}],
             "children": []},
            {"span_id": "s4", "name": "retrieve_history", "status": "ok",
             "events": [{"name": "retrieve", "ts": 3, "attributes": {"count": 2}}],
             "children": []},
        ],
    }]


def test_nest_progress_todos_mirrors_real_span_tree():
    """过程区结构照搬真实埋点：分析 →（规划 + 各能力）→ 各能力的子步骤/事件。"""
    from ai.agents.AiTaskPlatform.tracing import nest_progress_todos
    items = [
        {"id": "planning", "description": "规划", "capability": ""},
        {"id": 1, "description": "分析日志", "capability": "log_analyze"},
        {"id": "log_index", "description": "建索引", "capability": "log_analyze"},
        {"id": 2, "description": "历史方案", "capability": "retrieve_history"},
    ]
    out = nest_progress_todos(items, span_tree=_supervisor_tree())

    assert len(out) == 1, "整个 run 归一个根节点"
    root = out[0]
    assert root["description"] == "分析"
    assert root["status"] == "in_progress", "span 还在跑就该是进行中"
    # 规划与各能力同属 run 下：这是埋点的真实结构，不搬成 plan 的子节点
    assert [c["id"] for c in root["children"]] == ["planning", 1, 2]

    plan, log, hist = root["children"]
    assert plan["description"] == "规划"
    assert log["status"] == "in_progress"
    assert hist["status"] == "completed"
    assert [c["id"] for c in log["children"]] == ["log_index", "log_r1"]
    assert [c["description"] for c in hist["children"]] == ["retrieve"]
    # 行里已有的步骤 id 不因 span 事件重复挂
    assert len(log["children"]) == 2


def test_nest_progress_todos_keeps_unmatched_rows_on_top():
    """没有对应 span 的行留在顶层，不因重排丢掉。"""
    from ai.agents.AiTaskPlatform.tracing import nest_progress_todos
    items = [
        {"id": "planning", "description": "规划", "capability": ""},
        {"id": 1, "description": "分析日志", "capability": "log_analyze"},
        {"id": 9, "description": "本轮补充", "capability": "inject"},
    ]
    out = nest_progress_todos(items, span_tree=_supervisor_tree())
    assert out[0]["description"] == "分析"
    assert [x["id"] for x in out[1:]] == [9]


def test_span_forest_handles_missing_ids_and_empty_tree():
    """span 缺 span_id、空树都不能炸；什么都没贡献的 span 不留空壳。"""
    from ai.agents.AiTaskPlatform.tracing import nest_progress_todos
    items = [{"id": 1, "description": "分析日志", "capability": "log_analyze"}]
    out = nest_progress_todos(items, span_tree=[{"name": "supervisor.run", "children": []}])
    assert [x["id"] for x in out] == [1], "挂不上的行留在顶层，不因重排丢掉"
    assert nest_progress_todos(items, span_tree=[]) == items
    assert nest_progress_todos(items, span_tree=None) == items


def test_span_node_does_not_override_row_status():
    """行自带 status/phase 时以行为准，span 状态只用来给合成节点定状态。"""
    from ai.agents.AiTaskPlatform.tracing import nest_progress_todos
    items = [{"id": "planning", "description": "规划", "capability": "",
              "status": "completed", "phase": "done"}]
    tree = [{
        "name": "supervisor.run", "status": "running", "events": [],
        "children": [{"name": "plan", "status": "running", "events": [], "children": []}],
    }]
    out = nest_progress_todos(items, span_tree=tree)
    plan = out[0]["children"][0]
    assert plan["status"] == "completed" and plan["phase"] == "done"
    assert out[0]["status"] == "in_progress", "合成节点没有行，按 span 状态算"


def test_span_forest_accepts_live_trace_bus_tree():
    """真 TraceBus.tree() 的产出直接喂进来也要能排（校验桩与真实埋点同构）。"""
    from ai.agents.AiTaskPlatform.tracing import TraceBus, nest_progress_todos
    bus = TraceBus()
    with bus.start_span("supervisor.run"):
        with bus.start_span("plan"):
            pass
        with bus.start_span("log_analyze"):
            bus.add_event("R1", id="log_r1")
        snapshot = bus.tree()  # 运行中取快照：run 还是 running
    assert snapshot[0]["status"] == "running"
    rows = [
        {"id": "planning", "description": "规划", "capability": ""},
        {"id": 1, "description": "分析日志", "capability": "log_analyze"},
    ]
    out = nest_progress_todos(rows, span_tree=snapshot)
    assert len(out) == 1 and out[0]["description"] == "分析"
    assert out[0]["status"] == "in_progress", "run 还没结束"
    assert [c["id"] for c in out[0]["children"]] == ["planning", 1]
    assert [c["id"] for c in out[0]["children"][1]["children"]] == ["log_r1"]
    # 收尾时再排一次：run 已闭合 → 根节点转已完成
    done = nest_progress_todos(rows, span_tree=bus.tree())
    assert done[0]["status"] == "completed"
