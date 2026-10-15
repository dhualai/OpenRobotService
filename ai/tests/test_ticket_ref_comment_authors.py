"""@# 引用工单：评论作者名必须还原成真实姓名。

回归背景：TaskComment 模型没有 created_by_name 列（后端 API 的该字段是派生值），
原实现 `getattr(c, "created_by_name", "")` 恒为空，每条评论都渲染成 【】正文，
LLM 分不清哪句话是谁说的；而同一函数上面的「提单人/接单人」已用 users 映射解析。

注：本文件用合成占位 ID/姓名，不引用任何真实用户数据。
"""
from ai.agents.AiDiagnosisPlatform.pipeline import _format_recent_comments


class _C:
    """替身：只需要 created_by / content 两个属性。"""

    def __init__(self, created_by, content="正文"):
        self.created_by = created_by
        self.content = content


def test_author_resolved_via_user_map():
    """微信用户必须还原成真实姓名，而不是 wechat_{openid[:10]} 或空串。"""
    lines = _format_recent_comments(
        [_C("wechat_AbCdEfGhIj", "小屏要不要优化？")],
        {"wechat_AbCdEfGhIj": "张三"},
    )
    assert lines == ["【张三】小屏要不要优化？"]


def test_internal_user_resolved_too():
    lines = _format_recent_comments([_C("admin", "问")], {"admin": "管理员"})
    assert lines == ["【管理员】问"]


def test_unknown_author_keeps_raw_id():
    """映射里没有的作者不得被吞掉，保留原始 ID 便于排查。"""
    lines = _format_recent_comments([_C("wechat_unknown", "hi")], {"x": "y"})
    assert lines == ["【wechat_unknown】hi"]


def test_missing_author_uses_placeholder():
    """没有作者时占位，避免渲染成 【】 这种空标签。"""
    assert _format_recent_comments([_C(None, "x")], {}) == ["【?】x"]
    assert _format_recent_comments([_C("", "x")], {}) == ["【?】x"]


def test_keeps_ten_newest_then_reverses_to_chronological():
    """入参是 ORM 的「新→旧」顺序：取最近 10 条后反转成正序。"""
    comments = [_C("a", f"m{i}") for i in range(12)]
    lines = _format_recent_comments(comments, {"a": "甲"})
    assert len(lines) == 10
    assert lines[0] == "【甲】m9"     # 保留的 10 条里最旧的一条
    assert lines[-1] == "【甲】m0"    # 最新的排在最后


def test_blank_body_skipped():
    lines = _format_recent_comments([_C("a", "   "), _C("a", "有内容")], {"a": "甲"})
    assert lines == ["【甲】有内容"]


def test_body_truncated_to_body_chars():
    lines = _format_recent_comments([_C("a", "字" * 500)], {"a": "甲"}, body_chars=200)
    assert lines == ["【甲】" + "字" * 200]


def test_none_comments_returns_empty():
    assert _format_recent_comments(None, {"a": "甲"}) == []


def test_none_umap_keeps_raw_id():
    """用户映射加载失败时降级为原始 ID，不能抛异常拖垮 @# 引用。"""
    assert _format_recent_comments([_C("wechat_x", "b")], None) == ["【wechat_x】b"]
