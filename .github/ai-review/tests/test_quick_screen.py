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


def test_deleted_lines_not_checked():
    """删除行不检查（误报抑制）。"""
    r = screen([f("backend/app/x.py", "-API_KEY = \"sk-abcdef1234567890abcdef\"\n")])
    assert r["blockers"] == []


def test_dedup_same_file_same_label():
    r = screen([f("backend/app/x.py",
                  "+a = datetime.now()\n+b = datetime.now()\n")])
    hits = [it for it in r["warnings"] if it["label"] == "时间字段"]
    assert len(hits) == 1


def test_render_markdown_has_marker_and_sections():
    r = screen([f("backend/alembic/versions/x.py"),
                f("backend/app/y.py", "+a = datetime.now()\n")])
    md = render_quick_markdown(r, 431, "abcdef123456")
    assert "<!-- ors-ai-review:quick -->" in md
    assert "**高危**" in md and "**建议关注**" in md
    assert "不阻塞合并" in md
