"""工单 Agent 状态：结构规整、渲染、折叠输出的容错。

全是纯函数，不调 LLM、不连库。
"""
from ai.agents.AiTaskPlatform.contexts.agent_state import (
    STATE_FIELDS,
    build_merge_prompt,
    empty_state,
    is_empty,
    merge_state,
    normalize_state,
    render_state_block,
)


def test_normalize_handles_none_and_garbage():
    assert is_empty(normalize_state(None))
    assert is_empty(normalize_state("不是 dict"))
    assert is_empty(normalize_state(123))
    assert is_empty(normalize_state([]))


def test_normalize_fills_all_keys_and_fixes_types():
    s = normalize_state({"goal": 42, "confirmed_facts": "单条事实"})
    # 所有九栏键都在
    assert set(s.keys()) == {k for k, _ in STATE_FIELDS}
    # 字符串栏收数字；列表栏把单个字符串收成单元素列表
    assert s["goal"] == "42"
    assert s["confirmed_facts"] == ["单条事实"]
    # 未提供的栏位为空
    assert s["ruled_out"] == []


def test_normalize_drops_empty_items_in_lists():
    s = normalize_state({"failed_attempts": ["a", "", None, [], {}, "b"]})
    assert s["failed_attempts"] == ["a", "b"]


def test_empty_state_renders_nothing():
    """短工单不注入状态块 —— 这是「无回归」的关键保证。"""
    assert render_state_block(None) == ""
    assert render_state_block(empty_state()) == ""
    assert render_state_block({"goal": "", "confirmed_facts": []}) == ""


def test_render_includes_source_for_facts():
    block = render_state_block({
        "goal": "看清根因",
        "confirmed_facts": [{"fact": "车定位飘到电梯外", "source": "评论 12"}],
        "key_identifiers": ["HY003", "4100005"],
    })
    assert "工单状态" in block
    assert "看清根因" in block
    assert "车定位飘到电梯外（出处：评论 12）" in block
    assert "- HY003" in block
    assert "- 4100005" in block


def test_render_skips_empty_sections():
    block = render_state_block({"goal": "只有目标"})
    assert "目标" in block
    assert "已排除的方向" not in block


def test_merge_state_extracts_json_from_prose():
    merged = merge_state('前言 ```json\n{"goal": "g"}\n``` 后语', None)
    assert merged is not None
    assert merged["goal"] == "g"


def test_merge_state_returns_none_on_unparsable():
    """解析失败必须返回 None —— 调用方据此不推进水位线，下次重试。"""
    assert merge_state("完全不是 JSON", {"goal": "旧"}) is None
    assert merge_state("", None) is None


def test_merge_state_rejects_wiping_existing_state():
    """模型把已有状态清空 → 视为异常输出，不采纳（防一轮抹掉全部结论）。"""
    prev = {"goal": "旧目标", "confirmed_facts": ["旧事实"]}
    assert merge_state('{"goal": ""}', prev) is None
    # 但本来就没状态时，空输出是合法的
    assert merge_state('{"goal": ""}', None) is not None


def test_build_merge_prompt_carries_prev_state():
    p = build_merge_prompt({"goal": "旧目标"}, ["[张三] 新情况"])
    assert "旧目标" in p
    assert "[张三] 新情况" in p


def test_build_merge_prompt_accepts_string():
    p = build_merge_prompt(None, "[张三] 一句话")
    assert "[张三] 一句话" in p
