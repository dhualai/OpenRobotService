"""quick_screen 规则引擎测试：命中、分级、去重、误报抑制。"""
from quick_screen import render_quick_markdown, screen


def f(path, patch=""):
    return {"filename": path, "status": "modified", "patch": patch,
            "additions": 1, "deletions": 0, "changes": 1}


def test_doc_only_pr_skipped():
    r = screen([f("docs/ai-review/rules.md", "+hello")])
    assert r["skipped"] is True


def test_lock_files_ignored():
    r = screen([f("backend/alembic/versions/x.py"),
                f("frontend/package-lock.json", "+\"version\": \"1\"")])
    paths = {it["path"] for it in r["blockers"] + r["warnings"]}
    assert "frontend/package-lock.json" not in paths
    assert any("alembic" in p for p in paths)


def test_alembic_is_blocker():
    r = screen([f("backend/alembic/versions/abc_add_col.py")])
    assert len(r["blockers"]) == 1
    assert r["blockers"][0]["label"] == "数据库迁移"


def test_workflow_is_warning_not_blocker():
    r = screen([f(".github/workflows/ci.yml")])
    assert r["blockers"] == []
    assert len(r["warnings"]) == 1


def test_env_file_is_blocker():
    r = screen([f("ai/.env.local")])
    assert len(r["blockers"]) == 1


def test_secret_literal_is_blocker():
    r = screen([f("backend/app/x.py", "+API_KEY = \"sk-abcdef1234567890abcdef\"\n")])
    labels = [it["label"] for it in r["blockers"]]
    assert "疑似密钥" in labels


def test_env_reference_not_flagged():
    """引用形态不算硬编码（误报抑制）。"""
    r = screen([f("backend/app/x.py",
                  "+API_KEY = os.environ.get(\"DEEPSEEK_API_KEY\")\n")])
    assert not any(it["label"] == "疑似密钥" for it in r["blockers"])


def test_drop_column_is_blocker():
    r = screen([f("backend/alembic/versions/x.py",
                  "+op.drop_column('users', 'remark')\n")])
    labels = [it["label"] for it in r["blockers"]]
    assert "删列风险" in labels


def test_datetime_now_is_warning():
    r = screen([f("backend/app/api/a.py", "+now = datetime.now()\n")])
    assert not r["blockers"]
    assert any(it["label"] == "时间字段" for it in r["warnings"])


def test_shell_true_is_warning():
    r = screen([f("backend/app/x.py", "+subprocess.run(cmd, shell=True)\n")])
    assert any(it["label"] == "命令/动态执行" for it in r["warnings"])


def test_python_exec_and_eval_still_flagged():
    """收紧正则后，独立 exec()/eval() 调用仍须命中（不能放过真风险）。"""
    r = screen([f("backend/app/x.py", "+exec(user_code)\n+result = eval(expr)\n")])
    hits = [it for it in r["warnings"] if it["label"] == "命令/动态执行"]
    assert len(hits) == 1  # 同文件同标签去重


def test_js_regex_exec_not_flagged():
    """JS 正则 .exec() 不是动态执行（PR #516 误报回归：前端仅重排行就中招）。"""
    r = screen([f("frontend/src/x.ts",
                  "+const m = /^\\/tasks\\/(\\d+)$/.exec(stripBase(url));\n")])
    assert not any(it["label"] == "命令/动态执行" for it in r["warnings"])


def test_method_named_exec_not_flagged():
    """对象方法调用（obj.exec(...)）同属方法形态，不按动态执行误报。"""
    r = screen([f("backend/app/x.py", "+runner.exec(cmd)\n")])
    assert not any(it["label"] == "命令/动态执行" for it in r["warnings"])


def test_deleted_lines_not_checked():
    """删除行不检查（误报抑制）。"""
    r = screen([f("backend/app/x.py", "-API_KEY = \"sk-abcdef1234567890abcdef\"\n")])
    assert r["blockers"] == []


def test_dedup_same_file_same_label():
    r = screen([f("backend/app/x.py",
                  "+a = datetime.now()\n+b = datetime.now()\n")])
    hits = [it for it in r["warnings"] if it["label"] == "时间字段"]
    assert len(hits) == 1


def test_requirements_txt_not_treated_as_doc():
    """requirements*.txt 是依赖清单不是文档，必须命中依赖规则（回归）。"""
    r = screen([f("backend/requirements.txt", "+flask==2.0.0\n")])
    paths = {it["path"] for it in r["warnings"]}
    assert "backend/requirements.txt" in paths


def test_requirements_md_still_doc():
    """requirements.md 是文档不是依赖清单（精确正则防误伤）。"""
    r = screen([f("backend/requirements.md", "+# deps\n"),
                f("backend/requirements.txt", "+flask==2.0.0\n")])
    assert r["skipped"] is False      # 有代码类（requirements.txt）才不 skip
    paths = {it["path"] for it in r["warnings"]}
    assert "backend/requirements.txt" in paths


def test_self_dir_excluded():
    """检视器自身目录豁免：规则源码字面量与测试桩不得自我命中（PR #492 教训）。"""
    r = screen([
        f(".github/ai-review/scripts/quick_screen.py",
         "+    (r\"DROP\\\\s+COLUMN|drop_column|remove_column\", \"blocker\", ...)\n"),
        f(".github/ai-review/tests/test_quick_screen.py",
         "+API_KEY = \"sk-abcdef1234567890abcdef\"\n"),
    ])
    assert r["blockers"] == [] and r["warnings"] == []


def test_render_markdown_has_marker_and_sections():
    r = screen([f("backend/alembic/versions/x.py"),
                f("backend/app/y.py", "+a = datetime.now()\n")])
    md = render_quick_markdown(r, 431, "abcdef123456")
    assert "<!-- ors-ai-review:quick -->" in md
    assert "🔴 必须采纳（高危）" in md and "🟡 选择采纳（建议关注）" in md
    assert "不阻塞合并" in md


def _run_main(files, out_md, monkeypatch, tmp_path):
    """以 --files-json 模式跑 main()，返回 (exit_code, 评论文件内容)。"""
    import json as _json
    import sys
    import quick_screen
    fj = tmp_path / "files.json"
    fj.write_text(_json.dumps(files, ensure_ascii=False), encoding="utf-8")
    md_path = tmp_path / "quick.md"
    monkeypatch.setattr(sys, "argv", [
        "quick_screen.py", "--files-json", str(fj), "--out-md", str(md_path)])
    code = quick_screen.main()
    body = md_path.read_text(encoding="utf-8") if md_path.exists() else None
    return code, body


def test_warning_only_writes_comment_and_exit_20(monkeypatch, tmp_path):
    """仅命中 warning 也必须落评论（exit 20）——PR #537/#539 静默回归。"""
    code, body = _run_main(
        [f("backend/app/x.py", "+now = datetime.now()\n")],
        "quick.md", monkeypatch, tmp_path)
    assert code == 20
    assert body and "🟡 选择采纳（建议关注）" in body
    assert "时间字段" in body


def test_blocker_exit_10_and_comment(tmp_path, monkeypatch):
    code, body = _run_main(
        [f("backend/app/x.py", '+API_KEY = "sk-abcdef1234567890abcdef"\n')],
        "quick.md", monkeypatch, tmp_path)
    assert code == 10
    assert body and "🔴 必须采纳（高危）" in body


def test_clean_exit_0_no_comment(tmp_path, monkeypatch):
    code, body = _run_main(
        [f("backend/app/x.py", "+value = compute(x)\n")],
        "quick.md", monkeypatch, tmp_path)
    assert code == 0
    assert body is None
