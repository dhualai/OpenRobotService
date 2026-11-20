"""责任双树三张表（resp_trees / resp_nodes / resp_node_links）建表命令（幂等，可重复运行）。

背景：`backend/scripts/apply_schema_patch.py` 与 `schema_sync.py` 都只支持「加列/加索引」
列级补丁，**新建表用不了**，所以单独出脚本（同 `create_task_proxy_relation.py` 的做法）。
结构与 `app/models/resp_tree.py` 的 ORM 定义保持一致；app 启动时 `create_all` 也会建，
本脚本是给存量库 / 远端库兜底的显式入口。

用法（在 backend/ 目录下，用项目 venv）：
    ../.venv/Scripts/python.exe scripts/create_resp_trees.py
或指定库（远端演练 / 生产）：
    DATABASE_URL=mysql+pymysql://user:pass@host:port/db \
        ../.venv/Scripts/python.exe scripts/create_resp_trees.py
"""
import os
import re
import sys

import pymysql


def _db_config() -> dict:
    url = os.environ.get("DATABASE_URL")
    if url:
        m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)", url)
        if m:
            return {
                "user": m.group(1), "password": m.group(2),
                "host": m.group(3), "port": int(m.group(4)),
                "database": m.group(5),
            }
    return {"user": "root", "password": "123456", "host": "127.0.0.1",
            "port": 3306, "database": "helpdesk"}


TREES = "resp_trees"
NODES = "resp_nodes"
LINKS = "resp_node_links"

DDL_TREES = f"""
CREATE TABLE IF NOT EXISTS `{TREES}` (
  `id` INT NOT NULL AUTO_INCREMENT COMMENT '主键',
  `kind` VARCHAR(16) NOT NULL COMMENT '树类型：org / skill',
  `name` VARCHAR(128) NOT NULL COMMENT '树名',
  `status` VARCHAR(16) NOT NULL DEFAULT 'draft' COMMENT 'draft / approved',
  `revision` INT NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
  PRIMARY KEY (`id`),
  KEY `ix_resp_trees_kind` (`kind`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='责任双树·树实例'
"""

DDL_NODES = f"""
CREATE TABLE IF NOT EXISTS `{NODES}` (
  `id` INT NOT NULL AUTO_INCREMENT COMMENT '主键',
  `tree_id` INT NOT NULL COMMENT '所属树 resp_trees.id',
  `parent_id` INT NULL COMMENT '父节点；null = 根',
  `name` VARCHAR(255) NOT NULL COMMENT '展示名',
  `description` TEXT NULL COMMENT '业务描述',
  `keywords` JSON NULL COMMENT '关键词数组',
  `anchor` TEXT NULL COMMENT '技能树兼容旧 anchor',
  `owners` JSON NULL COMMENT '编制负责人 user_id 数组',
  `linked_node_id` INT NULL COMMENT '跨树弱关联节点 id（不加 FK）',
  `node_kind` VARCHAR(16) NOT NULL DEFAULT 'leaf' COMMENT 'umbrella / module / leaf / org / func',
  `org_kind` VARCHAR(16) NULL COMMENT 'org 节点细分：company / department',
  `vehicle_models` VARCHAR(255) NULL COMMENT '负责车型',
  `sort_order` INT NOT NULL DEFAULT 0 COMMENT '同级排序',
  `legacy_ref` JSON NULL COMMENT '迁移留痕',
  `is_deleted` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '软删标记',
  `deleted_at` DATETIME NULL COMMENT '软删时间',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
  PRIMARY KEY (`id`),
  KEY `ix_resp_nodes_tree_parent` (`tree_id`, `parent_id`),
  KEY `ix_resp_nodes_linked_node_id` (`linked_node_id`),
  CONSTRAINT `fk_resp_nodes_tree` FOREIGN KEY (`tree_id`)
    REFERENCES `{TREES}` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='责任双树·通用节点'
"""

DDL_LINKS = f"""
CREATE TABLE IF NOT EXISTS `{LINKS}` (
  `id` INT NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id` VARCHAR(50) NOT NULL COMMENT '工程师 users.id',
  `node_id` INT NOT NULL COMMENT '挂载节点 resp_nodes.id',
  `link_type` VARCHAR(16) NOT NULL DEFAULT 'solid' COMMENT 'solid / dotted',
  `role` VARCHAR(16) NOT NULL DEFAULT 'member' COMMENT 'leader / owner / specialist / member',
  `source` VARCHAR(32) NOT NULL DEFAULT 'manual' COMMENT 'manual / synced_from_dept / inferred / migrated_from_engineers',
  `confidence` FLOAT NULL COMMENT '推断置信度',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_resp_link_user_node` (`user_id`, `node_id`),
  KEY `ix_resp_node_links_node_id` (`node_id`),
  CONSTRAINT `fk_resp_links_node` FOREIGN KEY (`node_id`)
    REFERENCES `{NODES}` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='责任双树·人挂载'
"""

# 结构校验：建表后必须存在的列（防「表已存在但是旧结构」）
REQUIRED = {
    TREES: ({"id", "kind", "name", "status", "revision", "created_at", "updated_at"},
            {"ix_resp_trees_kind"}),
    NODES: ({"id", "tree_id", "parent_id", "name", "description", "keywords", "anchor",
             "owners", "linked_node_id", "node_kind", "org_kind", "vehicle_models",
             "sort_order", "legacy_ref", "is_deleted", "deleted_at",
             "created_at", "updated_at"},
            {"ix_resp_nodes_tree_parent", "fk_resp_nodes_tree"}),
    LINKS: ({"id", "user_id", "node_id", "link_type", "role", "source", "confidence",
             "created_at", "updated_at"},
            {"uq_resp_link_user_node", "ix_resp_node_links_node_id", "fk_resp_links_node"}),
}


def _verify(cur, table: str) -> bool:
    """列与索引齐不齐（索引集里混了唯一键与外键名，SHOW INDEX 都能给出）。"""
    want_cols, want_idx = REQUIRED[table]
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    missing_cols = want_cols - {row[0] for row in cur.fetchall()}

    cur.execute(f"SHOW INDEX FROM `{table}`")
    have_idx = {row[2] for row in cur.fetchall()}
    cur.execute(
        "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
    have_idx |= {row[0] for row in cur.fetchall()}
    missing_idx = want_idx - have_idx

    if missing_cols:
        print(f"[ERR] {table} 缺列：{sorted(missing_cols)}")
    if missing_idx:
        print(f"[ERR] {table} 缺索引/约束：{sorted(missing_idx)}")
    return not missing_cols and not missing_idx


def main() -> int:
    cfg = _db_config()
    conn = pymysql.connect(
        host=cfg["host"], user=cfg["user"], password=cfg["password"],
        port=cfg["port"], database=cfg["database"], charset="utf8mb4",
    )
    cur = conn.cursor()
    ok = True
    try:
        # 顺序有依赖：resp_nodes 引用 resp_trees，resp_node_links 引用 resp_nodes
        for table, ddl in ((TREES, DDL_TREES), (NODES, DDL_NODES), (LINKS, DDL_LINKS)):
            cur.execute(f"SHOW TABLES LIKE '{table}'")
            if cur.fetchone():
                print(f"[SKIP] {table} 已存在")
            else:
                cur.execute(ddl)
                print(f"[ADD] {table} 创建成功")
        conn.commit()

        for table in (TREES, NODES, LINKS):
            if not _verify(cur, table):
                ok = False
    finally:
        conn.close()

    if not ok:
        print("完成：结构校验未通过，请人工比对 app/models/resp_tree.py 的 ORM 定义")
        return 1
    print("完成：责任双树三张表已就绪，结构校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
