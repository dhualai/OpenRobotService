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


def test_report_doc_layout():
    """文档化排版：红黄绿固定排序（红组含快筛且最前）、无 alert 色条、建议默认展开。"""
    parsed = {
        "conclusion": "高风险",
        "blockers": [{"location": "a.py:5", "issue": "删列" * 60,
                      "suggestion": "确认无读写"}],
        "warnings": [{"location": "b.py:1", "issue": "datetime.now",
                      "suggestion": "改 UTC"}],
        "infos": [{"location": "c.py:1", "issue": "命名", "suggestion": ""}],
    }
    quick = {"skipped": False, "blockers": [
        {"path": "m.py", "label": "数据库迁移",
         "reason": "DDL 不可自动回滚"}], "warnings": []}
    md = render_report(parsed, quick, 492, "abcd1234", "deepseek-flash")
    # 排序：红组（快筛在前）→ 黄 → 绿（匹配 ### 分组标题，避开表格列头）
    assert md.index("### 🔴 规则快筛命中") < md.index("### 🔴 必须采纳")
    assert md.index("### 🔴 必须采纳") < md.index("### 🟡 选择采纳")
    assert md.index("### 🟡 选择采纳") < md.index("### 🟢 可优化")
    # 无 alert 色条（用户要求去除左侧色条）
    assert "[!CAUTION]" not in md and "[!WARNING]" not in md and "[!TIP]" not in md
    # 内容不截断（120 字 issue 全文保留）+ 建议默认展开 + 卡通图标
    assert "删列" * 60 in md
    assert "<details open>" in md and "🧸" in md
    # 上海时间：无 UTC 标注 + 计数表格 + 页脚
    import re as _re
    head = _re.search(r"\*\*PR #492\*\*[^\n]*", md).group(0)
    assert _re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", head)  # 上海时间
    assert "| 🟢 可优化 |" in md and "🪵" in md


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


def test_insert_delta_by_anchor():
    body = ("<!-- ors-ai-review:report -->\n"
            "<!-- ors-ai-review:fp=x -->\n"
            "<!-- ors-ai-review:delta -->\n"
            "# 🤖 AI 检视报告\n")
    out = insert_delta(body, "> 🔄 较上次检视：**新增 1 条**")
    assert "> 🔄 较上次检视：**新增 1 条**" in out
    assert "<!-- ors-ai-review:delta -->" not in out   # 锚点被替换
