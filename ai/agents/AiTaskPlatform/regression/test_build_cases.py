"""用例抽取的纯函数测试：不连库。

抽取纪律决定用例可不可信 —— 尤其是「材料必须早于提问」那条，
松掉它整个核对就变成抄答案。
"""
from ai.agents.AiTaskPlatform.regression.build_cases import (
    is_system_notice,
    material_before,
    pick_qa_pairs,
)


def _c(created_by, content):
    return {"created_by": created_by, "author": created_by, "content": content}


def _render(rows, per_comment=None):
    return [f"[{r['author']}] {r['content']}" for r in rows]


# ── 系统通知 ────────────────────────────────────────────────

def test_system_notice_filtered():
    assert is_system_notice("工单状态变更为 in_progress") is True
    assert is_system_notice("开始归档工单") is True
    assert is_system_notice("") is True


def test_human_talk_is_not_a_system_notice():
    assert is_system_notice("我觉得是下发时机的问题") is False


# ── 问答配对 ────────────────────────────────────────────────

def test_pair_is_question_then_next_ai_answer():
    comments = [
        _c("张三", "前面这句闲聊"),
        _c("张三", "@U老师 两个人的分歧在哪"),
        _c("U老师", "两人分歧是……"),
        _c("李四", "我补充一点"),
    ]
    assert pick_qa_pairs(comments) == [(1, 2)]


def test_ai_authored_comment_is_not_a_question():
    """U老师 自己发的、正文里带 @U 的（罕见但要防）不该被当成提问。"""
    comments = [_c("U老师", "@U老师 记录一下"), _c("U老师", "好的")]
    assert pick_qa_pairs(comments) == []


def test_question_without_following_answer_is_dropped():
    comments = [_c("张三", "@U老师 在吗？")]
    assert pick_qa_pairs(comments) == []


def test_question_without_at_u_is_not_a_question():
    comments = [_c("张三", "两个人的分歧在哪"), _c("U老师", "……")]
    assert pick_qa_pairs(comments) == []


def test_per_ticket_limits_samples():
    """默认一张工单只取 1 条：否则 20 条可能其实只有 1 个观测。"""
    comments = [
        _c("张三", "@U老师 问题一"), _c("U老师", "答一"),
        _c("张三", "@U老师 问题二"), _c("U老师", "答二"),
    ]
    assert len(pick_qa_pairs(comments)) == 1
    assert len(pick_qa_pairs(comments, per_ticket=2)) == 2


# ── 材料 ────────────────────────────────────────────────────

def test_material_excludes_the_question_and_everything_after():
    """**最关键的一条**：提问之后的评论绝不进材料，否则核对变成抄答案。"""
    comments = [
        _c("张三", "之前的讨论"),
        _c("李四", "@U老师 分歧在哪"),
        _c("U老师", "答案是汪海波认为……"),
    ]
    mat = material_before(comments, 1, _render)
    assert "之前的讨论" in mat
    assert "分歧在哪" not in mat, "提问本身不算材料"
    assert "汪海波" not in mat, "AI 自己的回答绝不能进材料"


def test_material_skips_system_notices():
    comments = [
        _c("system", "工单状态变更为 in_progress"),
        _c("张三", "真人发言"),
        _c("李四", "@U老师 问"),
    ]
    mat = material_before(comments, 2, _render)
    assert "真人发言" in mat
    assert "状态变更为" not in mat


def test_material_is_empty_when_nothing_prior():
    assert material_before([_c("张三", "@U老师 问")], 0, _render) == ""
