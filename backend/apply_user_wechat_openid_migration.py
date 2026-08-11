"""幂等迁移：为 users 表增加 wechat_openid 列（讨论区消息转发到微信用）。

规避 alembic 多 head 问题，直接用原生连接幂等加列：
- 列已存在则跳过；
- 列不存在则 ALTER TABLE ADD COLUMN + 建索引。

可重复执行（生产/测试/本地均安全）。
用法：
    cd backend && python apply_user_wechat_openid_migration.py
"""
from sqlalchemy import create_engine, text

from app.core.config import settings


def get_engine():
    cfg = settings.DB_CONFIG
    url = (
        f"mysql+pymysql://{cfg['user']}:{cfg['password']}@{cfg['host']}:{cfg['port']}"
        f"/{cfg['database']}?charset=utf8mb4"
    )
    return create_engine(url, future=True)


def column_exists(engine, table: str, column: str) -> bool:
    sql = text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = :db AND table_name = :table AND column_name = :column"
    )
    with engine.connect() as conn:
        return conn.execute(
            sql, {"db": settings.DB_CONFIG["database"], "table": table, "column": column}
        ).scalar() > 0


def index_exists(engine, table: str, index: str) -> bool:
    sql = text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = :db AND table_name = :table AND index_name = :index"
    )
    with engine.connect() as conn:
        return conn.execute(
            sql, {"db": settings.DB_CONFIG["database"], "table": table, "index": index}
        ).scalar() > 0


def main():
    engine = get_engine()
    table = "users"
    column = "wechat_openid"
    index = "ix_users_wechat_openid"

    if column_exists(engine, table, column):
        print(f"[skip] {table}.{column} 已存在，无需迁移")
    else:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE `users` ADD COLUMN `wechat_openid` "
                    "VARCHAR(128) NULL COMMENT '绑定的微信open_id（讨论区消息转发到微信用）'"
                )
            )
        print(f"[ok] {table}.{column} 已添加")

    if not index_exists(engine, table, index):
        with engine.begin() as conn:
            conn.execute(text(f"CREATE INDEX `{index}` ON `{table}` (`{column}`)"))
        print(f"[ok] 索引 {index} 已创建")
    else:
        print(f"[skip] 索引 {index} 已存在")


if __name__ == "__main__":
    main()
