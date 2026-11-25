"""通知与评论脚本测试（mock GitHub API / HTTP）。"""
from unittest import mock

import notify_wecom
import post_comment
from ai_review_common import allowed_webhook, masked, parse_targets


# ---------------- NOTIFY_WEBHOOKS 解析（与 deploy/notify.py 同款格式） ----------

def test_parse_targets_full_format():
    targets = parse_targets("https://a|always|开源小组, https://b|failure|发布群|prod")
    assert targets[0] == ("https://a", "always", "开源小组", set())
    assert targets[1] == ("https://b", "failure", "发布群", {"prod"})


def test_parse_targets_envs_semicolon():
    targets = parse_targets("https://a|always|群|test;prod")
    assert targets[0][3] == {"test", "prod"}


def test_parse_targets_bad_policy_defaults_always():
    targets = parse_targets("https://a|whatever|群")
    assert targets[0][1] == "always"


def test_webhook_whitelist():
    assert allowed_webhook("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")
    assert allowed_webhook("https://open.feishu.cn/open-apis/bot/v2/hook/x")
    assert not allowed_webhook("http://qyapi.weixin.qq.com/x")     # 非 https
    assert not allowed_webhook("https://evil.example.com/x")       # 非白名单


def test_masked_hides_key():
    url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET123"
    m = masked(url)
    assert "SECRET123" not in m and "qyapi.weixin.qq.com" in m


# ---------------- notify_wecom 路由 ----------------

def _run_notify(monkeypatch, raw, sender):
    monkeypatch.setenv("NOTIFY_WEBHOOKS", raw)
    monkeypatch.setattr(notify_wecom, "wecom_markdown", sender)
    monkeypatch.syspath_prepend(".")
    tmp = notify_wecom.__file__
    assert tmp
    # 写临时文案文件
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write("**AI 检视发现高危项**\n▪ 测试")
        path = fh.name
    import sys
    argv = ["notify_wecom.py", "--file", path]
    with mock.patch.object(sys, "argv", argv):
        assert notify_wecom.main() == 0


def test_notify_routes_test_scope_only(monkeypatch):
    """envs=prod 的目标跳过，缺省（全环境）目标发送——USP 群不会被 AI Review 打扰。"""
    sent = []
    _run_notify(
        monkeypatch,
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=A|always|开源小组,"
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=B|always|USP群|prod",
        lambda url, content: sent.append(url) or (True, "ok"))
    assert len(sent) == 1


# ---------------- post_comment sticky 逻辑 ----------------

def test_post_comment_updates_existing(monkeypatch, tmp_path):
    report = tmp_path / "report.md"
    report.write_text("<!-- ors-ai-review:report -->\n**结论：测试**", encoding="utf-8")
    calls = {}

    def fake_gh_api(repo, path, token, method="GET", body=None, accept=None):
        if path.startswith("issues/431/comments?"):
            return 200, [{"id": 7, "body": "<!-- ors-ai-review:report -->旧"}]
        calls["method"] = method
        calls["path"] = path
        return 200, {"html_url": "http://x"}

    monkeypatch.setenv("REPO", "dhualai/OpenRobotService")
    monkeypatch.setenv("PR_NUMBER", "431")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(post_comment, "gh_api", fake_gh_api)
    with mock.patch.object(post_comment.sys, "argv",
                           ["post_comment.py", "--marker",
                            "ors-ai-review:report", "--file", str(report)]):
        assert post_comment.main() == 0
    assert calls["method"] == "PATCH"
    assert calls["path"] == "issues/comments/7"


def test_post_comment_creates_when_absent(monkeypatch, tmp_path):
    report = tmp_path / "report.md"
    report.write_text("<!-- ors-ai-review:report -->\n新报告", encoding="utf-8")
    calls = {}

    def fake_gh_api(repo, path, token, method="GET", body=None, accept=None):
        if path.startswith("issues/431/comments?"):
            return 200, [{"id": 1, "body": "无标记评论"}]
        calls["method"] = method
        return 201, {"html_url": "http://x"}

    monkeypatch.setenv("REPO", "r")
    monkeypatch.setenv("PR_NUMBER", "431")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(post_comment, "gh_api", fake_gh_api)
    with mock.patch.object(post_comment.sys, "argv",
                           ["post_comment.py", "--marker",
                            "ors-ai-review:report", "--file", str(report)]):
        assert post_comment.main() == 0
    assert calls["method"] == "POST"
