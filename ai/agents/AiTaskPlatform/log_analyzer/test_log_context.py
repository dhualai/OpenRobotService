"""日志分析：工单上下文整段放入；查询回灌未到窗口用原文。"""
import asyncio
from pathlib import Path

from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import (
    LogSubAgent,
    _build_context,
    _feedback_to_llm,
    _release_older_log_feedback,
)


def test_ticket_context_keeps_full_description_and_discussion():
    desc = "车停在路径规划中。" + "现场补充" * 80
    text = _build_context({
        "title": "单车取货后卡规划",
        "description": desc,
        "discussion": "[张三] 调度没有下发\n[U老师] 先看规划是否一直重试",
        "attachment_summaries": ["对话记录.md: 车号 A1"],
        "fault_code": "E100",
    }, "为什么一直规划中")
    assert desc in text
    assert "[张三] 调度没有下发" in text
    assert "对话记录.md: 车号 A1" in text
    assert "E100" in text
    assert "为什么一直规划中" in text


def test_ticket_context_keeps_site_vehicle_and_dispatch():
    text = _build_context({
        "title": "单车取货后卡规划",
        "description": "车停在路径规划中",
        "project_facts": "【车型与载具】\n- 型号: XQE-6\n\n【调度软件】\n- 调度版本: USP 3.2.1",
    }, "为什么一直规划中")
    assert "XQE-6" in text
    assert "USP 3.2.1" in text
    assert "现场档案（车型与调度版本）" in text


def test_parse_structured_log_steps():
    from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import _parse_llm_command
    anchor = _parse_llm_command(
        '{"action":"anchor","phenomenon":"路径规划中","time_start":"2026-08-11 10:58","robot":"A1"}'
    )
    hypo = _parse_llm_command('{"action":"hypothesis","hypothesis":"求解一直失败"}')
    contrast = _parse_llm_command(
        '{"action":"contrast","analysis":"对照","query":{"time_start":"2026-08-11 10:50","time_end":"2026-08-11 11:00"}}'
    )
    attr = _parse_llm_command('{"action":"attribute","module":"TMS","because":"本文件没有新任务","need_feed":"TMS"}')
    assert anchor["action"] == "anchor"
    assert hypo["hypothesis"] == "求解一直失败"
    assert contrast["action"] == "contrast"
    assert attr["need_feed"] == "TMS"


class _ScriptedLLM:
    """按顺序回命令。不连真实模型。"""

    def __init__(self, replies):
        self._replies = list(replies)
        self.user_turns = []

    async def chat(self, messages, max_tokens=300, temperature=0.0):
        for message in reversed(messages):
            if message.get("role") == "user":
                self.user_turns.append(message.get("content") or "")
                break
        if not self._replies:
            return '{"action":"conclude","conclusion":"结束","confidence":0.2,"evidence_lines":[]}'
        return self._replies.pop(0)


def test_bookkeeping_steps_do_not_scan_until_a_real_query(tmp_path: Path):
    log = tmp_path / "robot.log"
    log.write_text(
        "2026-08-11 11:01:44 INFO XNA-169 idle\n"
        "2026-08-11 11:01:45 ERROR 一致性校验失败 lock_id=8842\n",
        encoding="utf-8",
    )
    llm = _ScriptedLLM([
        '{"action":"anchor","phenomenon":"一致性校验失败","time_start":"2026-08-11 11:01","robot":"XNA-169"}',
        '{"action":"shell","analysis":"过早取证","cmd":"grep -n ERROR | head -n 5"}',
        '{"action":"contrast","analysis":"还没给查询"}',
        '{"action":"hypothesis","hypothesis":"锁号 8842 导致校验失败"}',
        '{"action":"contrast","analysis":"对照故障分钟","query":{"time_start":"2026-08-11 11:01","time_end":"2026-08-11 11:02","robot_filter":"","keyword_filter":"一致性","error_only":true,"max_results":10}}',
        '{"action":"attribute","module":"TMS","because":"本文件只有校验失败","need_feed":"TMS"}',
        '{"action":"conclude","conclusion":"校验失败但缺 TMS","confidence":0.4,"need_feed":"TMS","evidence_lines":[]}',
    ])
    agent = LogSubAgent(str(log), source_name="robot.log")
    agent._llm = llm
    steps = []
    result = asyncio.run(agent.analyze(
        {"title": "校验失败", "description": "11:01 左右报一致性校验失败", "project_facts": "【车型与载具】\n- 型号: XQE-6"},
        user_question="看一下一致性校验",
        progress=steps.append,
    ))
    joined = "\n".join(llm.user_turns)
    assert "已记录锚定" in joined
    assert "不要在查询之前 shell" in joined
    assert "contrast 必须带 query" in joined
    assert result.queries_made == 1
    assert result.anchor["phenomenon"] == "一致性校验失败"
    assert result.step_hypotheses[0]["hypothesis"] == "锁号 8842 导致校验失败"
    assert result.attributions[0]["need_feed"] == "TMS"
    assert "校验失败但缺 TMS" in result.conclusion
    assert any("对照" in (item.get("description") or "") for item in steps)
    assert not any(str(item.get("description") or "").startswith("日志 shell") for item in steps)


def test_log_feedback_stays_raw_under_window():
    raw = "\n".join(f"* L{i}| ERR=规划失败 ts=10:58:{i:02d}" for i in range(30))
    text = _feedback_to_llm(raw, matched=30, room_tokens=100_000)
    assert "ERR=规划失败 ts=10:58:00" in text
    assert "ERR=规划失败 ts=10:58:29" in text
    assert "压成高频" not in text


def test_log_feedback_compresses_only_when_over_room():
    raw = "\n".join(f"* L{i}| ERR=规划失败 车A1" for i in range(40))
    text = _feedback_to_llm(raw, matched=40, room_tokens=200)
    assert "超过窗口" in text
    assert "规划失败" in text


def test_older_query_feedback_yields_when_window_full(monkeypatch):
    monkeypatch.setenv("DISCUSS_CONTEXT_WINDOW_TOKENS", "20000")
    monkeypatch.setenv("DISCUSS_CONTEXT_RESERVE_TOKENS", "1000")
    messages = [
        {"role": "user", "content": "查询结果(命中3行)\n" + "旧" * 25000},
        {"role": "user", "content": "查询结果(命中1行)\n最近这条要留下"},
    ]
    _release_older_log_feedback(messages)
    assert messages[0]["content"].startswith("此前查询结果已让出窗口")
    assert "最近这条要留下" in messages[1]["content"]
