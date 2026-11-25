"""报告样式（三色 alert/精简）与增量对比、行内落点测试。"""
from ai_review_common import finding_fingerprint, parse_new_side_lines
from deep_review import render_report
from post_comment import fingerprint_delta, insert_delta


def test_parse_new_side_lines():
    patch = ("@@ -10,4 +10,5 @@ ctx\n+new1\n+new2\n ctx\n+new3\n ctx\n"
             "@@ -40,1 +50,1 @@ ctx2\n+new4\n")
    lines = parse_new_side_lines(patch)
    assert {10, 11, 12, 13, 14, 50} <= lines


def test_fingerprint_stable():
    a = finding_fingerprint("a.py:1", "问题")
    b = finding_fingerprint("a.py:1", "问题")
    c = finding_fingerprint("a.py:2", "问题")
    assert a == b and a != c and len(a) == 8


def test_report_red_only_layout():
    """只留红档：快筛红组置顶 → 必须采纳；黄绿不渲染；建议 💡 默认展开。"""
    parsed = {
        "conclusion": "高风险",
        "blockers": [{"location": "a.py:5", "issue": "删列" * 60,
                      "suggestion": "确认无读写方，历史数据先归档"}],
        "warnings": [{"location": "b.py:1", "issue": "datetime.now",
                      "suggestion": "改 UTC"}],
        "infos": [{"location": "c.py:1", "issue": "命名", "suggestion": ""}],
    }
    quick = {"skipped": False, "blockers": [
        {"path": "m.py", "label": "数据库迁移",
         "reason": "DDL 不可自动回滚"}], "warnings": []}
    md = render_report(parsed, quick, 492, "abcd1234", "deepseek-flash")
    assert md.index("### 🔴 规则快筛命中") < md.index("### 🔴 必须采纳")
    assert "### 🟡" not in md and "### 🟢" not in md
    assert "[!CAUTION]" not in md
    assert "删列" * 60 in md                      # 内容不截断
    assert "<details open><summary>💡 小D的建议</summary>" in md
    assert " UTC" not in _head_of(md) and "🪵" in md
    # 计数行与红组条目数一致
    assert "🔴 **发现 2 项必须采纳的问题**（AI 1 项 + 规则快筛 1 项）" in md


def _head_of(md):
    import re
    return re.search(r"\*\*PR #492\*\*[^\n]*", md).group(0)

def test_report_inline_items_kept_with_pin():
    """总结全量展示（口径统一），已钉行内的条目带 📍 标记。"""
    fp = finding_fingerprint("a.py:5", "删列")
    parsed = {
        "conclusion": "高风险",
        "blockers": [{"location": "a.py:5", "issue": "删列",
                      "suggestion": "确认"}],
        "warnings": [], "infos": [],
    }
    md = render_report(parsed, None, 492, "abcd1234", "m", inline_paths={fp})
    assert "删列" in md                     # 全量展示（不剔除）
    assert "📍 `a.py:5`" in md              # 带钉出标记


def test_fingerprint_delta():
    old = "<!-- ors-ai-review:fp=aaa,bbb -->"
    new_same = "<!-- ors-ai-review:fp=bbb,aaa -->"
    new_added = "<!-- ors-ai-review:fp=bbb,ccc -->"
    assert "无变化" in fingerprint_delta(old, new_same)
    d = fingerprint_delta(old, new_added)
    assert "新增 1 条" in d and "已消除 1 条" in d
    assert fingerprint_delta(None, new_added) is None
    assert fingerprint_delta("", new_added) is None


def test_insert_delta_by_anchor():
    body = ("<!-- ors-ai-review:report -->\n"
            "<!-- ors-ai-review:fp=x -->\n"
            "<!-- ors-ai-review:delta -->\n"
            "# 🤖 AI 检视报告\n")
    out = insert_delta(body, "> 🔄 较上次检视：**新增 1 条**")
    assert "> 🔄 较上次检视：**新增 1 条**" in out
    assert "<!-- ors-ai-review:delta -->" not in out   # 锚点被替换
