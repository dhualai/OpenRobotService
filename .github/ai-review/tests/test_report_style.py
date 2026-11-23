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


def test_report_uses_colored_alerts():
    parsed = {
        "conclusion": "高风险",
        "blockers": [{"location": "a.py:5", "issue": "删列",
                      "suggestion": "确认无读写"}],
        "warnings": [{"location": "b.py:1", "issue": "datetime.now",
                      "suggestion": "改 UTC"}],
        "infos": [{"location": "c.py:1", "issue": "命名", "suggestion": ""}],
    }
    md = render_report(parsed, None, 492, "abcd1234", "deepseek-flash")
    assert "[!CAUTION]" in md and "🔴 必须采纳" in md
    assert "[!WARNING]" in md and "🟡 选择采纳" in md
    assert "[!TIP]" in md and "🟢 可优化" in md
    assert "🟢 **结论：高风险**" or "🔴 **结论：高风险**" in md
    assert "<details><summary>💡 建议</summary>" in md
    # 精简断言：问题行单行内，无两行式「建议：」前缀
    assert "\n  - 建议：" not in md


def test_report_inline_paths_removed_from_summary():
    fp = finding_fingerprint("a.py:5", "删列")
    parsed = {
        "conclusion": "高风险",
        "blockers": [{"location": "a.py:5", "issue": "删列",
                      "suggestion": "确认"}],
        "warnings": [], "infos": [],
    }
    md = render_report(parsed, None, 492, "abcd1234", "m", inline_paths={fp})
    assert "删列" not in md          # 已发行内的意见从总结剔除


def test_fingerprint_delta():
    old = "<!-- ors-ai-review:fp=aaa,bbb -->"
    new_same = "<!-- ors-ai-review:fp=bbb,aaa -->"
    new_added = "<!-- ors-ai-review:fp=bbb,ccc -->"
    assert "无变化" in fingerprint_delta(old, new_same)
    d = fingerprint_delta(old, new_added)
    assert "新增 1 条" in d and "已消除 1 条" in d
    assert fingerprint_delta(None, new_added) is None
    assert fingerprint_delta("", new_added) is None


def test_insert_delta_position():
    body = ("<!-- ors-ai-review:report -->\n"
            "<!-- ors-ai-review:fp=x -->\n"
            "**AI 检视报告** · PR #492\n"
            "🔴 **结论：高风险**\n")
    out = insert_delta(body, "> 🔄 较上次检视：**新增 1 条**")
    lines = out.split("\n")
    assert lines[3].startswith("🔴")          # 结论行在前
    assert lines[4] == "> 🔄 较上次检视：**新增 1 条**"   # 增量行紧随其后
