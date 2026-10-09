# -*- coding: utf-8 -*-
"""部署通知脚本回归测试：发布说明解析 / 群路由 envs / 版面选择 / 三种版面渲染。

运行：仓库根目录 `python -m pytest deploy/tests -q`
依赖：Pillow（渲染用例缺库时自动跳过，不影响其余用例）。
"""
import notify
import notify_image

NOTES_MD = """# 发布说明（Release Notes）

> 注释与「未发布」区之外的内容都不应被解析

## v1.4.2（2026-09-15）

### 新增功能
- 旧版本条目不应出现

## 未发布

### BUG 修复
- 修复甲问题
* 修复乙问题（星号列表也要认）

### 新增功能
- 新增丙功能，描述要言简意赅

### 未知分组
- 兜底组条目排在固定分组之后
"""

STD_CTX = {
    "ok": True,
    "title": "部署成功",
    "env_name": "test",
    "components": "all",
    "git_ref": "test",
    "sha": "8f3c21ab",
    "actor": "张俊磊",
    "event": "手动触发",
    "elapsed": "1 分 52 秒",
    "pr_no": "194",
    "pr_title": "feat(ORS-907): 部署通知支持自绘图片样式",
    "run_url": "https://github.com/example/actions/runs/1",
    "layout": "standard",
}


def _write_notes(tmp_path):
    p = tmp_path / "RELEASE_NOTES.md"
    p.write_text(NOTES_MD, encoding="utf-8")
    return str(p)


def _base_env(monkeypatch):
    monkeypatch.setenv("NOTIFY_STATUS", "success")
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("ENV_NAME", "prod")
    monkeypatch.setenv("ACTOR", "tester")
    monkeypatch.setenv("COMPONENTS", "all")
    monkeypatch.setenv("GIT_REF", "dev")
    monkeypatch.setenv("COMMIT_SHA", "abcdef1234567890")
    monkeypatch.setenv("RUN_URL", "https://github.com/example/actions/runs/1")
    monkeypatch.setenv("RUN_STARTED_AT", "2026-09-30T10:00:00Z")


# ---------- 发布说明解析 ----------

def test_parse_release_notes_basic(tmp_path):
    groups = notify.parse_release_notes(_write_notes(tmp_path))
    assert groups == [
        ("新增功能", ["新增丙功能，描述要言简意赅"]),
        ("BUG 修复", ["修复甲问题", "修复乙问题（星号列表也要认）"]),
        ("未知分组", ["兜底组条目排在固定分组之后"]),
    ]


def test_parse_release_notes_empty_or_missing(tmp_path):
    assert notify.parse_release_notes("") == []
    assert notify.parse_release_notes(str(tmp_path / "nope.md")) == []

    (tmp_path / "empty.md").write_text("# 只有标题\n\n没有未发布区\n", encoding="utf-8")
    assert notify.parse_release_notes(str(tmp_path / "empty.md")) == []

    (tmp_path / "noitems.md").write_text("## 未发布\n\n### 新增功能\n", encoding="utf-8")
    assert notify.parse_release_notes(str(tmp_path / "noitems.md")) == []


def test_parse_release_notes_utf8_bom(tmp_path):
    p = tmp_path / "bom.md"
    p.write_bytes("\ufeff## 未发布\n\n### 新增功能\n- BOM 文件也要能解析\n".encode("utf-8"))
    assert notify.parse_release_notes(str(p)) == [("新增功能", ["BOM 文件也要能解析"])]


# ---------- 群路由（NOTIFY_WEBHOOKS 第 4 段 envs） ----------

def test_parse_targets_envs():
    targets = notify.parse_targets(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=a|always|群A,"
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=b|always|群B|prod,"
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c|failure|群C|test;prod")
    assert [t[1] for t in targets] == ["always", "always", "failure"]
    assert [t[2] for t in targets] == ["群A", "群B", "群C"]
    assert targets[0][3] == set()                 # 缺省 = 所有环境
    assert targets[1][3] == {"prod"}
    assert targets[2][3] == {"test", "prod"}      # envs 内部分隔符是分号（逗号是目标分隔符）


def test_parse_targets_unknown_env_falls_back(capsys):
    targets = notify.parse_targets(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=a|always|群A|stage")
    assert targets[0][3] == set()                 # 环境名写错：告警但忽略限制（通知不丢优先）
    assert "未识别的环境范围" in capsys.readouterr().out


def test_main_skips_when_env_not_in_scope(monkeypatch, capsys):
    monkeypatch.setenv(
        "NOTIFY_WEBHOOKS",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=a|always|群A|prod")
    monkeypatch.setenv("ENV_NAME", "test")
    monkeypatch.setenv("NOTIFY_STATUS", "success")
    monkeypatch.delenv("RELEASE_NOTES_FILE", raising=False)
    assert notify.main() == 0
    out = capsys.readouterr().out
    assert "环境范围为 prod" in out and "按策略跳过" in out


# ---------- 版面选择 ----------

def test_pick_layout():
    notes = [("新增功能", ["x"])]
    assert notify.pick_layout(True, "test", notes) == "standard"      # test 不换版面
    assert notify.pick_layout(True, "prod", notes) == "release"       # 生产成功 + 有说明
    assert notify.pick_layout(True, "prod", []) == "standard"         # 生产成功但无说明
    assert notify.pick_layout(False, "prod", []) == "failure-prod"    # 生产失败
    assert notify.pick_layout(False, "test", []) == "standard"


def test_collect_release_layout(monkeypatch, tmp_path):
    _base_env(monkeypatch)
    monkeypatch.setenv("RELEASE_NOTES_FILE", _write_notes(tmp_path))
    c = notify.collect()
    assert c["layout"] == "release"
    assert c["env_label"] == "生产"
    assert c["release_date"]                              # 日期串（不显示版本号）
    assert [g[0] for g in c["release_notes"]] == ["新增功能", "BUG 修复", "未知分组"]
    assert c["steps"] == []                               # 成功不带失败步骤


def test_collect_failure_layout(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("NOTIFY_STATUS", "failure")
    monkeypatch.setenv("AUTO_ROLLBACK", "yes")
    monkeypatch.delenv("RELEASE_NOTES_FILE", raising=False)
    c = notify.collect()
    assert c["layout"] == "failure-prod"
    assert c["steps"] == list(notify.FAILURE_STEPS)
    assert any("自动回滚" in a for a in c["alerts"])


def test_collect_test_layout_unchanged(monkeypatch, tmp_path):
    _base_env(monkeypatch)
    monkeypatch.setenv("ENV_NAME", "test")
    monkeypatch.setenv("RELEASE_NOTES_FILE", _write_notes(tmp_path))
    c = notify.collect()
    assert c["layout"] == "standard"                     # test 保持原版面


# ---------- 门禁非阻塞后的如实标注 ----------

def test_gate_failed_steps_reads_outcomes(monkeypatch):
    monkeypatch.delenv("PYTEST_OUTCOME", raising=False)
    monkeypatch.delenv("VITEST_OUTCOME", raising=False)
    assert notify.gate_failed_steps() == []               # 未跑（skipped）/ 未传都算正常
    monkeypatch.setenv("PYTEST_OUTCOME", "success")
    monkeypatch.setenv("VITEST_OUTCOME", "skipped")
    assert notify.gate_failed_steps() == []
    monkeypatch.setenv("VITEST_OUTCOME", "failure")
    assert notify.gate_failed_steps() == ["vitest"]
    monkeypatch.setenv("PYTEST_OUTCOME", "failure")
    assert notify.gate_failed_steps() == ["pytest", "vitest"]


def test_collect_marks_gate_failures_nonblocking(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("PYTEST_OUTCOME", "failure")
    monkeypatch.setenv("VITEST_OUTCOME", "success")
    monkeypatch.delenv("RELEASE_NOTES_FILE", raising=False)
    c = notify.collect()
    assert c["gate_failed"] == ["pytest"]
    assert any("非阻塞放行" in a for a in c["alerts"])
    assert "有失败用例（非阻塞放行）" in notify.build_markdown(c)["markdown_v2"]["content"]
    assert "非阻塞放行" in notify.build_text(c)
    card = notify.build_card(c)["template_card"]
    keys = [row["keyname"] for row in card.get("horizontal_content_list", [])]
    assert "测试门禁" in keys


def test_collect_reports_gate_pass_when_clean(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("PYTEST_OUTCOME", "success")
    monkeypatch.setenv("VITEST_OUTCOME", "success")
    monkeypatch.delenv("RELEASE_NOTES_FILE", raising=False)
    c = notify.collect()
    assert c["gate_failed"] == []
    md = notify.build_markdown(c)["markdown_v2"]["content"]
    assert "✅ 已通过" in md and "有失败用例" not in md


# ---------- markdown / 纯文本降级时附带发布说明 ----------

def test_markdown_and_text_carry_notes(monkeypatch, tmp_path):
    _base_env(monkeypatch)
    monkeypatch.setenv("RELEASE_NOTES_FILE", _write_notes(tmp_path))
    c = notify.collect()
    md = notify.build_markdown(c)["markdown_v2"]["content"]
    assert "**更新说明**" in md and "新增丙功能，描述要言简意赅" in md
    text = notify.build_text(c)
    assert "更新说明:" in text and "[BUG 修复] 修复甲问题" in text


# ---------- 三种版面渲染 ----------

def _render(layout, extra):
    pytest = __import__("pytest")
    pytest.importorskip("PIL")
    ctx = dict(STD_CTX)
    ctx.update(extra)
    ctx["layout"] = layout
    try:
        return notify_image.render_png(ctx)
    except notify_image.NotifyImageError as exc:
        __import__("pytest").skip(f"渲染环境不可用：{exc}")


def test_render_standard_layout():
    png = _render("standard", {})
    assert png[:4] == b"\x89PNG" and len(png) <= 2 * 1024 * 1024


def test_render_release_layout():
    png = _render("release", {
        "env_name": "prod",
        "env_label": "生产",
        "release_date": "2026-09-30",
        "release_notes": [
            ("新增功能", ["提单页支持 AI 生成问题文档，会话内容一键转结构化补充段",
                          "摇人页承接车体扫码：出厂码弹车辆确认并切换车型引导流"]),
            ("体验优化", ["部署通知改自绘图片卡片，发布说明一图览、字号更大"]),
            ("BUG 修复", ["修复未关注用户首次扫码链路断裂（扫码事件前缀剥离）"]),
        ],
    })
    assert png[:4] == b"\x89PNG" and len(png) <= 2 * 1024 * 1024


def test_render_failure_layout():
    png = _render("failure-prod", {
        "ok": False,
        "title": "部署失败",
        "env_name": "prod",
        "env_label": "生产",
        "release_date": "2026-09-30",
        "alerts": ["健康检查未通过，已自动回滚到部署前版本，请人工确认线上服务"],
        "steps": list(notify.FAILURE_STEPS),
        "auto_rollback": True,
    })
    assert png[:4] == b"\x89PNG" and len(png) <= 2 * 1024 * 1024


def test_render_standard_layout_with_gate_failures():
    png = _render("standard", {"gate_failed": ["pytest", "vitest"]})
    assert png[:4] == b"\x89PNG" and len(png) <= 2 * 1024 * 1024


def test_render_unknown_layout_falls_back_to_standard():
    assert _render("no-such-layout", {}) == _render("standard", {})
