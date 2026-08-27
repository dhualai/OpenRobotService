"""数据迁移脚本：把旧表 `module_trees`（每产品一行 JSON 整树）拆行灌入新表 `module_tree_nodes`（功能级行模型）。

背景：责任模块树从"整树 JSON + config 回写"演进为"功能级行模型"以支持多人并发编辑。
本脚本将旧 `tree_json` 里的每个功能拆成一行（每功能一行），并写入 `module_tree_nodes`。

要点：
- **幂等**：每次运行会先清空 `module_tree_nodes` 再按旧表重建（新表是派生态，可安全重建）。
- 字段映射：func_name/iface_name 取旧树中的 name（不用 key，见方案 §2.1）；iface_order/func_order 按遍历顺序赋 0..n；keywords/anchor/engineers 原样搬运。
- `(product, func_name)` 唯一：旧树中同产品内功能名不应重复；若重复会跳过后者并告警（避免唯一约束冲突）。

运行方式：cd backend && python scripts/migrate_module_tree_to_nodes.py
"""
import sys
import json
from pathlib import Path

# 允许从 backend 目录直接运行
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def main() -> None:
    from app.core.db import SessionLocal
    from app.models.module_tree import ModuleTree
    from app.models.module_tree_node import ModuleTreeNode

    db = SessionLocal()
    try:
        # 1) 读旧表
        old_rows = db.query(ModuleTree).all()
        print(f"旧表 module_trees 产品数: {len(old_rows)}")

        # 2) 清空新表（派生表，幂等重建）
        deleted = db.query(ModuleTreeNode).delete()
        print(f"清空 module_tree_nodes: {deleted} 行")

        total = 0
        skipped = 0
        seen: set = set()
        for row in old_rows:
            product = row.product
            tree = row.tree_json or {}
            for iface_idx, iface in enumerate(tree.get("interfaces", []) or []):
                iface_name = (iface.get("name") or "").strip()
                for func_idx, fn in enumerate(iface.get("functions", []) or []):
                    func_name = (fn.get("name") or "").strip()
                    if not func_name:
                        continue
                    key = (product, func_name)
                    if key in seen:
                        print(f"  [跳过] 同产品重名功能: {product}/{func_name}")
                        skipped += 1
                        continue
                    seen.add(key)
                    db.add(ModuleTreeNode(
                        product=product,
                        iface_name=iface_name,
                        iface_order=iface_idx,
                        func_name=func_name,
                        func_order=func_idx,
                        keywords=fn.get("keywords") or [],
                        anchor=fn.get("anchor") or "",
                        engineers=fn.get("engineers") or [],
                    ))
                    total += 1

        db.commit()
        print(f"迁移完成：写入 {total} 个功能，跳过重名 {skipped} 个")
    except Exception:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
