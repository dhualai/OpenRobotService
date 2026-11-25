"""deep_review 渲染与工具函数测试（不真调 LLM）。"""
from deep_review import (
    build_diff_text, parse_llm_json, render_report, render_wecom,
)
from quick_screen import screen


def f(path, patch="", changes=1):
    return {"filename": path, "status": "modified", "patch": patch,
            "additions": 1, "deletions": 0, "changes": changes}


def test_diff_prefers_important_files():
    files = [
        f("backend/app/big_unrelated.py", "+x\n" * 50, changes=50),
        f("backend/alembic/versions/a.py", "+op.add_column('t','c')\n"),
    ]
    quick = screen(files)
    diff, n = build_diff_text(files, {it["path"] for it in quick["blockers"]})
    assert diff.index("alembic") < diff.index("big_unrelated")


def test_diff_clips_large_patch():
    files = [f("backend/app/x.py", "+line\n" * 500, changes=500)]
    diff, _ = build_diff_text(files, set())
    assert "已截断" in diff


def test_parse_llm_json_tolerates_fence():
    content = "```json\n{\"conclusion\": \"高风险\", \"blockers\": [], " \
              "\"warnings\": [], \"infos\": []}\n```"
    parsed = parse_llm_json(content)
    assert parsed["conclusion"] == "高风险"


def test_render_report_green():
    """无 blocker 时只出一句绿灯结论，黄绿意见不渲染。"""
    parsed = {"conclusion": "未发现明显风险", "blockers": [],
              "warnings": [{"location": "b.py:1", "issue": "小问题",
                            "suggestion": "改掉"}], "infos": []}
    md = render_report(parsed, {"skipped": True, "blockers": [], "warnings": []},
                       431, "abcd1234", "deepseek-flash")
    assert "🟢 **未发现必须采纳的高危改动**" in md
    assert "小问题" not in md and "### 🟡" not in md


def test_render_report_llm_failed():
    md = render_report(None, {"skipped": True, "blockers": [], "warnings": []},
                       431, "abcdef123456", "双通道超时")
    assert "未能完成" in md
    assert "不阻塞合并" in md
    assert "<!-- ors-ai-review:report -->" in md


def test_render_report_full():
    parsed = {
        "conclusion": "高风险",
        "blockers": [{"location": "a.py:52", "issue": "删列未确认",
                      "suggestion": "确认无读写方"}],
        "warnings": [{"location": "b.py:1", "issue": "datetime.now",
                      "suggestion": "改 UTC"}],
        "infos": [],
    }
    quick = screen([f("backend/app/y.py", "+a = datetime.now()\n")])
    md = render_report(parsed, quick, 431, "abcdef123456", "deepseek-v4-flash")
    # 只展示红档：标题 + 红组（快筛置顶）+ blockers，黄绿不渲染
    assert "# 🤖 AI 检视报告" in md
    assert "🔴 **发现 2 项必须采纳的问题**（AI 1 项 + 规则快筛 1 项）" in md
    assert "`a.py:52`" in md
    assert "<details open><summary>💡 小D的建议</summary>" in md
    assert "🔴 规则快筛命中（确定性）" in md
    assert "deepseek-v4-flash" in md
    assert "### 🟡" not in md and "### 🟢" not in md   # 黄绿不展示
    import re as _re
    head = _re.search(r"\*\*PR #431\*\*[^\n]*", md).group(0)
    assert _re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", head)  # 上海时间到秒
    assert "🪵" in md                 # 文档页脚


def test_render_wecom_only_when_blocker():
    parsed_block = {"conclusion": "高风险",
                    "blockers": [{"location": "a.py:1", "issue": "x"}],
                    "warnings": [], "infos": []}
    wecom = render_wecom(parsed_block, {"blockers": []}, "dhualai/OpenRobotService",
                         431, "T")
    assert wecom and "1 项高危" in wecom and "pull/431" in wecom
    assert render_wecom({"blockers": []}, {"blockers": []}, "r", 431, "T") is None


def test_report_no_double_count_when_all_inline():
    """防回归：blocker 全部转行内时总结不得出现双重计数。

    PR #511 的中间提交（f28）曾加「发现 N 项已钉」分支却漏了 return，
    导致继续落到通用计数行再追加「发现 0 项」——修分裂的代码自己分裂。
    f29 全量汇总后 blocker_items 不再剔除，该路径已不可达；此用例锁死。
    """
    from deep_review import render_report
    from ai_review_common import finding_fingerprint
    fp = finding_fingerprint("a.py:5", "x")
    # blocker 非空（全量渲染）+ 行内指纹：只允许出现一次「发现 1 项」
    parsed = {"conclusion": "高风险",
              "blockers": [{"location": "a.py:5", "issue": "x",
                            "suggestion": "y"}],
              "warnings": [], "infos": []}
    md = render_report(parsed, None, 511, "034be50c", "m", inline_paths={fp})
    assert md.count("发现 1 项") == 1
    assert "📍 `a.py:5`" in md
    assert "发现 0 项" not in md
    # 全空场景走绿灯，无任何「发现 N 项」计数
    empty = {"conclusion": "低风险", "blockers": [], "warnings": [], "infos": []}
    md2 = render_report(empty, None, 511, "abc", "m", inline_paths={fp})
    assert "发现 0 项" not in md2
    assert "🟢" in md2
