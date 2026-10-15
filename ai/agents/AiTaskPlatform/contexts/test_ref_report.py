"""@# 引用工单：注入「帮我分析」报告快照，拿不到时给 LLM 明确口径。

回归背景：diagnose 的报告**不落库**，原实现只拿得到库里的字段 + 讨论评论，
用户问"他上次那份分析报告说了啥"时 LLM 只能编。现在 diagnose 会留一份进程内快照
（runtime/report_cache），@# 引用同进程内的工单就能把报告原文注入；没快照时
明确要求如实说明"报告不落库、只能重新点一次『帮我分析』"。

注：本文件用合成工单号/文本，不引用任何真实数据，也不连库。
"""
from ai.agents.AiTaskPlatform.contexts import contexts
from ai.agents.AiTaskPlatform.contexts import comments
from ai.agents.AiTaskPlatform.runtime import report_cache
from ai.agents.AiTaskPlatform.schemas import TaskContext


def _patch(monkeypatch, ctx: TaskContext, discussion: str = ""):
    monkeypatch.setattr(contexts, "load_referenced_task_context", lambda rid: ctx)
    monkeypatch.setattr(comments, "load_discussion", lambda rid, limit=10: discussion)


def test_report_injected_when_snapshot_exists(monkeypatch):
    report_cache.clear()
    report_cache.put("44123", "# 诊断报告\n根因: 相机标定漂移\n置信度: 0.8")
    _patch(monkeypatch, TaskContext(task_id="44123", title="堆垛机报错"))

    block = contexts.format_referenced_tickets(["44123"])

    assert "根因: 相机标定漂移" in block, "报告原文必须注入"
    assert "未落库" in block, "要标注这是进程内快照、不是库里的"
    assert "拿不到" not in block, "有快照就不该再发『拿不到』口径"


def test_honest_note_when_no_snapshot(monkeypatch):
    report_cache.clear()
    _patch(monkeypatch, TaskContext(task_id="44123", title="堆垛机报错"))

    block = contexts.format_referenced_tickets(["44123"])

    assert "报告不落库" in block and "重新点一次" in block, "拿不到时必须给如实说明的口径"
    assert "不要编造" in block


def test_expired_snapshot_falls_back_to_note(monkeypatch):
    report_cache.clear()
    report_cache.put("44123", "旧报告正文")
    ts, body = report_cache._snapshots["44123"]
    report_cache._snapshots["44123"] = (ts - report_cache.TTL_SECONDS - 1, body)
    _patch(monkeypatch, TaskContext(task_id="44123", title="堆垛机报错"))

    block = contexts.format_referenced_tickets(["44123"])

    assert "旧报告正文" not in block
    assert "报告不落库" in block


def test_long_report_truncated(monkeypatch):
    report_cache.clear()
    report_cache.put("44123", "字" * (contexts.MAX_REF_REPORT_CHARS + 1000))
    _patch(monkeypatch, TaskContext(task_id="44123", title="堆垛机报错"))

    block = contexts.format_referenced_tickets(["44123"])

    assert "已截断" in block
    assert block.count("字") == contexts.MAX_REF_REPORT_CHARS


def test_unreadable_ticket_gets_no_report_section(monkeypatch):
    """整张工单读不到时只留占位，不叠加报告口径（双重噪音）。"""
    report_cache.clear()
    _patch(monkeypatch, TaskContext(task_id="44123"))

    block = contexts.format_referenced_tickets(["44123"])

    assert "未能读取到该工单的上下文" in block
    assert "报告不落库" not in block


def test_each_ticket_gets_its_own_report(monkeypatch):
    report_cache.clear()
    report_cache.put("1", "工单一的报告")
    monkeypatch.setattr(
        contexts, "load_referenced_task_context",
        lambda rid: TaskContext(task_id=rid, title=f"标题{rid}"),
    )
    monkeypatch.setattr(comments, "load_discussion", lambda rid, limit=10: "")

    block = contexts.format_referenced_tickets(["1", "2"])

    first, second = block.split("- 工单 #2")
    assert "工单一的报告" in first
    assert "本进程内也没留快照" not in first, "工单一有快照"
    assert "本进程内也没留快照" in second, "工单二缺快照"
    assert "工单一的报告" not in second, "不许把别家的报告串过来"
