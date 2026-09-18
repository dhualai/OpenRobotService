# -*- coding: utf-8 -*-
"""0829 项目歧义反问 — 匹配层单测（确定性纯函数，不走 LLM）。

覆盖 _match_project_mention（唯一命中/歧义 None）与
_ambiguous_project_candidates（反问候选列表）的分工：
- 唯一指代 1 个 → _match 命中，无候选
- 唯一指代 ≥2 个（「安吉中力」→ 智芯/富阳/AGV-USP/安吉北区…）→ _match None + 候选
- 无唯一子串但整体命中 2~8 个（「安吉」「中力」2 字模糊词）→ _match None + 候选
- 高频词/大池（叉车/潜伏车/仓储）→ 返回全部命中（>8 由调用侧按票史近度截 4 反问）
"""
from unittest.mock import AsyncMock

import pytest

from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform

# 真实命名规律项目池（含易混组）
_POOL = [
    {"name": "浙江安吉中力智芯仓储物流园叉车搬运项目", "code": "zl2"},
    {"name": "浙江安吉中力富阳工厂内部项目", "code": "zl3"},
    {"name": "浙江安吉AGV-USP出厂测试服务器维护项目", "code": "usp2"},
    {"name": "江苏中力靖江工厂XQE调度升级项目", "code": "zl1"},
    {"name": "浙江湖州中力安吉北区调度升级项目", "code": "zl4"},
    {"name": "河南郑州东昇汽配厂潜伏车项目", "code": "p1"},
    {"name": "河南郑州思念食品潜伏车项目", "code": "p5"},
    {"name": "江苏南京本川XSC仓储项目", "code": "69"},
    {"name": "湖北襄阳2025年629机器人跳舞演示项目", "code": "xy1"},
    {"name": "湖北襄阳2025年双11机器人跳舞演示项目", "code": "xy2"},
    {"name": "摇人吧服务号", "code": "Leo_test"},
]
# 大池高频词（模拟真实 150+ 项目里叉车/潜伏车/仓储不唯一）
_BIG = _POOL + [
    {"name": f"测试客户{i}叉车搬运项目", "code": f"t{i}"} for i in range(20)
]


def _names(projects):
    return [p["name"] for p in (projects or [])]


def test_唯一命中():
    got = AiDiagnosisPlatform._match_project_mention("东昇", _POOL)
    assert got and got["name"] == "河南郑州东昇汽配厂潜伏车项目"
    assert AiDiagnosisPlatform._ambiguous_project_candidates("东昇", _POOL) == []


def test_带修饰原话唯一命中():
    got = AiDiagnosisPlatform._match_project_mention("河南东昇那个潜伏车项目", _POOL)
    assert got and got["name"] == "河南郑州东昇汽配厂潜伏车项目"


def test_平台简称唯一命中():
    got = AiDiagnosisPlatform._match_project_mention("服务号", _POOL)
    assert got and got["name"] == "摇人吧服务号"


def test_精确code命中():
    got = AiDiagnosisPlatform._match_project_mention("69", _POOL)
    assert got and got["code"] == "69"


def test_安吉模糊词整体命中多个():
    # 「安吉」只有 2 字，子串枚举抠不出更小唯一词 → 整体命中 4 个项目 → 反问候选
    got = AiDiagnosisPlatform._match_project_mention("安吉", _POOL)
    assert got is None  # 不误收
    cands = AiDiagnosisPlatform._ambiguous_project_candidates("安吉", _POOL)
    assert len(cands) == 4
    assert "浙江安吉中力智芯仓储物流园叉车搬运项目" in _names(cands)
    assert "浙江安吉AGV-USP出厂测试服务器维护项目" in _names(cands)


def test_多唯一指代同时存在():
    # 「安吉中力」唯一指代到智芯+富阳+AGV-USP+安吉北区+中力靖江 → 歧义候选
    got = AiDiagnosisPlatform._match_project_mention("安吉中力", _POOL)
    assert got is None
    cands = AiDiagnosisPlatform._ambiguous_project_candidates("安吉中力", _POOL)
    assert len(cands) >= 2


def test_唯一标识不受模糊词干扰():
    got = AiDiagnosisPlatform._match_project_mention("中力智芯", _POOL)
    assert got and got["name"] == "浙江安吉中力智芯仓储物流园叉车搬运项目"
    assert AiDiagnosisPlatform._ambiguous_project_candidates("中力智芯", _POOL) == []


def test_襄阳629唯一命中():
    got = AiDiagnosisPlatform._match_project_mention("襄阳629项目", _POOL)
    assert got and got["code"] == "xy1"


def test_高频词大池超限返回供截断():
    # 真实池 20+ 叉车项目 → 0829 起不再静默拒收：返回全部命中，
    # 由 _cap_ambiguous_candidates 按票史近度截 4 反问（印尼实锤拍板）
    got = AiDiagnosisPlatform._match_project_mention("叉车", _BIG)
    assert got is None
    cands = AiDiagnosisPlatform._ambiguous_project_candidates("叉车", _BIG)
    assert len(cands) > 8
    capped = AiDiagnosisPlatform._cap_ambiguous_candidates(cands, [])
    assert len(capped) == 5


def test_cap_票史近度优先():
    cands = [{"name": n} for n in
             ["甲叉车", "乙叉车", "丙叉车", "丁叉车", "戊叉车", "己叉车",
              "庚叉车", "辛叉车", "壬叉车", " tenth叉车"]]
    # 票史最近的排最前；不在票史的保持原序排后；>5 截前 5（0829 定稿统一规则）
    capped = AiDiagnosisPlatform._cap_ambiguous_candidates(
        cands, ["壬叉车", "戊叉车"])
    assert [c["name"] for c in capped] == [
        "壬叉车", "戊叉车", "甲叉车", "乙叉车", "丙叉车"]
    # 无票史信号 → 原序取前 5
    capped0 = AiDiagnosisPlatform._cap_ambiguous_candidates(cands, [])
    assert [c["name"] for c in capped0] == [
        "甲叉车", "乙叉车", "丙叉车", "丁叉车", "戊叉车"]
    # ≤5 候选：排序照做（票史最近的排前面）但不截
    small = cands[:4]
    capped_s = AiDiagnosisPlatform._cap_ambiguous_candidates(small, ["丁叉车"])
    assert [c["name"] for c in capped_s] == ["丁叉车", "甲叉车", "乙叉车", "丙叉车"]


def test_泛词不污染具体词候选():
    # 整句原话：泛词「项目」命中全池，具体词「印尼」只命中 2 个 → 取最小命中集
    pool = [
        {"name": "印尼三宝垄物流园叉车搬运项目", "code": "yd1"},
        {"name": "印尼雅加达仓储项目", "code": "yd2"},
    ] + [{"name": f"测试客户{i}叉车搬运项目", "code": f"t{i}"} for i in range(10)]
    cands = AiDiagnosisPlatform._ambiguous_project_candidates(
        "不是这个项目，是印尼的", pool)
    assert len(cands) == 2
    assert all("印尼" in c["name"] for c in cands)


def test_无标识泛指反问最近():
    # 「那个项目」只含泛词「项目」（命中全池 11）→ 不再静默拒收，
    # 返回全部命中（调用侧截 4 反问最近的）
    got = AiDiagnosisPlatform._match_project_mention("那个项目", _POOL)
    assert got is None
    cands = AiDiagnosisPlatform._ambiguous_project_candidates("那个项目", _POOL)
    assert len(cands) == 10  # 池 11 个里 10 个名含「项目」（服务号除外）


def test_空输入():
    assert AiDiagnosisPlatform._match_project_mention("", _POOL) is None
    assert AiDiagnosisPlatform._match_project_mention(" ", _POOL) is None
    assert AiDiagnosisPlatform._ambiguous_project_candidates("", _POOL) == []


# ---- 收集轮项目捕捉（0829 印尼实锤：收集轮跳过规划器，服务端直接判定）----

_YDN_POOL = [
    {"name": "摇人吧服务号", "code": "Leo_test"},
    {"name": "印尼三宝垄物流园叉车搬运项目", "code": "yd1"},
    {"name": "印尼雅加达仓储项目", "code": "yd2"},
]
_AMB2 = _YDN_POOL[1:]


def test_防线_歧义挂起期间臆断拒收():
    # 用户说「印尼的项目」原话匹配 2 个候选，LLM 挑三宝垄照抄 = 臆断
    p = AiDiagnosisPlatform.__new__(AiDiagnosisPlatform)
    assert p._choice_supported_by_amb(
        "印尼的项目叉车出故障了，帮我提个单", _AMB2[0], _AMB2) is False


def test_防线_原话唯一命中放行():
    # 确认词（「就三宝垄那个」）的提升由收集轮捕捉/规划器确认通道在
    # 防线之前完成（命中即清挂起）——防线遇挂起非空只剩臆断，不放行。
    p = AiDiagnosisPlatform.__new__(AiDiagnosisPlatform)
    assert p._choice_supported_by_amb("就三宝垄那个", _AMB2[0], _AMB2) is False
    assert p._choice_supported_by_amb("雅加达那个", _AMB2[0], _AMB2) is False
    # ⚠️ 设备词支撑陷阱：原话「叉车」恰好唯一命中三宝垄，也不是放行理由
    assert p._choice_supported_by_amb(
        "印尼的项目叉车出故障了", _AMB2[0], _AMB2) is False


def test_防线_序号应答放行():
    p = AiDiagnosisPlatform.__new__(AiDiagnosisPlatform)
    for q in ("1", "2号", "第一个", "就1", "第2个"):
        assert p._choice_supported_by_amb(q, _AMB2[0], _AMB2) is True
    # 序号应答限短句：长句不适用
    assert p._choice_supported_by_amb(
        "第一个吧，另外叉车是昨天坏的，联系人张三", _AMB2[0], _AMB2) is False


def test_防线_无挂起放行():
    p = AiDiagnosisPlatform.__new__(AiDiagnosisPlatform)
    assert p._choice_supported_by_amb("随便什么", _AMB2[0], []) is True


def _mk_request(query, sid="s-col"):
    from ai.agents.AiDiagnosisPlatform.pipeline import DiagnosisRequest
    return DiagnosisRequest(session_id=sid, query=query,
                            created_by="tester", skip_retrieval=False)


def _mk_state(**kw):
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState
    d = dict(session_id="s-col", phase="diagnosing",
             problem_summary="测试", ticket_collecting=["联系方式"],
             required_fields={"contact": "联系方式"})
    d.update(kw)
    return AgentState(**d)


@pytest.mark.asyncio
async def test_收集轮歧义词挂起():
    p = AiDiagnosisPlatform.__new__(AiDiagnosisPlatform)
    p._get_user_projects = AsyncMock(return_value=_YDN_POOL)
    p._get_recent_ticket_projects = AsyncMock(return_value=[])
    st = _mk_state()
    await p._collect_round_project_capture(_mk_request("不是这些，是印尼的"), st)
    assert st.mentioned_project is None
    assert len(st.ambiguous_project_candidates) == 2
    assert all("印尼" in c["name"] for c in st.ambiguous_project_candidates)


@pytest.mark.asyncio
async def test_收集轮挂起后确认提升():
    p = AiDiagnosisPlatform.__new__(AiDiagnosisPlatform)
    p._get_user_projects = AsyncMock(return_value=_YDN_POOL)
    st = _mk_state(ambiguous_project_candidates=[
        {"name": "印尼三宝垄物流园叉车搬运项目", "code": "yd1"},
        {"name": "印尼雅加达仓储项目", "code": "yd2"}])
    await p._collect_round_project_capture(_mk_request("就三宝垄那个"), st)
    assert st.mentioned_project["code"] == "yd1"
    assert st.ambiguous_project_candidates == []


@pytest.mark.asyncio
async def test_收集轮已确认不再捕捉():
    p = AiDiagnosisPlatform.__new__(AiDiagnosisPlatform)
    p._get_user_projects = AsyncMock(return_value=_YDN_POOL)
    st = _mk_state(mentioned_project={"name": "摇人吧服务号", "code": "Leo_test"})
    await p._collect_round_project_capture(_mk_request("印尼的"), st)
    assert st.mentioned_project["code"] == "Leo_test"  # 不覆盖
    assert st.ambiguous_project_candidates == []


@pytest.mark.asyncio
async def test_非收集轮不捕捉():
    p = AiDiagnosisPlatform.__new__(AiDiagnosisPlatform)
    p._get_user_projects = AsyncMock(return_value=_YDN_POOL)
    st = _mk_state(ticket_collecting=[])
    await p._collect_round_project_capture(_mk_request("印尼的项目有问题"), st)
    assert st.mentioned_project is None
    assert st.ambiguous_project_candidates == []
    p._get_user_projects.assert_not_called()
