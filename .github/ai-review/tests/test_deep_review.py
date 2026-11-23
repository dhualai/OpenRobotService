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
    # 文档化排版：标题 + 计数表格 + 红组最前（规则快筛优先于 AI 判断）
    assert "# 🤖 AI 检视报告" in md
    assert "**高风险**" in md and "| 🔴 必须采纳 |" in md
    assert "`a.py:52`" in md
    assert "<details open><summary>🧸 小D的建议</summary>" in md
    assert "🔴 规则快筛命中（确定性）" in md
    assert "deepseek-v4-flash" in md
    import re as _re
    head = _re.search(r"\*\*PR #431\*\*[^\n]*", md).group(0)
    assert _re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", head)  # 上海时间无 UTC 标注
    assert "🪵" in md                 # 文档页脚


def test_render_wecom_only_when_blocker():
    parsed_block = {"conclusion": "高风险",
                    "blockers": [{"location": "a.py:1", "issue": "x"}],
                    "warnings": [], "infos": []}
    wecom = render_wecom(parsed_block, {"blockers": []}, "dhualai/OpenRobotService",
                         431, "T")
    assert wecom and "1 项高危" in wecom and "pull/431" in wecom
    assert render_wecom({"blockers": []}, {"blockers": []}, "r", 431, "T") is None
