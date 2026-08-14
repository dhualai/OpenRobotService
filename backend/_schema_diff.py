# 一次性诊断脚本：对比 SQLAlchemy 模型 metadata 与本机 MySQL 实际 schema，
# 找出"表存在但缺列"的情况（create_all 不会修复这类差异）。
# 用法：.venv/Scripts/python.exe _schema_diff.py [--apply]
import sys

from sqlalchemy import create_engine, text
from app.core.config import settings
from app.models import Base  # 导入即注册全部模型

db = settings.DB_CONFIG
url = f"mysql+pymysql://{db['user']}:{db['password']}@{db['host']}:{db['port']}/{db['database']}?charset=utf8mb4"
engine = create_engine(url)

with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
        "EXTRA, COLUMN_COMMENT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = :db"
    ), {"db": db["database"]}).fetchall()

actual = {}
for t, c, ctype, nullable, default, extra, comment in rows:
    actual.setdefault(t, {})[c] = {
        "type": ctype, "nullable": nullable, "default": default,
        "extra": extra, "comment": comment,
    }

missing_tables = []
missing_cols = []
for table_name, table in Base.metadata.tables.items():
    if table_name not in actual:
        missing_tables.append(table_name)
        continue
    for col in table.columns:
        if col.name not in actual[table_name]:
            missing_cols.append((table_name, col.name, str(col.type), col.nullable))

print(f"== 缺失的表（create_all 会自动建）: {len(missing_tables)}")
for t in missing_tables:
    print(f"  TABLE {t}")

print(f"== 表存在但缺列: {len(missing_cols)}")
statements = []
for t, c, ctype, nullable in missing_cols:
    print(f"  {t}.{c} ({ctype}, nullable={nullable})")
    stmt = f"ALTER TABLE `{t}` ADD COLUMN `{c}` {ctype}" + ("" if nullable else " NOT NULL")
    statements.append(stmt)

if "--apply" in sys.argv and statements:
    with engine.begin() as conn:
        for s in statements:
            print("APPLY:", s)
            conn.execute(text(s))
    print(f"== 已应用 {len(statements)} 条 ALTER")
elif statements:
    print("（仅诊断。加 --apply 实际执行 ALTER）")
