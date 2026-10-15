"""部署链路 schema 同步引擎（backend/scripts/schema_sync.py）的纯逻辑测试。

覆盖重点：
  * 列分类（AUTOMATIC / MANUAL）——决定「哪些能自动补」的安全边界
  * 期望结构生成：MySQL 方言下的 DDL 编译（类型 / NULL / DEFAULT / COMMENT）
  * 结构比对：缺列 / 缺索引 / 表不存在跳过 / 类型不一致只告警 / 库多余列只统计
  * 执行动作：只产出 ADD COLUMN 与 CREATE INDEX，且不碰 MANUAL 项（绝不 DROP / MODIFY）

说明：本用例不依赖真实库，也不调用 load_metadata——测试基建（conftest）为了不连库
把 `app.core.database` 桩成了 MagicMock，在那种环境下导入全部模型会失真；而真实
导入链已在部署目标机上实测通过（54 表 / 617 列）。这里用自建 metadata 覆盖逻辑。
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, String, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import declarative_base

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPT = os.path.join(_BACKEND, "scripts", "schema_sync.py")


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sync = _load_module("ors_schema_sync_under_test", _SCRIPT)
DIALECT = mysql.dialect()


def _demo_metadata():
    """构造一张覆盖各分类的演示表。"""
    base = declarative_base()

    class Demo(base):
        __tablename__ = "demo"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String(64), nullable=True, comment="名称")
        counters = Column(Integer, nullable=False, server_default=text("0"))
        required = Column(String(32), nullable=False)  # NOT NULL 且无默认 → MANUAL

    return base.metadata


# ── 列分类：自动补列的边界 ──────────────────────────────────────────────
def test_classify_nullable_column_is_automatic():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert cols["name"]["classification"] == "AUTOMATIC"


def test_classify_not_null_with_server_default_is_automatic():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert cols["counters"]["classification"] == "AUTOMATIC"


def test_classify_not_null_without_default_is_manual():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert cols["required"]["classification"] == "MANUAL"
    assert cols["required"]["reason"]  # 必须给出「为什么不能自动」


def test_classify_primary_key_is_manual():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert cols["id"]["classification"] == "MANUAL"


# ── 期望结构的 DDL 编译 ─────────────────────────────────────────────────
def test_expected_ddl_keeps_type_nullability_and_comment():
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert "VARCHAR(64)" in cols["name"]["ddl"]
    assert "COMMENT" in cols["name"]["ddl"]
    assert "NOT NULL" in cols["counters"]["ddl"]
    assert "DEFAULT" in cols["counters"]["ddl"]


def test_expected_ddl_excludes_column_name_to_avoid_duplicate_in_alter():
    """列片段不得再带列名：执行侧会自行拼反引号列名，片段里若也含列名就会生成
    重复列名的非法 SQL（ADD COLUMN name name ...）——实测 CreateColumn 输出正是以列名开头。
    """
    md = _demo_metadata()
    cols = sync.build_expected(md, DIALECT)["demo"]["columns"]
    assert not cols["name"]["ddl"].startswith("name")
    assert not cols["counters"]["ddl"].startswith("counters")
    assert cols["counters"]["ddl"].startswith("INTEGER")


def test_expected_lists_indexes():
    md = _demo_metadata()
    # 单独声明索引，避免依赖模型里的 Index 定义
    from sqlalchemy import Index
    Index("ix_demo_name", md.tables["demo"].c.name)
    md2 = sync.build_expected(md, DIALECT)
    assert md2["demo"]["indexes"]["ix_demo_name"]["columns"] == ["name"]
    assert md2["demo"]["indexes"]["ix_demo_name"]["classification"] == "AUTOMATIC"


# ── 结构比对 ────────────────────────────────────────────────────────────
def _expected_demo():
    return sync.build_expected(_demo_metadata(), DIALECT)


def test_diff_reports_missing_column_with_classification():
    actual = {"demo": {"columns": {"id": {"type": "bigint", "nullable": False}},
                       "indexes": {}}}
    result = sync.diff_structure(_expected_demo(), actual)
    missing = {c["column"]: c["classification"] for c in result["missing_columns"]}
    assert missing["name"] == "AUTOMATIC"
    assert missing["required"] == "MANUAL"
    assert "id" not in missing  # 已存在


def test_diff_skips_table_that_does_not_exist():
    """表还没建：启动时 create_all 会整表新建，不当缺失处理。"""
    result = sync.diff_structure(_expected_demo(), {})
    assert result["missing_columns"] == []
    assert result["missing_indexes"] == []


def test_diff_reports_missing_index():
    actual = {"demo": {"columns": {
        "id": {"type": "bigint", "nullable": False},
        "name": {"type": "varchar(64)", "nullable": True},
        "counters": {"type": "int", "nullable": False},
        "required": {"type": "varchar(32)", "nullable": False},
    }, "indexes": {}}}
    md = _demo_metadata()
    from sqlalchemy import Index
    Index("ix_demo_name", md.tables["demo"].c.name)
    result = sync.diff_structure(sync.build_expected(md, DIALECT), actual)
    assert [i["index"] for i in result["missing_indexes"]] == ["ix_demo_name"]


def test_diff_type_mismatch_is_warn_only():
    """类型/长度不一致只告警，绝不自动改列。"""
    actual = {"demo": {"columns": {
        "id": {"type": "bigint", "nullable": False},
        "name": {"type": "varchar(128)", "nullable": True},
        "counters": {"type": "int", "nullable": False},
        "required": {"type": "varchar(32)", "nullable": False},
    }, "indexes": {}}}
    result = sync.diff_structure(_expected_demo(), actual)
    assert any(m["column"] == "name" for m in result["type_mismatch"])
    assert all(c["column"] != "name" for c in result["missing_columns"])


def test_normalize_type_folds_equivalent_spellings():
    """MySQL 的同义写法（INTEGER/int、BOOL/tinyint(1)、INT/INT(11)）不得误报。

    实测背景：首次在 test 环境跑只读校验时，几十条 type_mismatch 全来自这类同义
    写法，真正的差异信号被淹没。
    """
    assert sync.normalize_type("INTEGER") == sync.normalize_type("int")
    assert sync.normalize_type("BOOL") == sync.normalize_type("tinyint(1)")
    assert sync.normalize_type("BIGINT") == sync.normalize_type("bigint(20)")


def test_normalize_type_keeps_real_differences():
    """归一只针对写法差异，真实类型/长度差异必须保留告警。"""
    assert sync.normalize_type("BIGINT") != sync.normalize_type("int")
    assert sync.normalize_type("VARCHAR(64)") != sync.normalize_type("VARCHAR(100)")
    assert sync.normalize_type("ENUM('A','B')") != sync.normalize_type("ENUM('A','B','C')")


def test_diff_reports_extra_columns_without_removal_action():
    """库多出来的列只统计，不产生任何删除动作。"""
    actual = {"demo": {"columns": {
        "id": {"type": "bigint", "nullable": False},
        "name": {"type": "varchar(64)", "nullable": True},
        "counters": {"type": "int", "nullable": False},
        "required": {"type": "varchar(32)", "nullable": False},
        "legacy_col": {"type": "varchar(8)", "nullable": True},
    }, "indexes": {}}}
    result = sync.diff_structure(_expected_demo(), actual)
    assert [c["column"] for c in result["extra_columns"]] == ["legacy_col"]


# ── 执行动作：只加不减 ──────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(sql)


def test_apply_only_adds_automatic_items_and_never_drops():
    result = {
        "missing_columns": [
            {"table": "demo", "column": "name", "ddl": "VARCHAR(64) NULL", "classification": "AUTOMATIC"},
            {"table": "demo", "column": "required", "ddl": "VARCHAR(32) NOT NULL", "classification": "MANUAL"},
        ],
        "missing_indexes": [{"table": "demo", "index": "ix_demo_name", "columns": ["name"], "unique": False}],
    }
    cur = _FakeCursor()
    applied = sync._apply(cur, result, dry_run=False)

    joined = " ".join(cur.statements).upper()
    assert applied == 2  # name + 索引；required 被跳过
    assert "ADD COLUMN `NAME`" in joined
    assert "CREATE INDEX" in joined
    assert "REQUIRED" not in joined
    assert "DROP" not in joined and "MODIFY" not in joined


def test_apply_dry_run_executes_nothing():
    result = {
        "missing_columns": [{"table": "demo", "column": "name", "ddl": "VARCHAR(64) NULL", "classification": "AUTOMATIC"}],
        "missing_indexes": [],
    }
    cur = _FakeCursor()
    assert sync._apply(cur, result, dry_run=True) == 0
    assert cur.statements == []


# ── 连接参数解析 ────────────────────────────────────────────────────────
def test_resolve_db_config_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@1.2.3.4:3307/mydb")
    cfg = sync.resolve_db_config(None)
    assert cfg == {"user": "u", "password": "p", "host": "1.2.3.4", "port": 3307, "database": "mydb"}


def test_resolve_db_config_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('APP_ENV=production\nDATABASE_URL="mysql+pymysql://u:p@db:3306/helpdesk_724"\n',
                        encoding="utf-8")
    cfg = sync.resolve_db_config(str(env_file))
    assert cfg["database"] == "helpdesk_724"


def test_resolve_db_config_requires_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        sync.resolve_db_config(None)


def test_resolve_db_config_rejects_unparsable_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/x.db")
    with pytest.raises(SystemExit):
        sync.resolve_db_config(None)


# ── 人工迁移草稿（--draft-dir）──────────────────────────────────────────
def _draft_result():
    """一条 AUTOMATIC + 一条 MANUAL，用于验证草稿只收录 MANUAL。"""
    return {
        "missing_columns": [
            {"table": "demo", "column": "name", "ddl": "VARCHAR(64) NULL",
             "type": "VARCHAR(64)", "classification": "AUTOMATIC", "reason": ""},
            {"table": "demo", "column": "required", "ddl": "VARCHAR(32) NOT NULL",
             "type": "VARCHAR(32)", "classification": "MANUAL",
             "reason": "NOT NULL 且无 server_default：存量行无值，需人工迁移并回填默认值"},
        ],
        "missing_indexes": [],
    }


def test_draft_written_to_single_sql_file(tmp_path):
    """草稿落点固定为 <draft-dir>/manual_migration.sql（单文件汇总）。"""
    path = sync.write_draft(str(tmp_path), _draft_result(), "helpdesk_test")
    assert path == str(tmp_path / "manual_migration.sql")
    assert (tmp_path / "manual_migration.sql").is_file()


def test_draft_contains_only_manual_items(tmp_path):
    path = sync.write_draft(str(tmp_path), _draft_result(), "helpdesk_test")
    text = Path(path).read_text(encoding="utf-8")
    assert "demo.required" in text
    assert "ADD COLUMN `name`" not in text  # AUTOMATIC 项由脚本自动补，不进草稿


def test_draft_gives_nullable_add_then_backfill_then_not_null(tmp_path):
    """三步模板：先加可空列 → 回填默认值（注释）→ 收紧为 NOT NULL。"""
    path = sync.write_draft(str(tmp_path), _draft_result(), "helpdesk_test")
    text = Path(path).read_text(encoding="utf-8")
    assert "ALTER TABLE `demo` ADD COLUMN `required` VARCHAR(32) NULL;" in text
    assert "-- UPDATE `demo` SET `required` = <默认值> WHERE `required` IS NULL;" in text
    assert "ALTER TABLE `demo` MODIFY COLUMN `required` VARCHAR(32) NOT NULL;" in text


def test_draft_has_no_destructive_statement():
    """草稿可执行行只允许 ALTER TABLE，绝不出现 DROP。"""
    text = sync.build_draft_sql(_draft_result(), "helpdesk_test")
    executable = [ln for ln in text.splitlines()
                  if ln.strip() and not ln.lstrip().startswith("--")]
    assert executable
    assert all(ln.startswith("ALTER TABLE") for ln in executable)
    assert not any("DROP" in ln.upper() for ln in executable)


def test_draft_skipped_when_no_manual_items(tmp_path):
    """没有 MANUAL 项时不落盘，避免留下与当前结构不符的过期草稿。"""
    result = {"missing_columns": [{"table": "demo", "column": "name",
                                  "ddl": "VARCHAR(64) NULL", "type": "VARCHAR(64)",
                                  "classification": "AUTOMATIC", "reason": ""}],
              "missing_indexes": []}
    assert sync.write_draft(str(tmp_path), result, "helpdesk_test") is None
    assert not (tmp_path / "manual_migration.sql").exists()


def test_draft_creates_missing_directory(tmp_path):
    target = tmp_path / "nested" / "drafts"
    path = sync.write_draft(str(target), _draft_result(), "helpdesk_test")
    assert Path(path) == target / "manual_migration.sql"


def test_draft_from_real_diff_carries_database_and_manual_column(tmp_path):
    """走真实 diff：演示表缺的 required 是 MANUAL，须进草稿并带上目标库名。"""
    actual = {"demo": {"columns": {
        "id": {"type": "bigint", "nullable": False},
        "name": {"type": "varchar(64)", "nullable": True},
        "counters": {"type": "int", "nullable": False},
    }, "indexes": {}}}
    result = sync.diff_structure(_expected_demo(), actual)
    path = sync.write_draft(str(tmp_path), result, "helpdesk_724")
    text = Path(path).read_text(encoding="utf-8")
    assert "helpdesk_724" in text
    assert "ALTER TABLE `demo` ADD COLUMN `required` VARCHAR(32) NULL;" in text


# ── main() 接线：--draft-dir 不改变退出码 ────────────────────────────────
class _StubCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return []


class _StubConn:
    def cursor(self):
        return _StubCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _wire_main(monkeypatch, actual):
    """把 main() 的「连库 / 读实际结构」两处 IO 换成替身，只验接线与退出码。"""
    monkeypatch.setattr(sync, "load_metadata", lambda root: _demo_metadata())
    monkeypatch.setattr(sync, "resolve_db_config",
                        lambda env: {"user": "u", "password": "p", "host": "h",
                                     "port": 3306, "database": "helpdesk_test"})
    monkeypatch.setattr(sync, "_connect", lambda cfg: _StubConn())
    monkeypatch.setattr(sync, "load_actual", lambda cur, db: actual)


def _actual_without_required():
    return {"demo": {"columns": {
        "id": {"type": "bigint", "nullable": False},
        "name": {"type": "varchar(64)", "nullable": True},
        "counters": {"type": "int", "nullable": False},
    }, "indexes": {}}}


def test_main_writes_draft_and_keeps_exit_code_two(tmp_path, monkeypatch, capsys):
    """传 --draft-dir：落草稿，但 MANUAL 未解决仍是退出码 2（行为不变）。"""
    _wire_main(monkeypatch, _actual_without_required())
    draft_dir = tmp_path / "drafts"
    monkeypatch.setattr(sys, "argv", ["schema_sync.py", "--draft-dir", str(draft_dir)])

    assert sync.main() == 2
    assert "[草稿] 已写出人工迁移草稿" in capsys.readouterr().out
    text = (draft_dir / "manual_migration.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE `demo` ADD COLUMN `required` VARCHAR(32) NULL;" in text


def test_main_without_draft_dir_writes_nothing(tmp_path, monkeypatch, capsys):
    """不传 --draft-dir：保持零落盘（严格零改动）。"""
    _wire_main(monkeypatch, _actual_without_required())
    monkeypatch.setattr(sys, "argv", ["schema_sync.py", "--draft-dir", ""])

    assert sync.main() == 2
    assert "[草稿]" not in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_main_returns_one_when_draft_unwritable(tmp_path, monkeypatch, capsys):
    """显式要了草稿却写不出（目录位置被文件占住）→ 运行错误退出码 1。"""
    _wire_main(monkeypatch, _actual_without_required())
    blocked = tmp_path / "blocked"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["schema_sync.py", "--draft-dir", str(blocked)])

    assert sync.main() == 1
    assert "[错误] 草稿写入失败" in capsys.readouterr().out
