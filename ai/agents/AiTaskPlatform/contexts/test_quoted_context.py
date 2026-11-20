"""「引用一句话再 @U老师」：注入的应是那句**所在的对话**，不是孤零零一句。

回归背景：只注入被引用的那一句时，模型读不到语境，容易答偏
（原事故里 AI 正确写出了两个人，回复却说「没有出现两个人」）。
"""
from ai.agents.AiTaskPlatform.contexts import comments


def _rows():
    return [
        {"id": 3, "author": "汪海波", "content": "是我方下发重定位太早"},
        {"id": 4, "author": "齐子谦", "content": "轿厢是封闭环境，未必是时机问题"},
        {"id": 5, "author": "张三", "content": "那到底谁对？"},
        {"id": 6, "author": "U老师", "content": "需要更多证据"},
    ]


def test_quoted_uses_surrounding_context(monkeypatch):
    monkeypatch.setattr(comments, "load_comment_context", lambda cid, k=5: _rows())
    block = comments.format_quoted_comment_block(
        {"reply_to": 4, "quoted_comment": {"id": 4, "content": "轿厢是封闭环境"}}, k=5
    )
    # 被引用的那条要标出来
    assert "▶ [齐子谦] 轿厢是封闭环境" in block
    # 前后几条都在（这才是「读到具体对话」）
    assert "[汪海波] 是我方下发重定位太早" in block
    assert "[张三] 那到底谁对？" in block
    # 明确告知模型哪条是引用的、其余是语境
    assert "不要当成用户本轮问的内容" in block


def test_falls_back_to_single_comment_when_context_missing(monkeypatch):
    monkeypatch.setattr(comments, "load_comment_context", lambda cid, k=5: [])
    block = comments.format_quoted_comment_block(
        {"reply_to": 4, "quoted_comment": {"id": 4, "author": "齐子谦", "content": "轿厢是封闭环境"}},
        k=5,
    )
    # 回退为原行为：只有那一句
    assert block == "## 用户本轮引用的评论\n[齐子谦] 轿厢是封闭环境\n"


def test_no_id_still_renders_single_comment(monkeypatch):
    monkeypatch.setattr(comments, "load_comment_context",
                        lambda cid, k=5: (_ for _ in ()).throw(AssertionError("不该查库")))
    block = comments.format_quoted_comment_block(
        {"quoted_comment": {"author": "张三", "content": "一句引用"}}
    )
    assert block == "## 用户本轮引用的评论\n[张三] 一句引用\n"


def test_empty_quoted_returns_empty(monkeypatch):
    monkeypatch.setattr(comments, "load_quoted_comment", lambda cid: {})
    assert comments.format_quoted_comment_block({}) == ""
    assert comments.format_quoted_comment_block(None) == ""


def test_k_zero_keeps_context_call_but_window_is_anchor_only(monkeypatch):
    """k=0 时窗口只剩被引用那条 —— 由 load_comment_context 决定，这里锁参数透传。"""
    seen = {}

    def _ctx(cid, k=5):
        seen["k"] = k
        return [{"id": 4, "author": "齐子谦", "content": "只有我"}]

    monkeypatch.setattr(comments, "load_comment_context", _ctx)
    comments.format_quoted_comment_block(
        {"reply_to": 4, "quoted_comment": {"id": 4, "content": "只有我"}}, k=0
    )
    assert seen["k"] == 0
