"""回归信号的纯函数测试：不连库、不调 LLM。

这些信号是「没退化」的唯一依据，算法本身必须先可信。
"""
from ai.agents.AiTaskPlatform.regression.signals import (
    aggregate,
    check_citations,
    cited_ticket_ids,
    compare,
    find_mentioned_names,
    is_giveup,
    is_plausible_person_name,
    name_hallucination,
    score_case,
)


# ── S1 人名 ─────────────────────────────────────────────────

def test_finds_only_known_names():
    """用 users 表的人名做匹配，不是 NER —— 表里没有的词不该被当人名。"""
    got = find_mentioned_names("汪海波和齐子谦在争，张三没说话", ["汪海波", "齐子谦", "张三"])
    assert set(got) == {"汪海波", "齐子谦", "张三"}


def test_longer_name_wins_over_substring():
    """「张三丰」命中后不该再报「张三」—— 否则假阳性满天飞。"""
    got = find_mentioned_names("张三丰提出了方案", ["张三", "张三丰"])
    assert got == ["张三丰"]


def test_name_in_material_is_supported():
    out = name_hallucination(
        answer="汪海波认为是下发时机问题",
        material="[汪海波] 我觉得是下发时机",
        known_names=["汪海波", "齐子谦"],
    )
    assert out["supported"] is True
    assert out["unsupported"] == []


def test_invented_name_is_flagged():
    """这是 #4 那个 bug 的形状：回答里的人材料里根本没有。"""
    out = name_hallucination(
        answer="齐子谦认为轿厢是封闭环境",
        material="[汪海波] 我觉得是下发时机",
        known_names=["汪海波", "齐子谦"],
    )
    assert out["unsupported"] == ["齐子谦"]


def test_empty_material_is_not_counted_as_pass():
    """材料都没有就无从核对 —— 不能算「通过」，否则比率会被稀释得好看。"""
    out = name_hallucination("齐子谦说……", "", ["齐子谦"])
    assert out["supported"] is False


# ── S2 引用 ─────────────────────────────────────────────────

def test_citations_are_deduped_and_ordered():
    assert cited_ticket_ids("见 #879 和 #640，另外 #879 也提过") == ["879", "640"]


def test_missing_citation_is_flagged():
    out = check_citations("参考 #879 和 #99999", existing_ids=["879", "640"])
    assert out["cited"] == ["879", "99999"]
    assert out["missing"] == ["99999"]


def test_no_citation_is_not_a_failure():
    assert check_citations("没有引用任何工单", existing_ids=[])["missing"] == []


# ── S3 放弃 ─────────────────────────────────────────────────

def test_giveup_detected():
    assert is_giveup("证据不足，暂时无法确认根因")["giveup"] is True


def test_asking_for_more_info_is_not_giveup():
    """「需要你补充现场信息」是产品设计（澄清闭环），不是放弃 —— 别误报。"""
    assert is_giveup("请补充一下故障发生时间，我再判断")["giveup"] is False


# ── 汇总与比对 ──────────────────────────────────────────────

def test_aggregate_denominators_are_honest():
    """分母要写清：没提到人名的用例不进 S1 分母，否则「没提」被算成「提得对」。"""
    rows = [
        {"name_supported": True, "names_unsupported": ["X"], "cited": [], "giveup": False},
        {"name_supported": True, "names_unsupported": [], "cited": ["1"], "citations_missing": [],
         "giveup": True},
        {"name_supported": False, "names_unsupported": [], "cited": [], "giveup": False},
    ]
    agg = aggregate(rows)
    assert agg["cases"] == 3
    assert agg["name_checked"] == 2, "材料缺失那条不进分母"
    assert agg["name_bad"] == 1
    assert agg["cited_checked"] == 1
    assert agg["giveup"] == 1


def test_compare_only_flags_regressions():
    base = {"name_bad": 1, "cited_bad": 0, "giveup": 2}
    assert compare(base, {"name_bad": 1, "cited_bad": 0, "giveup": 1})["regressions"] == []
    reg = compare(base, {"name_bad": 3, "cited_bad": 0, "giveup": 1})["regressions"]
    assert reg == [{"metric": "name_bad", "baseline": 1, "current": 3}]


def test_compare_without_baseline_is_not_a_failure():
    assert compare(None, {"name_bad": 5})["has_baseline"] is False


def test_score_case_end_to_end():
    row = score_case(
        {
            "case_id": "c1",
            "answer": "汪海波和齐子谦的分歧在于 #99999",
            "material": "[汪海波] 我说的是下发时机",
        },
        known_names=["汪海波", "齐子谦"],
        existing_ticket_ids=["879"],
    )
    assert row["names_unsupported"] == ["齐子谦"]
    assert row["citations_missing"] == ["99999"]


# ── 人名表清洗（实测踩到的：users 表里有 `...` 和 `管理员`）──────

def test_role_words_are_not_names():
    """「管理员」是角色词：回答里说「需管理员重启」不该被当成凭空出现的人名。"""
    assert is_plausible_person_name("管理员") is False
    assert is_plausible_person_name("系统") is False
    assert is_plausible_person_name("admin") is False


def test_junk_values_are_not_names():
    """users 表里真的出现过名字是 `...` 的用户 —— 不清洗会大面积误报。"""
    assert is_plausible_person_name("...") is False
    assert is_plausible_person_name("1") is False
    assert is_plausible_person_name("") is False
    assert is_plausible_person_name("A/B") is False


def test_real_names_still_pass():
    assert is_plausible_person_name("汪海波") is True
    assert is_plausible_person_name("Mary-Jane") is True
    assert is_plausible_person_name("阿依古丽·买买提") is True


def test_role_word_in_answer_is_not_flagged():
    """端到端：回答里出现「管理员」不该报人名幻觉。"""
    out = name_hallucination(
        answer="需管理员重启算法计算服务",
        material="[汪海波] 地图发布卡住了",
        known_names=["管理员", "汪海波"],
    )
    assert out["unsupported"] == []
