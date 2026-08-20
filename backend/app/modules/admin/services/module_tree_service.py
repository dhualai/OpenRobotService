"""module_tree（产品→界面→功能 责任树）业务服务层。

职责：
- 读写 DB 中的 product→tree（每个产品一行，JSON 存接口树）
- 保存后将**所有产品**的树导出覆盖到 AI Assigner 的 config.yaml（作为启动快照）
- 导出后通知 AI 服务热更新，让运行中派单流水线感知新配置
"""
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from app.core.database import db_manager
from app.models.module_tree import ModuleTree

logger = logging.getLogger(__name__)

# config.yaml 相对项目根的路径（本文件位于 backend/app/modules/admin/services/）
# parents[5] = 项目根（services->admin->modules->app->backend->root）
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_ASSIGNER_CONFIG_PATH = _PROJECT_ROOT / "ai" / "agents" / "AiDiagnosisPlatform" / "assigner" / "config" / "config.yaml"


def _get_db():
    return db_manager.get_db()


def get_all_trees() -> Dict[str, Any]:
    """返回 {产品: {"interfaces": [...]}}，供前端展示 / 导出 config。"""
    db = _get_db()
    try:
        rows = db.query(ModuleTree).all()
        result: Dict[str, Any] = {}
        for row in rows:
            result[row.product] = row.tree_json or {"interfaces": []}
        return result
    finally:
        db.close()


def get_product_tree(product: str) -> Optional[Dict[str, Any]]:
    """返回单产品的接口树（该产品名下的 interfaces 结构）。"""
    db = _get_db()
    try:
        row = db.query(ModuleTree).filter(ModuleTree.product == product).first()
        return row.tree_json if row else None
    finally:
        db.close()


def upsert_product_tree(product: str, tree: Dict[str, Any]) -> bool:
    """写入或更新某个产品的接口树（tree 为 {"interfaces": [...]}）。"""
    db = _get_db()
    try:
        row = db.query(ModuleTree).filter(ModuleTree.product == product).first()
        if row:
            row.tree_json = tree
        else:
            db.add(ModuleTree(product=product, tree_json=tree))
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("upsert module_tree 失败: %s", product)
        return False
    finally:
        db.close()


def replace_all_trees(trees: Dict[str, Any]) -> bool:
    """整体覆盖所有产品的树（前端整树保存用）。"""
    db = _get_db()
    try:
        # 删除现有全部
        db.query(ModuleTree).delete()
        for product, tree in trees.items():
            db.add(ModuleTree(product=product, tree_json=tree or {"interfaces": []}))
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("replace_all_trees 失败")
        return False
    finally:
        db.close()


def find_function(trees: Dict[str, Any], product: str, iface_key: str, func_key: str):
    """在树中定位某个功能节点，返回 (tree, iface, fn, iface_idx, fn_idx)，未找到返回 None。"""
    tree = (trees or {}).get(product)
    if not tree:
        return None
    for iface_idx, iface in enumerate(tree.get("interfaces", []) or []):
        if str(iface.get("key")) != str(iface_key):
            continue
        for fn_idx, fn in enumerate(iface.get("functions", []) or []):
            if str(fn.get("key")) == str(func_key) or str(fn.get("name")) == str(func_key):
                return tree, iface, fn, iface_idx, fn_idx
    return None


def apply_function_change(product: str, iface_key: str, func_key: str, new_json: Dict[str, Any]) -> bool:
    """审批通过后，把某功能节点的修改应用到 DB，并导出 config + 同步用户画像。

    只替换该功能节点的字段（name/keywords/anchor/engineers），不动其它。
    返回是否成功。
    """
    db = _get_db()
    try:
        row = db.query(ModuleTree).filter(ModuleTree.product == product).first()
        if not row:
            logger.error("apply_function_change: 产品不存在 %s", product)
            return False
        tree = row.tree_json or {"interfaces": []}
        found = find_function({product: tree}, product, iface_key, func_key)
        if not found:
            logger.error("apply_function_change: 功能定位失败 %s/%s/%s", product, iface_key, func_key)
            return False
        _, iface, fn, iface_idx, fn_idx = found
        # 合并新值：仅覆盖给定的键，保留未提及字段
        merged = dict(fn)
        merged.update(new_json or {})
        tree["interfaces"][iface_idx]["functions"][fn_idx] = merged
        row.tree_json = tree
        db.commit()

        # 导出 config + 同步用户画像（尽力而为）
        all_trees = get_all_trees()
        export_to_config(all_trees)
        sync_to_user_profiles(all_trees)
        return True
    except Exception:
        db.rollback()
        logger.exception("apply_function_change 失败 %s/%s/%s", product, iface_key, func_key)
        return False
    finally:
        db.close()



def export_to_config(trees: Optional[Dict[str, Any]] = None) -> bool:
    """把 DB 中的产品树导出覆盖到 config.yaml 的 module_tree 块。

    采用"保留文件头注释 + 只替换 module_tree 数据段"的策略：
    读取现有 config.yaml，替换 module_tree 块的 YAML 文本，保留其它一切。
    """
    trees = trees if trees is not None else get_all_trees()
    # 组装成 settings.py 期望的结构：{产品: {"interfaces": [...]}}
    module_tree_block = {}
    for product, tree in trees.items():
        if tree and "interfaces" in tree:
            module_tree_block[product] = tree
        else:
            module_tree_block[product] = {"interfaces": []}

    path = _ASSIGNER_CONFIG_PATH
    if not path.exists():
        logger.error("config.yaml 不存在: %s", path)
        return False

    try:
        raw = path.read_text(encoding="utf-8")
        # 用 yaml 序列化新的 module_tree 块
        import yaml
        block_yaml = yaml.safe_dump(
            {"module_tree": module_tree_block},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        )

        # 定位旧的 module_tree 块边界（从 "module_tree:" 行到下一个顶级键/注释分节）
        lines = raw.splitlines(keepends=True)
        start_idx = None
        for i, ln in enumerate(lines):
            if ln.rstrip() == "module_tree:" or ln.rstrip().startswith("module_tree: "):
                start_idx = i
                break
        if start_idx is None:
            # 没有 module_tree：追加到文件头注释后
            new_content = raw + "\n" + block_yaml
        else:
            # 从 start_idx 到下一个顶格非注释非空行的前一行结束
            end_idx = len(lines)
            for j in range(start_idx + 1, len(lines)):
                ln = lines[j]
                # 下一个顶级键（无缩进的 key+冒号，且非注释）
                if (not ln.startswith(" ") and ":" in ln and not ln.startswith("#") and ln.strip()):
                    end_idx = j
                    break
            new_content = "".join(lines[:start_idx]) + block_yaml + "".join(lines[end_idx:])

        path.write_text(new_content, encoding="utf-8")
        logger.info("已导出 module_tree 到 config.yaml: %s", path)
        return True
    except Exception:
        logger.exception("导出 module_tree 到 config.yaml 失败")
        return False


def sync_to_user_profiles(trees: Dict[str, Any]) -> int:
    """把树上分配的工程师同步覆盖回 users.responsibility_modules。

    以 module_tree 为唯一权威：对每个涉及其产品的工程师，
    重算 responsibility_modules[产品] = [该工程师负责的所有界面名]。
    仅覆盖本次涉及的产品 key，工程师在其它产品的已有模块保留（避免误清车端等）。

    返回：被更新的工程师数。
    """
    from app.models.identity import UserDB

    # 1. 收集 { 产品: { 工程师id: set(界面名) } }
    product_engineers: Dict[str, Dict[str, set]] = {}
    for product, tree in trees.items():
        pe = product_engineers.setdefault(product, {})
        for iface in (tree or {}).get("interfaces", []) or []:
            iface_name = iface.get("name") or ""
            if not iface_name:
                continue
            for fn in (iface.get("functions", []) or []):
                for eid in (fn.get("engineers") or []):
                    if not eid:
                        continue
                    pe.setdefault(eid, set()).add(iface_name)

    if not product_engineers:
        return 0

    # 2. 收集所有涉及的产品和工程师 id
    all_eids = set()
    for pe in product_engineers.values():
        all_eids.update(pe.keys())

    db = _get_db()
    try:
        rows = db.query(UserDB).filter(UserDB.id.in_(all_eids)).all()
        user_map = {u.id: u for u in rows}
        updated = 0
        for product, pe in product_engineers.items():
            for eid, ifaces in pe.items():
                u = user_map.get(eid)
                if not u:
                    continue
                # 读取现有模块，保留其它产品的 key，只覆盖当前产品
                current = u.responsibility_modules
                if isinstance(current, str):
                    try:
                        current = json.loads(current)
                    except Exception:
                        current = {}
                if not isinstance(current, dict):
                    current = {}
                current = {k: v for k, v in current.items() if v is not None}
                # 覆盖当前产品 = 该工程师负责的界面名列表（非空）
                current[product] = sorted(ifaces) if ifaces else []
                # 这个产品该工程师一个界面都没负责 → 移除该产品 key
                if not ifaces:
                    current.pop(product, None)
                # 存回
                try:
                    u.responsibility_modules = current
                    updated += 1
                except Exception:
                    logger.exception("同步 responsibility_modules 失败: %s", eid)
        db.commit()
        logger.info("已将 module_tree 工程师同步到 users 画像: %d 人", updated)
        return updated
    except Exception:
        db.rollback()
        logger.exception("同步 module_tree 工程师到 users 画像失败")
        return 0
    finally:
        db.close()


def save_trees(trees: Dict[str, Any]) -> Dict[str, Any]:
    """统一保存：写 DB + 同步用户画像 + 导出 config。

    返回 {"db": bool, "synced": int, "export": bool}。
    """
    ok_db = replace_all_trees(trees)
    if not ok_db:
        return {"db": False, "synced": 0, "export": False}
    synced = sync_to_user_profiles(trees)
    ok_export = export_to_config(trees)
    return {"db": True, "synced": synced, "export": ok_export}
