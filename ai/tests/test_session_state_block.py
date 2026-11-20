# -*- coding: utf-8 -*-
"""0901 会话全局状态块单测（主 LLM 全局视角）。

背景：0831 生产 PDA 误判——提交动作不写 turns（submit 形态 message 留空、
弹窗路径前端事件），LLM 从对话文本考古不出「已提交」，ticket_intent 的
OR 子句把提单痕迹历史读成流程延续。状态块每轮从 AgentState 渲染事实锚点。
锁定：三行基线、完结语义、收集中/草稿两形态、项目三态、闭环话术收编、
不泄漏旧单单号。
"""
import time

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AgentState,
    _session_state_block,
)


class _Mem:
    def __init__(self, metadata=None):
        self.metadata = metadata or {}


def _st(**kw):
    d = dict(session_id="s", phase="idle", problem_summary="")
    d.update(kw)
    return AgentState(**d)


def test_全新会话三行基线():
    block = _session_state_block(_st(), _Mem())
    assert "【上一张工单】（无）" in block
    assert "当前不在提单流程中" in block
    assert "本单未确定" in block


def test_上单已提交_完结语义():
    st = _st(last_submitted_ticket={
        "ticket_id": "TK-9", "db_id": 9, "submitted_at": time.time()})
    block = _session_state_block(st, _Mem())
    assert "【上一张工单】已提交" in block
    assert "已完结归档" in block
    assert "不是该单的延续" in block
    # 不透露单号/项目/主题（防 flash 挖旧单内容写回 state_update、抠号当引用）
    assert "TK-9" not in block


def test_收集中与草稿两形态():
    st = _st(ticket_collecting=["联系方式", "故障现象"])
    block = _session_state_block(st, _Mem())
    assert "提单进行中：字段收集中" in block
    assert "联系方式" in block and "故障现象" in block

    mem2 = _Mem({"ticket_draft": {"title": "x"}})
    block2 = _session_state_block(_st(), mem2)
    assert "草稿已生成" in block2
    assert "尚未提交" in block2


def test_项目三态():
    assert "已确定" in _session_state_block(
        _st(mentioned_project={"name": "印尼三宝垄物流园叉车搬运项目"}), _Mem())
    assert "待确认" in _session_state_block(
        _st(ambiguous_project_candidates=[{"name": "a"}, {"name": "b"}]), _Mem())
    assert "本单未确定" in _session_state_block(_st(), _Mem())


def test_闭环拦截话术收编():
    # 刚提交 + 无新问题 → 注入固定话术（原 last_ticket_context 强版本）
    st = _st(last_submitted_ticket={"ticket_id": "TK-9", "db_id": 9})
    block = _session_state_block(st, _Mem())
    assert "刚提交过工单且还没有新问题" in block
    assert "刚放弃或提交过工单" in block  # _block_msg 固定话术
    # 用户又说话了（run_stream 已置 spoke；PDA 轮真实形态）→ 不注入拦截话术，
    # 只靠「已完结归档」事实行
    st2 = _st(problem_summary="牵引车无法充电",
              last_submitted_ticket={"ticket_id": "TK-9", "db_id": 9},
              user_spoke_after_submit=True)
    block2 = _session_state_block(st2, _Mem())
    assert "刚提交过工单且还没有新问题" not in block2
    assert "已完结归档" in block2


def test_收集中不触发闭环拦截():
    # _can_submit 对收集模式放行——状态块不得注入拦截话术误伤补字段轮
    st = _st(ticket_collecting=["联系方式"],
             last_submitted_ticket={"ticket_id": "TK-9", "db_id": 9})
    block = _session_state_block(st, _Mem())
    assert "刚提交过工单且还没有新问题" not in block
