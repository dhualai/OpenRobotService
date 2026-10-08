# -*- coding: utf-8 -*-
"""只读 CLI 白名单：不打 LLM。"""
from pathlib import Path

from ai.agents.AiTaskPlatform.log_analyzer.shell_tool import run_readonly_shell
from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import _parse_llm_command


def _log(tmp_path: Path) -> Path:
    p = tmp_path / "robot.log"
    p.write_text(
        "2026-08-11 11:01:44 INFO XNA-169 idle\n"
        "2026-08-11 11:01:45 ERROR 一致性校验失败 lock_id=8842\n"
        "2026-08-11 11:01:46 WARN XNA-170 retry\n"
        "2026-08-11 11:01:47 ERROR 路径规划失败\n",
        encoding="utf-8",
    )
    return p


def test_grep_pipe_head(tmp_path: Path):
    log = _log(tmp_path)
    ok, text, n = run_readonly_shell("grep -n ERROR | head -n 10", str(log))
    assert ok
    assert "一致性校验失败" in text
    assert "8842" in text
    assert "路径规划失败" in text
    assert n == 2


def test_rejects_write_and_unknown(tmp_path: Path):
    log = _log(tmp_path)
    ok, text, n = run_readonly_shell("rm -rf /", str(log))
    assert not ok and n == 0
    assert "白名单" in text
    ok, text, _ = run_readonly_shell("grep ERROR > out.txt", str(log))
    assert not ok
    assert "禁止" in text


def test_rejects_path_escape(tmp_path: Path):
    log = _log(tmp_path)
    outside = tmp_path.parent / "secret.log"
    try:
        outside.write_text("secret\n", encoding="utf-8")
        ok, text, _ = run_readonly_shell(f"cat {outside.as_posix()}", str(log))
        assert not ok
        assert "超出" in text or "拒绝" in text
    finally:
        if outside.exists():
            outside.unlink()


def test_sed_n_range(tmp_path: Path):
    log = _log(tmp_path)
    ok, text, n = run_readonly_shell("sed -n '2,3p'", str(log))
    assert ok
    assert n == 2
    assert "一致性校验失败" in text
    assert "XNA-170" in text
    assert "idle" not in text.split(":", 1)[-1] or "idle" not in text


def test_awk_print(tmp_path: Path):
    log = _log(tmp_path)
    ok, text, n = run_readonly_shell("awk '{print $1}' | head -n 2", str(log))
    assert ok
    assert n == 2
    assert "2026-08-11" in text


def test_parse_shell_command():
    cmd = _parse_llm_command(
        '{"action":"shell","analysis":"找校验","cmd":"grep -n 一致性校验失败 | head -n 20"}'
    )
    assert cmd["action"] == "shell"
    assert "grep" in cmd["cmd"]
