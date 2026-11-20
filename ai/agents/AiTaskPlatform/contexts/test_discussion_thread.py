"""一张工单的讨论史：按时间保留，过长时留开头和最近对话。"""
from ai.agents.AiTaskPlatform.contexts.comments import format_discussion_thread


def test_thread_keeps_order_and_full_reply():
    text = format_discussion_thread([
        {"author": "张三", "content": "车在折返"},
        {"author": "U老师", "content": "先看调度是否来回切换。" + "证" * 300},
        {"created_by_name": "李四", "content": "调度确实在切"},
    ])
    assert text.index("[张三]") < text.index("[U老师]") < text.index("[李四]")
    assert "证" * 300 in text
    assert "AI历史分析" not in text


def test_thread_keeps_head_and_tail_when_long():
    comments = [{"author": "开单", "content": "开头现象"}]
    comments += [{"author": f"u{i}", "content": f"第{i}条 " + "x" * 80} for i in range(30)]
    text = format_discussion_thread(comments, per_comment=100, total=500)
    assert text.startswith("[开单] 开头现象")
    assert "省略" in text
    assert "[u29]" in text
