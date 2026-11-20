"""讨论史作者名还原：created_by 是 users.id/username，必须还原成真实姓名。

回归背景：TaskComment 模型没有 created_by_name 列（后端 API 的该字段是派生值），
AI 侧原先直接取 created_by，导致微信用户被渲染成 wechat_{openid[:10]} 这种原始
ID（讨论区 UI 显示的是真实姓名），模型只能靠正文猜人名，猜不出就被自评判为幻构。

另一条规矩（**未实名账号加保护**）：映射只能给出 `wechat_…` 形式的 username 时，
一律写成「（微信用户N）」，同人同号、不同人不同号 —— 不写 openid（那是指纹），
也不编名字（会与真人重名）。口径与 issues.md #21 一致。

注：本文件用合成占位 ID/姓名，不引用任何真实用户数据。
"""
import sys

import pytest

from ai.agents.AiTaskPlatform.contexts import comments as _comments
from ai.agents.AiTaskPlatform.contexts.comments import (
    _author_display_map,
    _rows_from_comments,
    render_comment_lines,
)


class _C:
    """替身：只需要 created_by / content 两个属性。"""

    def __init__(self, created_by, content="正文"):
        self.created_by = created_by
        self.content = content


@pytest.fixture(autouse=True)
def _reset_wx_placeholders():
    """代称表是进程级的，测试之间要清干净（否则编号随执行顺序变）。"""
    _comments._WX_PLACEHOLDERS.clear()
    yield
    _comments._WX_PLACEHOLDERS.clear()


def test_wechat_author_resolved_to_real_name():
    rows = _rows_from_comments(
        [_C("wechat_AbCdEfGhIj", "小屏页面是否需要适配？")],
        {"wechat_AbCdEfGhIj": "张三"},
    )
    assert rows[0]["author"] == "张三"


def test_render_uses_resolved_name():
    rows = _rows_from_comments(
        [_C("wechat_AbCdEfGhIj", "小屏页面是否需要适配？")],
        {"wechat_AbCdEfGhIj": "张三"},
    )
    assert render_comment_lines(rows) == ["[张三] 小屏页面是否需要适配？"]


def test_internal_user_resolved_too():
    """非微信用户（created_by=admin）同样要吃映射。"""
    rows = _rows_from_comments([_C("admin", "问")], {"admin": "管理员"})
    assert rows[0]["author"] == "管理员"


def test_unknown_author_keeps_raw_id():
    """映射里没有的作者不得被吞掉，保留原始 ID 便于排查。"""
    rows = _rows_from_comments(
        [_C("wechat_unknown", "hi")], {"wechat_AbCdEfGhIj": "张三"}
    )
    assert rows[0]["author"] == "wechat_unknown"


def test_u_teacher_author_preserved():
    """U老师不在用户表里，须原样保留（render_comment_lines 会特判它）。"""
    rows = _rows_from_comments([_C("U老师", "答")], {"admin": "管理员"})
    assert rows[0]["author"] == "U老师"
    assert render_comment_lines(rows) == ["[U老师] 答"]


def test_missing_created_by_falls_back_to_placeholder():
    rows = _rows_from_comments([_C(None, "x")], {})
    assert rows[0]["author"] == "?"


def test_empty_and_none_name_map_keep_raw():
    assert _rows_from_comments([_C("wechat_x")], {})[0]["author"] == "wechat_x"
    assert _rows_from_comments([_C("wechat_x")], None)[0]["author"] == "wechat_x"


def test_empty_comments_returns_empty():
    assert _rows_from_comments([], {"a": "b"}) == []
    assert _rows_from_comments(None, {}) == []


def test_author_display_map_degrades_on_import_failure(monkeypatch):
    """映射加载失败必须降级为空 dict，不能把讨论史读取一起搞挂。"""
    monkeypatch.setitem(sys.modules, "app.services.user_service", None)
    assert _author_display_map() == {}


# ── 未实名账号的保护（issues.md #21 口径）─────────────────────

def test_unverified_wechat_account_hides_openid():
    """映射命中但只给得出 wechat_ 形式的 username（= 未实名）→ 代称，绝不露 openid。"""
    rows = _rows_from_comments(
        [_C("wechat_oD5oY3abc", "未实名的发言")],
        {"wechat_oD5oY3abc": "wechat_oD5oY3abc"},   # 映射的取值规则：name or username
    )
    assert rows[0]["author"] == "（微信用户1）"
    assert "wechat_" not in rows[0]["author"]
    assert "oD5oY3abc" not in rows[0]["author"]


def test_unverified_same_person_same_number():
    """同人同号：同一个账号出现多次，编号不变。"""
    m = {"wechat_AAA111": "wechat_AAA111"}
    rows = _rows_from_comments([_C("wechat_AAA111"), _C("wechat_AAA111")], m)
    assert rows[0]["author"] == rows[1]["author"] == "（微信用户1）"
    assert len(_comments._WX_PLACEHOLDERS) == 1


def test_unverified_different_people_different_numbers():
    """不同人不同号。"""
    m = {"wechat_AAA111": "wechat_AAA111", "wechat_BBB222": "wechat_BBB222"}
    rows = _rows_from_comments([_C("wechat_AAA111"), _C("wechat_BBB222")], m)
    assert rows[0]["author"] == "（微信用户1）"
    assert rows[1]["author"] == "（微信用户2）"


def test_unverified_matches_username_and_bare_openid_as_same_person():
    """同一个人的两种写法（username 截断 vs 裸 openid）应拿到同一个号。

    注意 username 是 `wechat_{openid[:10]}`——前缀后**正好 10 位**，
    按前 10 位认人这条规则才成立。
    """
    full = "oD5oY3abcX" + "YZ123456789"     # 裸 openid（users.id 形式）
    short = "wechat_oD5oY3abcX"             # username 形式（openid 前 10 位）
    assert len(short) - len("wechat_") == 10
    m = {full: full, short: short}
    rows = _rows_from_comments([_C(full), _C(short)], m)
    assert rows[0]["author"] == rows[1]["author"] == "（微信用户1）"


def test_named_wechat_account_still_shows_real_name():
    """已实名的微信账号不受影响 —— 保护只针对「给不出真名」的。"""
    rows = _rows_from_comments(
        [_C("wechat_AbCdEfGhIj", "已实名")], {"wechat_AbCdEfGhIj": "张三"}
    )
    assert rows[0]["author"] == "张三"


def test_unverified_protection_applies_in_rendered_lines():
    """渲染层是模型实际看到的东西，端到端确认没有指纹漏出去。"""
    rows = _rows_from_comments(
        [_C("wechat_oD5oY3abc", "一句话")], {"wechat_oD5oY3abc": "wechat_oD5oY3abc"}
    )
    assert render_comment_lines(rows) == ["[（微信用户1）] 一句话"]


def test_unverified_bare_openid_also_protected():
    """未实名的另一种形态：映射回落到**裸 openid**（users.id 形式）。

    映射取值是 `name or username or user_id`，username 也为空时值就等于 user_id，
    而 users.id 本身可以是裸 openid —— 它不以 `wechat_` 开头，容易漏挡。
    """
    bare = "oD5oY3abcX" + "YZ123456789"
    rows = _rows_from_comments([_C(bare, "发言")], {bare: bare})
    assert rows[0]["author"] == "（微信用户1）"
    assert "oD5oY3" not in rows[0]["author"]


def test_empty_map_is_loud(monkeypatch, caplog):
    """整片映射拿不到时必须显形（ERROR 级）。

    这是「有名字的必须一直能读到」那条要求的告警线：映射一旦整片失效，
    所有作者（包括有名字的）都会退化成原始 ID，模型就又开始猜人。
    只留一行 warning 等于没人会看见。
    """
    import logging as _logging
    import types

    fake = types.ModuleType("app.services.user_service")

    class _Svc:
        @staticmethod
        def get_user_map():
            return {}

    fake.user_service = _Svc()
    monkeypatch.setitem(sys.modules, "app.services.user_service", fake)

    with caplog.at_level(_logging.ERROR):
        assert _author_display_map() == {}

    assert any("姓名映射为空" in str(r.message) for r in caplog.records), \
        "空映射必须打 ERROR，否则整片退化不可见"
