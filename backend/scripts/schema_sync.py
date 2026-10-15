"""数据库结构一致性校验 / 自动补列（部署链路专用）。

背景
----
部署链路原先只做「备份 → 传代码 → 重启 → 健康检查」，完全不碰数据库；而 alembic
链与存量库状态不一致（部分列由 create_all / 手工补上，链上仍记为未应用），
`alembic upgrade head` 会因 Duplicate column 中断（如 users.company_id），因此这里
绕开 alembic，直接按「代码模型定义」对齐存量库。

本脚本是 A（只读门禁）与 B（自动补列）共用的引擎：
  * 期望结构：合成一个空 `app` 包再 import app.models，拿到 Base.metadata。
    合成空包可避免执行 app/__init__.py（不拉起 FastAPI 路由与调度线程）。
  * 比对：只读 information_schema，得到「模型有、库没有」的列与索引。
  * 分类：
      - AUTOMATIC：可为 NULL 或带 server_default，存量行有值可依，可安全补。
      - MANUAL   ：NOT NULL 且无 server_default —— 存量表加该列会失败或写入隐式
                   默认值，必须人工迁移（含默认值回填），不自动执行。
  * 执行：--execute 时只执行 AUTOMATIC 的 ADD COLUMN / CREATE INDEX。
 * 草稿：--draft-dir 时把 MANUAL 项写成人工迁移草稿（供人审阅执行），
        不改变自动行为，也不影响退出码。

安全边界（硬约束，改动前请先读）
--------------------------------
1. 只加不减：绝不 DROP / MODIFY 任何列或索引。「库多出来的列」只统计不处理——
   废弃列的删除带业务收尾，必须人工（见 backend/scripts/apply_schema_patch.py 的 DROPS）。
2. 只处理「列名缺失」：类型 / 长度不一致只 WARN 不自动改（自动改列类型风险极高）。
3. 幂等：每次都按 information_schema 现况判断，重复执行无副作用。
4. 并发：--execute 前取 MySQL 命名锁，避免人工与 CI 同时 ALTER。
5. 凭据不落日志：只打印主机与库名，绝不回显 DATABASE_URL / 密码。
6. 客户端默认值（Column(default=...)）是 Python 侧的，不进 DDL，故不作为
   「可安全补」的依据。

退出码
------
  0  无缺失（或 --execute 已把 AUTOMATIC 全部补齐）
  2  存在未解决的缺失（check 模式：发现缺失即返回；execute 模式：剩余 MANUAL）
  1  运行错误（连库失败、DDL 失败、草稿写入失败等）

用法
----
  # A：只读校验（默认），有缺失返回 2
  python scripts/schema_sync.py --backend-root /data/apps/OpenRobotService/backend

  # 导出期望结构快照（供审阅 / 存档，不连库）
  python scripts/schema_sync.py --snapshot-out /tmp/schema_expected.json

  # B：自动补列（只补 AUTOMATIC，且先干跑确认）
  python scripts/schema_sync.py --execute --dry-run
  python scripts/schema_sync.py --execute

  # C：把 MANUAL 项写成人工迁移草稿（不改动 check/sync 的行为与退出码）
  python scripts/schema_sync.py --draft-dir /tmp/ors_schema_drafts
"""
import argparse
import json
import os
import re
import sys
import types
from pathlib import Path

# 与 app/core/config.py 的解析口径一致（mysql+pymysql://user:pass@host:port/db）
_DB_URL_RE = re.compile(r"mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)")

# 执行期的安全护栏
_LOCK_NAME = "ors_schema_sync"
_LOCK_TIMEOUT_SECONDS = 30
_LOCK_WAIT_TIMEOUT_SQL = "SET SESSION lock_wait_timeout = 15"

# 人工迁移草稿的文件名（--draft-dir 时生成）
_DRAFT_FILENAME = "manual_migration.sql"


# --------------------------------------------------------------------------- #
# 期望结构：从代码模型生成
# --------------------------------------------------------------------------- #
def load_metadata(backend_root: Path):
    """import app.models 并返回 Base.metadata。

    合成空 `app` 包（types.ModuleType + __path__）塞进 sys.modules，使
    `import app.models` 走真实文件而不执行 app/__init__.py——后者会拉起
    FastAPI 应用与后台调度线程，部署期间不需要也不应该启动。
    """
    root = str(backend_root)
    if root not in sys.path:
        sys.path.insert(0, root)

    if "app" not in sys.modules:
        pkg = types.ModuleType("app")
        pkg.__path__ = [os.path.join(root, "app")]
        sys.modules["app"] = pkg

    from app.models import Base  # noqa: E402  （须在 sys.path 就绪后导入）

    return Base.metadata


def _column_ddl(column, dialect) -> str:
    """把 ORM 列编译成 MySQL 的列定义片段（类型 + NULL/NOT NULL + DEFAULT + COMMENT）。

    注意：CreateColumn 的输出以「列名」开头（如 name VARCHAR(64) ...），而执行侧会
    自行拼「ADD COLUMN + 反引号列名」，因此这里必须剥掉列名，否则会生成重复列名的
    非法 SQL（ADD COLUMN name name VARCHAR(64)）。
    """
    from sqlalchemy.schema import CreateColumn

    full = str(CreateColumn(column).compile(dialect=dialect)).strip()
    name = column.name
    for prefix in (f"`{name}`", f'"{name}"', name):
        # 名称后必须紧跟空白，避免把 `a` 误剥成 `abc ...` 的前缀
        if full.startswith(prefix) and len(full) > len(prefix) and full[len(prefix)] in " \t":
            return full[len(prefix):].lstrip()
    return full


def _column_type(column, dialect) -> str:
    """列的类型串（如 INTEGER / VARCHAR(64) / BOOL / ENUM('A','B')）。

    直接取列类型编译结果，不从 DDL 里反推——后者遇到 DECIMAL(10, 2) 这类带空格的
    类型会截断。
    """
    try:
        return str(column.type.compile(dialect=dialect))
    except Exception:
        return str(column.type)


def _classify(column) -> str:
    """列的可补性分类：见模块头的 AUTOMATIC / MANUAL 说明。

    主键 / 自增列缺失属表结构异常（且 ALTER 加 AUTO_INCREMENT 列有额外约束），
    一律交人工，不自动补。
    """
    if column.primary_key or column.autoincrement is True:
        return "MANUAL"
    if column.nullable or column.server_default is not None:
        return "AUTOMATIC"
    return "MANUAL"


def build_expected(metadata, dialect) -> dict:
    """生成期望结构：{table: {"columns": {...}, "indexes": {...}}}。"""
    expected = {}
    for table in metadata.sorted_tables:
        columns = {}
        for column in table.columns:
            columns[column.name] = {
                "ddl": _column_ddl(column, dialect),
                "type": _column_type(column, dialect),
                "classification": _classify(column),
                "reason": ""
                if _classify(column) == "AUTOMATIC"
                else "NOT NULL 且无 server_default：存量行无值，需人工迁移并回填默认值",
            }
        indexes = {}
        for index in table.indexes:
            if index.name is None:
                continue
            indexes[index.name] = {
                "columns": [c.name for c in index.columns],
                "unique": bool(index.unique),
                "classification": "AUTOMATIC",
                "reason": "",
            }
        expected[table.name] = {"columns": columns, "indexes": indexes}
    return expected


# --------------------------------------------------------------------------- #
# 实际结构：只读 information_schema
# --------------------------------------------------------------------------- #
def load_actual(cur, database: str) -> dict:
    """读取存量库的表 / 列 / 索引 现况。"""
    actual: dict = {}

    cur.execute(
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s",
        (database,),
    )
    for table, column, column_type, is_nullable in cur.fetchall():
        entry = actual.setdefault(table, {"columns": {}, "indexes": {}})
        entry["columns"][column] = {
            "type": (column_type or "").lower(),
            "nullable": (is_nullable or "").upper() == "YES",
        }

    cur.execute(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, NON_UNIQUE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
        (database,),
    )
    for table, index, column, _seq, non_unique in cur.fetchall():
        entry = actual.setdefault(table, {"columns": {}, "indexes": {}})
        idx = entry.setdefault("indexes", {}).setdefault(
            index, {"columns": [], "unique": not bool(non_unique)}
        )
        idx["columns"].append(column)

    return actual


def diff_structure(expected: dict, actual: dict) -> dict:
    """比对期望与实际，得到缺失列 / 缺失索引 / 类型不一致 / 库多余列。"""
    result = {"missing_columns": [], "missing_indexes": [], "type_mismatch": [], "extra_columns": []}

    for table, exp in expected.items():
        act = actual.get(table)
        if act is None:
            # 表尚不存在：应用启动 create_all 会按模型整表新建，无需补列
            continue

        for name, meta in exp["columns"].items():
            if name not in act["columns"]:
                result["missing_columns"].append(
                    {"table": table, "column": name, "ddl": meta["ddl"], "type": meta["type"],
                     "classification": meta["classification"], "reason": meta["reason"]}
                )
            else:
                actual_type = act["columns"][name].get("type", "")
                # 取不到实际类型时不做比较，避免把「读不到」误报成「类型不一致」
                if actual_type and normalize_type(meta["type"]) != normalize_type(actual_type):
                    result["type_mismatch"].append(
                        {"table": table, "column": name, "expected": meta["type"],
                         "actual": actual_type}
                    )

        for name, meta in exp["indexes"].items():
            if name not in act["indexes"]:
                result["missing_indexes"].append(
                    {"table": table, "index": name, "columns": meta["columns"],
                     "unique": meta["unique"]}
                )

        for name in act["columns"]:
            if name not in exp["columns"]:
                result["extra_columns"].append({"table": table, "column": name})

    return result


_TYPE_ALIASES = {
    "integer": "int",
    "boolean": "bool",
    "tinyint(1)": "bool",  # MySQL 以 tinyint(1) 表示 BOOL
    "character varying": "varchar",
}
_INT_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)(\s+unsigned)?$")


def normalize_type(raw: str) -> str:
    """规范化类型串，消除大小写 / 别名 / 显示宽度差异。

    只归并「同一类型的不同写法」，不做任何放宽——BIGINT 与 INT、VARCHAR(64) 与
    VARCHAR(100) 仍视为不一致并如实告警。
    """
    text = (raw or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = _TYPE_ALIASES.get(text, text)
    matched = _INT_WIDTH_RE.match(text)
    if matched:
        text = matched.group(1) + (matched.group(2) or "")
    return text


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def _emit_diff(result: dict, dry_run: bool) -> None:
    for item in result["missing_columns"]:
        tag = "ADD" if item["classification"] == "AUTOMATIC" else "MANUAL"
        if item["classification"] == "AUTOMATIC" and dry_run:
            tag = "ADD(dryrun)"
        print(f"[{tag}] {item['table']}.{item['column']} {item['ddl']}")
        if item["reason"]:
            print(f"       └─ {item['reason']}")
    for item in result["missing_indexes"]:
        print(f"[ADD] 索引 {item['index']} ON {item['table']}({', '.join(item['columns'])})"
              f"{' UNIQUE' if item['unique'] else ''}")
    for item in result["type_mismatch"]:
        print(f"[WARN] {item['table']}.{item['column']} 类型与模型不一致（不自动修改）："
              f"模型={item['expected']} 库={item['actual']}")
    if result["extra_columns"]:
        names = ", ".join(f"{c['table']}.{c['column']}" for c in result["extra_columns"][:20])
        more = "" if len(result["extra_columns"]) <= 20 else f" 等 {len(result['extra_columns'])} 个"
        print(f"[INFO] 库中存在模型已移除的列（不处理，需人工确认后按迁移删除）：{names}{more}")


def _emit_counts(result: dict, applied: int, database: str) -> None:
    auto = sum(1 for c in result["missing_columns"] if c["classification"] == "AUTOMATIC")
    manual = len(result["missing_columns"]) - auto
    # 固定格式，供 deploy/deploy.py 抓取
    print(f"SCHEMA_DATABASE={database}")
    print(f"SCHEMA_MISSING_COLUMNS={len(result['missing_columns'])}")
    print(f"SCHEMA_MISSING_INDEXES={len(result['missing_indexes'])}")
    print(f"SCHEMA_AUTOMATIC={auto}")
    print(f"SCHEMA_MANUAL={manual}")
    print(f"SCHEMA_APPLIED={applied}")


# --------------------------------------------------------------------------- #
# 人工迁移草稿（MANUAL 项）
# --------------------------------------------------------------------------- #
def build_draft_sql(result: dict, database: str) -> str:
    """把 MANUAL 缺失列渲染成人工迁移草稿文本。

    只生成文本，不连库、不执行；每条对应 _emit_diff 里的一个 [MANUAL] 项。
    采用「先加可空列 → 回填默认值 → 收紧为 NOT NULL」三步，避免直接给存量表加
    NOT NULL 列（会失败，或按 MySQL 隐式默认值写满存量行）。

    注意本草稿是给人工执行的，不受脚本自身「只加不减」约束的限制：第 3 步的
    MODIFY 是人工收紧列约束的必经动作，脚本永远不会自动执行它。
    """
    lines = [
        "-- 数据库结构同步 · 人工迁移草稿（由 backend/scripts/schema_sync.py 生成）",
        f"-- 目标库：{database}",
        "-- 适用：以下列在代码模型中为 NOT NULL 且无 server_default，存量行无值，",
        "--       无法安全自动补列（脚本自身只自动执行 AUTOMATIC 的 ADD COLUMN / CREATE INDEX）。",
        "-- 本草稿供人工审阅后执行；请先把 <默认值> 按业务语义替换掉再回填。",
        "-- 若缺失项为新增主键 / 自增列，请勿直接套用下面的三步模板，需人工设计迁移。",
        "",
    ]
    for item in result["missing_columns"]:
        if item["classification"] != "MANUAL":
            continue
        table, column, ddl = item["table"], item["column"], item["ddl"]
        lines += [
            "-- " + "-" * 72,
            f"-- {table}.{column}  {ddl}",
            f"-- 原因：{item['reason'] or '需人工迁移'}",
            "-- " + "-" * 72,
            f"ALTER TABLE `{table}` ADD COLUMN `{column}` {item['type']} NULL;",
            f"-- UPDATE `{table}` SET `{column}` = <默认值> WHERE `{column}` IS NULL;",
            f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` {ddl};",
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_draft(draft_dir: str, result: dict, database: str) -> str | None:
    """把 MANUAL 项草稿写到 <draft_dir>/manual_migration.sql，返回落盘路径。

    没有 MANUAL 项时不落盘并返回 None——避免留下一份与当前结构不符的过期草稿。
    """
    if not any(c["classification"] == "MANUAL" for c in result["missing_columns"]):
        return None
    target = Path(draft_dir) / _DRAFT_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_draft_sql(result, database), encoding="utf-8")
    return str(target)


# --------------------------------------------------------------------------- #
# 连接与执行
# --------------------------------------------------------------------------- #
def resolve_db_config(env_file: str | None) -> dict:
    """取连接参数：环境变量 DATABASE_URL 优先，其次 --env-file 里的同名键。"""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url and env_file:
        path = Path(env_file)
        if not path.is_file():
            raise SystemExit(f"[错误] 找不到 env 文件：{path}")
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise SystemExit("[错误] 未提供 DATABASE_URL（环境变量或 --env-file）")
    matched = _DB_URL_RE.match(url)
    if not matched:
        raise SystemExit("[错误] DATABASE_URL 格式无法解析（期望 mysql+pymysql://user:pass@host:port/db）")
    return {
        "user": matched.group(1),
        "password": matched.group(2),
        "host": matched.group(3),
        "port": int(matched.group(4)),
        "database": matched.group(5),
    }


def _connect(cfg: dict):
    import pymysql

    return pymysql.connect(
        host=cfg["host"], user=cfg["user"], password=cfg["password"],
        port=cfg["port"], database=cfg["database"], charset="utf8mb4",
        autocommit=False,
    )


def _apply(cur, result: dict, dry_run: bool) -> int:
    """执行 AUTOMATIC 的补列 / 建索引，返回执行条数。"""
    applied = 0
    for item in result["missing_columns"]:
        if item["classification"] != "AUTOMATIC":
            continue
        sql = f"ALTER TABLE `{item['table']}` ADD COLUMN `{item['column']}` {item['ddl']}"
        if dry_run:
            print(f"[dryrun] {sql}")
            continue
        cur.execute(sql)
        print(f"[DONE] {sql}")
        applied += 1

    for item in result["missing_indexes"]:
        cols = ", ".join(f"`{c}`" for c in item["columns"])
        unique = "UNIQUE " if item["unique"] else ""
        sql = f"CREATE {unique}INDEX `{item['index']}` ON `{item['table']}` ({cols})"
        if dry_run:
            print(f"[dryrun] {sql}")
            continue
        cur.execute(sql)
        print(f"[DONE] {sql}")
        applied += 1
    return applied


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="数据库结构一致性校验 / 自动补列")
    parser.add_argument("--backend-root", default=str(Path(__file__).resolve().parents[1]),
                        help="后端代码根目录（含 app/ 包），默认取脚本的上级目录。")
    parser.add_argument("--env-file", default="",
                        help="从此文件读取 DATABASE_URL（如 backend/.env）；"
                             "环境变量 DATABASE_URL 优先。")
    parser.add_argument("--execute", action="store_true",
                        help="真正执行 AUTOMATIC 的补列 / 建索引；缺省为只读校验。")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="配合 --execute：只打印将执行的 SQL，不落库。")
    parser.add_argument("--draft-dir", dest="draft_dir", default="",
                        help="把 MANUAL 缺失项的补列 SQL 草稿写入该目录下的 "
                             "manual_migration.sql（供人工迁移）；缺省不生成。")
    parser.add_argument("--snapshot-out", dest="snapshot_out", default="",
                        help="导出期望结构快照 JSON 后退出（不连库，供审阅 / 存档）。")
    args = parser.parse_args()

    try:
        from sqlalchemy.dialects import mysql
    except ImportError as e:
        print(f"[错误] 缺少 SQLAlchemy（请用后端 venv 执行）：{e}")
        return 1

    dialect = mysql.dialect()
    metadata = load_metadata(Path(args.backend_root))
    expected = build_expected(metadata, dialect)

    if args.snapshot_out:
        payload = {"backend_root": str(Path(args.backend_root).resolve()), "tables": expected}
        Path(args.snapshot_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tables = len(expected)
        cols = sum(len(t["columns"]) for t in expected.values())
        print(f"已导出期望结构：{args.snapshot_out}（{tables} 表 / {cols} 列）")
        return 0

    cfg = resolve_db_config(args.env_file or None)
    print(f"目标库：{cfg['host']}:{cfg['port']}/{cfg['database']}")

    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            actual = load_actual(cur, cfg["database"])
            result = diff_structure(expected, actual)
            _emit_diff(result, dry_run=args.dry_run or not args.execute)

            if args.draft_dir:
                try:
                    draft = write_draft(args.draft_dir, result, cfg["database"])
                except OSError as e:
                    # 人工显式要了草稿却写不出：如实报错，不静默放过
                    print(f"[错误] 草稿写入失败：{e}")
                    return 1
                print(f"[草稿] 已写出人工迁移草稿：{draft}" if draft
                      else "[草稿] 无 MANUAL 项，未生成草稿")

            applied = 0
            if args.execute:
                if not args.dry_run:
                    cur.execute(f"SELECT GET_LOCK(%s, {_LOCK_TIMEOUT_SECONDS})", (_LOCK_NAME,))
                    if cur.fetchone()[0] != 1:
                        print(f"[错误] 未取到命名锁 {_LOCK_NAME}（可能有人正在同步），本次跳过执行")
                        return 1
                    cur.execute(_LOCK_WAIT_TIMEOUT_SQL)
                try:
                    applied = _apply(cur, result, dry_run=args.dry_run)
                    if not args.dry_run:
                        conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    if not args.dry_run:
                        try:
                            cur.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_NAME,))
                        except Exception:
                            pass

            _emit_counts(result, applied, cfg["database"])

            # 只有「缺列」会直接导致业务侧 1054，因此只有缺列会中止部署；
            # 缺索引只影响性能，告警即可（--execute 时会一并创建），避免索引命名
            # 差异之类的误报拦住发布。
            unresolved = sum(
                1 for c in result["missing_columns"]
                if not args.execute or c["classification"] == "MANUAL"
            )
            if unresolved:
                if not args.execute:
                    print("[结果] 库结构落后于代码：请在部署前完成迁移 "
                          "（AUTOMATIC 项可用 --execute 自动补，MANUAL 项需人工）")
                else:
                    print("[结果] 仍有 MANUAL 项未解决：需人工迁移后重新部署")
                return 2
            if result["missing_indexes"]:
                print(f"[结果] 库结构可用；有 {len(result['missing_indexes'])} 个索引缺失"
                      "（不影响本次部署，建议后续补齐）")
                return 0
            print("[结果] 库结构与代码模型一致")
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
