"""发行说明兜底：Release 不存在时不能把 gh 的 404 错误 JSON 写进文件。

回归背景：生产通知卡片一度退化成没有「更新日志」区的 standard 版面，
真凶是 workflow 里 `gh api ... --jq '.body'` 在 Release 不存在时把 404 的
错误 JSON 打到 stdout，那段 JSON 被当成发行说明写进了 RELEASE_NOTES.md。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(1, str(Path(__file__).resolve().parents[1]))

from fetch_release_notes import fetch_body, main, pick_body, write_notes

NOT_FOUND_PAYLOAD = {
    "message": "Not Found",
    "documentation_url": "https://docs.github.com/rest/releases/releases#get-a-release-by-tag-name",
    "status": "404",
}


def _runner(stdout, returncode=0):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")

    return run


def test_pick_body_rejects_404_error_payload():
    assert pick_body(json.dumps(NOT_FOUND_PAYLOAD)) == ""


def test_pick_body_rejects_plain_text_error():
    assert pick_body("release not found") == ""


def test_pick_body_rejects_release_without_body_field():
    assert pick_body(json.dumps({"tag_name": "prod-release-notes"})) == ""


def test_pick_body_rejects_non_object_payload():
    assert pick_body(json.dumps(["prod-release-notes"])) == ""


def test_pick_body_accepts_real_release():
    body = "# 发布说明\n\n## 未发布\n\n### 新增功能\n- 示例\n"
    assert pick_body(json.dumps({"tag_name": "prod-release-notes", "body": body})) == body


def test_fetch_body_ignores_gh_error_exit_code():
    """gh 遇 HTTP 错误会非 0 退出但把错误 JSON 打到 stdout——不能当 body。"""
    runner = _runner(json.dumps(NOT_FOUND_PAYLOAD), returncode=1)
    assert fetch_body("owner/repo", runner=runner) == ""


def test_fetch_body_returns_body_on_success():
    body = "## 未发布\n\n### BUG 修复\n- 修复 xxx\n"
    runner = _runner(json.dumps({"body": body}))
    assert fetch_body("owner/repo", runner=runner) == body


def test_write_notes_skips_empty_body(tmp_path):
    out = tmp_path / "RELEASE_NOTES.md"
    out.write_text("手写版", encoding="utf-8")
    assert write_notes("", out) is False
    assert out.read_text(encoding="utf-8") == "手写版"


def test_write_notes_writes_body(tmp_path):
    out = tmp_path / "RELEASE_NOTES.md"
    assert write_notes("## 未发布\n- 条目\n", out) is True
    assert out.read_text(encoding="utf-8").startswith("## 未发布")


def test_main_keeps_existing_notes_when_release_missing(tmp_path, monkeypatch):
    """404 场景：手写版必须原样保留，不能被错误 JSON 覆盖。"""
    out = tmp_path / "RELEASE_NOTES.md"
    out.write_text("# 手写版\n\n## 未发布\n\n### 新增功能\n- 手写条目\n", encoding="utf-8")
    monkeypatch.setattr("fetch_release_notes.fetch_body",
                        lambda repo, tag="prod-release-notes": "")
    assert main(["--repo", "owner/repo", "--out", str(out)]) == 0
    assert "手写条目" in out.read_text(encoding="utf-8")
    assert "Not Found" not in out.read_text(encoding="utf-8")


def test_main_overwrites_notes_with_release_body(tmp_path, monkeypatch):
    out = tmp_path / "RELEASE_NOTES.md"
    out.write_text("# 手写版\n", encoding="utf-8")
    body = "# 发布说明\n\n## 未发布\n\n### 新增功能\n- Release 里的条目\n"
    monkeypatch.setattr("fetch_release_notes.fetch_body",
                        lambda repo, tag="prod-release-notes": body)
    assert main(["--repo", "owner/repo", "--out", str(out)]) == 0
    assert "Release 里的条目" in out.read_text(encoding="utf-8")


def test_main_returns_zero_when_gh_call_raises(tmp_path, monkeypatch):
    def boom(repo, tag="prod-release-notes"):
        raise FileNotFoundError("gh")

    monkeypatch.setattr("fetch_release_notes.fetch_body", boom)
    out = tmp_path / "RELEASE_NOTES.md"
    assert main(["--repo", "owner/repo", "--out", str(out)]) == 0


@pytest.mark.parametrize("step_marker", ["fetch_release_notes.py"])
def test_workflow_restore_step_uses_script_not_raw_jq(step_marker):
    """哨兵：workflow 必须走脚本，不能再直接 `gh api --jq '.body'` 取 body。"""
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/deploy-split.yml"
    text = workflow.read_text(encoding="utf-8")
    assert step_marker in text
    # 只看代码行：注释里可以说明旧写法为什么不行
    code_lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    assert "--jq '.body'" not in "\n".join(code_lines)
