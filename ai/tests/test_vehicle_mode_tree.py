"""车型分叉树直出（ORS-980）：选项点击走树不走 LLM。

三层覆盖：
1. parse_fork_tree 全树解析（类目/场景/选项表/深化分叉，管理章节排除）
2. _match_tree_step / tree_direct_reply（匹配序、叶子收尾、nav 写回、话术防重）
3. 管线短路集成（_agent_think_stream：命中树零 LLM 零检索，choices 全量直出）
"""

import random
from pathlib import Path

import pytest

from ai.api.vehicle_mode import (
    _match_tree_step,
    parse_fork_tree,
    tree_direct_reply,
)

_TREE_MD = """# XQE 故障分叉树

## 1. 车辆停着不动（任务中断）

**引导问题：车是在什么位置停住的？**

### 1.1 行驶途中/半路停下
**引导问题：界面上有什么提示？**

| 选项 | 现象 | 判断 | 解决方案 | 处置 |
|---|---|---|---|---|
| A | 报「避障」/前方有障碍 | 激光识别到障碍物 | 清理障碍后重发任务 | 现场 |
| B | 无任何提示/正常显示 | 后台参数问题 | 走下方 F 深化分叉（看监控路径） | 后台 |

**F 深化分叉——引导问题：监控界面有没有绿色路径？**

| 选项 | 监控观察 | 判断 | 解决方案 | 处置 |
|---|---|---|---|---|
| F1 | 无绿色路径 | 调度未规划 | 重启算法服务 | 后台 |
| F2 | 有路径但车不动 | 车端执行问题 | 重发任务 | 后台 |

### 1.2 在取货位停住不动作
**引导问题：界面上有什么提示？**

| 选项 | 现象 | 判断 | 解决方案 | 处置 |
|---|---|---|---|---|
| A | 报「货叉移动达到限位」 | 横向偏差超范围 | 重新标定雷达 | 后台 |

## 2. 堆叠/堆垛失败

| 选项 | 现象 | 判断 | 解决方案 | 处置 |
|---|---|---|---|---|
| A | 报「识别超时」 | 识别框内无支腿 | 调整识别基准线 | 后台→维修 |
| B | 反复调整后任务失败 | 行为树问题 | 更新行为树文件 | 后台 |

## 附：数据质量问题（整理中发现，供核对）

| 选项 | 现象 | 判断 | 解决方案 | 处置 |
|---|---|---|---|---|
| A | 不该出现的行 | — | — | — |
"""


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    d = tmp_path / "company" / "XQE"
    d.mkdir(parents=True)
    (d / "00_故障分叉树.md").write_text(_TREE_MD, encoding="utf-8")
    return tmp_path


@pytest.fixture
def tree(kb: Path) -> list:
    return parse_fork_tree("XQE", kb_root=kb)


# ---- 解析 ----

def test_parse_categories_and_skip_admin_sections(tree):
    names = [c["name"] for c in tree]
    assert names == ["车辆停着不动（任务中断）", "堆叠/堆垛失败"]
    assert all("附" not in n for n in names), "无序号管理章节必须排除"


def test_parse_scenes_rows_deep(tree):
    cat1 = tree[0]
    assert cat1["question"] == "车是在什么位置停住的？"
    assert [s["name"] for s in cat1["scenes"]] == ["1.1 行驶途中/半路停下", "1.2 在取货位停住不动作"]
    s11 = cat1["scenes"][0]
    assert s11["question"] == "界面上有什么提示？"
    assert [r["phen"] for r in s11["rows"]] == ["报「避障」/前方有障碍", "无任何提示/正常显示"]
    # 深化分叉：挂场景，不在普通 rows 里
    assert s11["deep_q"] == "监控界面有没有绿色路径？"
    assert [r["phen"] for r in s11["deep_rows"]] == ["无绿色路径", "有路径但车不动"]
    # 类目直属表（无场景层的类目 2）
    assert [r["phen"] for r in tree[1]["rows"]] == ["报「识别超时」", "反复调整后任务失败"]


def test_parse_missing_tree_returns_empty(tmp_path):
    assert parse_fork_tree("XQE", kb_root=tmp_path) == []


# ---- 匹配 ----

def test_match_category_scene_option_deep(tree):
    assert _match_tree_step("堆叠/堆垛失败", tree, {})[
        "kind"] == "category"
    nav = {"category": "车辆停着不动（任务中断）"}
    assert _match_tree_step("1.1 行驶途中/半路停下", tree, nav)["kind"] == "scene"
    nav2 = {**nav, "scene": "1.1 行驶途中/半路停下"}
    assert _match_tree_step("报「避障」/前方有障碍", tree, nav2)["kind"] == "option"
    assert _match_tree_step("无绿色路径", tree, nav2)["kind"] == "option"  # 深化行


def test_match_cross_category_jump_and_miss(tree):
    # 跨类目跳转：类目名全局匹配兜住
    assert _match_tree_step("堆叠/堆垛失败", tree, {"category": "车辆停着不动（任务中断）"}) is not None
    # 自由输入不命中 → None（走 AI）
    assert _match_tree_step("车今天有点奇怪", tree, {}) is None
    assert _match_tree_step("", tree, {}) is None
    # 场景下钻必须先点所属类目（nav 没定位时不认场景名以外类目）
    assert _match_tree_step("1.2 在取货位停住不动作", tree, {}) is None


# ---- 组装（含话术多样化与 nav 写回）----

def test_reply_category_with_scenes(tree, kb):
    vm = {"model": "XQE"}
    r = tree_direct_reply("车辆停着不动（任务中断）", vm, "XQE", kb_root=kb, rng=random.Random(1))
    assert r["kind"] == "category"
    assert r["action"] == "ask"
    assert r["choices"] == ["1.1 行驶途中/半路停下", "1.2 在取货位停住不动作"]
    assert "车是在什么位置停住的" in r["message"]
    assert vm["tree_nav"] == {"category": "车辆停着不动（任务中断）", "scene": None}


def test_reply_category_direct_rows(tree, kb):
    vm = {"model": "XQE"}
    r = tree_direct_reply("堆叠/堆垛失败", vm, "XQE", kb_root=kb, rng=random.Random(1))
    assert r["choices"] == ["报「识别超时」", "反复调整后任务失败"]


def test_reply_option_leaf_clears_nav(tree, kb):
    vm = {"model": "XQE", "tree_nav": {"category": "堆叠/堆垛失败", "scene": None}}
    r = tree_direct_reply("报「识别超时」", vm, "XQE", kb_root=kb, rng=random.Random(1))
    assert r["kind"] == "option"
    assert r["action"] == "answer"
    assert r["choices"] is None
    assert "调整识别基准线" in r["message"] and "后台→维修" in r["message"]
    assert vm["tree_nav"] == {}, "叶子命中后树导览结束"


def test_reply_deep_entry_then_deep_leaf(tree, kb):
    vm = {"model": "XQE", "tree_nav": {"category": "车辆停着不动（任务中断）", "scene": "1.1 行驶途中/半路停下"}}
    r = tree_direct_reply("无任何提示/正常显示", vm, "XQE", kb_root=kb, rng=random.Random(1))
    assert r["action"] == "ask"
    assert r["choices"] == ["无绿色路径", "有路径但车不动"]
    assert "绿色路径" in r["message"]
    r2 = tree_direct_reply("无绿色路径", vm, "XQE", kb_root=kb, rng=random.Random(1))
    assert r2["action"] == "answer" and "重启算法服务" in r2["message"]


def test_reply_phrases_vary_and_no_immediate_repeat(tree, kb):
    vm = {"model": "XQE"}
    seen = set()
    last = ""
    for i in range(12):
        vm.pop("tree_nav", None)
        r = tree_direct_reply("堆叠/堆垛失败", vm, "XQE", kb_root=kb, rng=random.Random(i))
        lead = r["message"].split("\n")[0]
        assert lead != last, "相邻两轮承接语不得重复"
        seen.add(lead)
        last = lead
    assert len(seen) >= 3, "多轮后话术池应有明显变化（不呆）"


def test_reply_free_text_returns_none(tree, kb):
    vm = {"model": "XQE", "tree_nav": {"category": "堆叠/堆垛失败"}}
    assert tree_direct_reply("车今天不太对劲", vm, "XQE", kb_root=kb) is None


# ================================================================
# 管线短路集成：命中树零 LLM 零检索；自由输入穿透走 AI
# ================================================================

async def _drive(platform, sid, query):
    """按真实入口跑一轮（对齐 test_vehicle_mode_pipeline._drive_stream）。"""
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState

    mem = await platform._memory_manager.get_memory(sid)
    if "vehicle_mode" not in mem.metadata:
        mem.metadata["vehicle_mode"] = {"model": "XQE"}
    state = AgentState(session_id=sid, phase="idle",
                       original_query=query, problem_summary=query)
    request = type("R", (), {})()
    request.session_id = sid
    request.query = query
    request.skip_retrieval = False
    request.created_by = "tester"
    tokens, result = [], None
    async for ev in platform._agent_think_stream(request, state, mem):
        if ev["event"] == "token":
            tokens.append(ev["data"] if isinstance(ev["data"], str) else ev["data"].get("token", ""))
        elif ev["event"] == "result":
            result = ev["data"]
    return "".join(tokens), result


async def _boom_async(*a, **k):
    raise AssertionError("树直出轮不得触碰检索")


async def _boom_llm(*a, **k):
    raise AssertionError("树直出轮不得触碰 LLM")


async def test_pipeline_tree_shortcut_zero_llm(platform, kb, monkeypatch):
    """点类目按钮（精确类目名）→ 秒回下一层选项，零检索零 LLM，
    choices 全量直出（不受 LLM 幻觉闸门的 ≤3 条截断）。"""
    import ai.api.vehicle_mode as vm_mod
    monkeypatch.setattr(vm_mod, "_default_kb_root", lambda: kb)
    sid = "tree-shortcut-cat"
    platform._retriever.retrieve_domain_dual = _boom_async
    platform._llm_client.stream = _boom_llm
    text, result = await _drive(platform, sid, "堆叠/堆垛失败")
    assert "具体是什么表现" in text
    assert result["vehicle_choices"] == ["报「识别超时」", "反复调整后任务失败"]
    assert result["action"] == "ask"
    vm = (await platform._memory_manager.get_memory(sid)).metadata["vehicle_mode"]
    assert vm["tree_nav"]["category"] == "堆叠/堆垛失败"
    assert vm["last_choices"] == result["vehicle_choices"]


async def test_pipeline_tree_leaf_and_free_text_falls_through(platform, kb, monkeypatch):
    """类目→选项走到叶子（answer 零 LLM）；随后自由输入不命中树，
    穿透到正常 AI 链路（LLM 被调用，且 turns 带完整树导览上下文）。"""
    import json as _json
    from unittest.mock import AsyncMock
    import ai.api.vehicle_mode as vm_mod
    monkeypatch.setattr(vm_mod, "_default_kb_root", lambda: kb)
    sid = "tree-leaf-free"

    # 两步树导览（类目 → 叶子选项），全程 LLM 即炸
    platform._retriever.retrieve_domain_dual = _boom_async
    platform._llm_client.stream = _boom_llm
    await _drive(platform, sid, "堆叠/堆垛失败")
    leaf_text, leaf = await _drive(platform, sid, "报「识别超时」")
    assert "调整识别基准线" in leaf_text
    assert leaf["action"] == "answer" and leaf.get("vehicle_choices") is None
    vm = (await platform._memory_manager.get_memory(sid)).metadata["vehicle_mode"]
    assert vm["tree_nav"] == {}

    # 自由输入：树不命中 → 正常链路。检索给空、LLM 给合法 JSON answer。
    async def _dual(q, domain, top_k=8, query_filter=None):
        return [], []
    platform._retriever.retrieve_domain_dual = _dual
    _free_reply = ('```json\n' + _json.dumps({
        "thinking": "树外自由输入", "action": "answer",
        "message": "刚才那个问题我再补充问下。",
    }, ensure_ascii=False) + '\n```')
    # stream 保持 None（conftest 同款约定）：pipeline 判定不支持流式 → 走 complete()
    platform._llm_client.stream = None
    platform._llm_client.complete = AsyncMock(return_value=_free_reply)
    text2, result2 = await _drive(platform, sid, "刚才那个识别超时调完基准线还是不行")
    assert result2 is not None and result2["action"] == "answer"
    mem = await platform._memory_manager.get_memory(sid)
    joined = "".join(t.get("content", "") for t in mem.turns)
    assert "报「识别超时」" in joined, "树导览轮次必须留在 turns 里（AI 接手时上下文完整）"
