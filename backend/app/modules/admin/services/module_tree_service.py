"""module_tree（产品→界面→功能 责任树）业务服务层。

职责：
- 读写 DB 中功能级行模型 module_tree_nodes（每功能一行）。
- 保存后同步工程师到 users.responsibility_modules（三层画像），并通知 AI 服务 reload。
"""
import hashlib
import json
import logging
from typing import Dict, Any, List, Optional

from pypinyin import pinyin, Style

from app.core.database import db_manager
from app.models.module_tree_node import ModuleTreeNode

logger = logging.getLogger(__name__)


def _pinyin_head(zh_name: str) -> str:
    """取中文名「前两字」的拼音全拼（无空格）；不足两字取全部；字母/数字保留。"""
    head = (zh_name or '')[:2]
    if not head:
        return ''
    try:
        return ''.join(p[0] for p in pinyin(head, style=Style.NORMAL))
    except Exception:
        return head


def _hash_str(s: str) -> str:
    h = 0
    for b in (s or '').encode('utf-8'):
        h = (h * 31 + b) & 0xFFFFFFFF
    return format(h, 'x')[:4]


def gen_key(zh_name: str, seen: set) -> str:
    """生成确定性 key：<前两字拼音>_<短哈希(全名)>，撞车则加盐保证唯一。"""
    name = (zh_name or '').strip()
    if not name:
        return ''
    base = _pinyin_head(name) or 'item'
    k = f"{base}_{_hash_str(name)}"
    salt = 1
    while k in seen:
        k = f"{base}_{_hash_str(f'{name}{salt}')}"
        salt += 1
    seen.add(k)
    return k


def renormalize_keys(trees: Dict[str, Any]) -> Dict[str, Any]:
    """对整棵树重算 界面/功能 的 key：

    - 界面 key：同产品内唯一
    - 功能 key：同产品内全局唯一（用 name 前两字拼音 + 哈希，避免歧义）
    返回新的 dict（不修改入参）。
    """
    result: Dict[str, Any] = {}
    for product, tree in trees.items():
        seen_iface: set = set()
        seen_fn: set = set()
        new_tree: Dict[str, Any] = {"interfaces": []}
        for iface in (tree or {}).get("interfaces", []) or []:
            new_iface = dict(iface)
            iface_name = (new_iface.get("name") or "").strip()
            if iface_name:
                new_iface["key"] = gen_key(iface_name, seen_iface)
            new_funcs = []
            for fn in (iface.get("functions", []) or []):
                new_fn = dict(fn)
                fn_name = (new_fn.get("name") or "").strip()
                if fn_name:
                    new_fn["key"] = gen_key(fn_name, seen_fn)
                new_funcs.append(new_fn)
            new_iface["functions"] = new_funcs
            new_tree["interfaces"].append(new_iface)
        result[product] = new_tree
    return result


def _get_db():
    return db_manager.get_db()


def _aggregate_from_nodes(products: Optional[List[str]] = None) -> Dict[str, Any]:
    """从行表 module_tree_nodes 聚合出 {产品: {interfaces:[...]}}。

    - 界面按 iface_name 分组、iface_order 排序；功能按 func_order 排序。
    - 每个功能带上行 id、iface_order/func_order。
    - 聚合后补界面/功能 key（用于展示折叠；业务定位靠行 id 与 func_name）。
    """
    db = _get_db()
    try:
        q = db.query(ModuleTreeNode)
        if products:
            q = q.filter(ModuleTreeNode.product.in_(products))
        rows = q.all()
        rows.sort(key=lambda r: (r.product, r.iface_order, r.func_order))
        result: Dict[str, Any] = {}
        for r in rows:
            tree = result.setdefault(r.product, {"interfaces": []})
            iface = next((it for it in tree["interfaces"] if it["name"] == r.iface_name), None)
            if iface is None:
                iface = {"name": r.iface_name, "functions": []}
                tree["interfaces"].append(iface)
            iface["functions"].append({
                "id": r.id,
                "name": r.func_name,
                "keywords": r.keywords or [],
                "anchor": r.anchor or "",
                "engineers": r.engineers or [],
                "iface_order": r.iface_order,
                "func_order": r.func_order,
            })
        return renormalize_keys(result)
    finally:
        db.close()


def get_all_trees() -> Dict[str, Any]:
    """返回 {产品: {"interfaces": [...]}}，从功能行模型 module_tree_nodes 聚合。"""
    return _aggregate_from_nodes()


def get_product_tree(product: str) -> Optional[Dict[str, Any]]:
    """返回单产品的接口树（该产品名下的 interfaces 结构），从行模型聚合。"""
    return _aggregate_from_nodes([product]).get(product)


def product_hash(tree: Optional[Dict[str, Any]]) -> str:
    """对产品树生成稳定哈希（乐观锁版本标识）。

    用 sort_keys + ensure_ascii=False 稳定序列化（与前端加载时一致），
    内容或顺序变化都会导致哈希变化，用于检测"产品是否被他人改过"。
    """
    try:
        raw = json.dumps(tree or {"interfaces": []}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(tree)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get_product_hashes() -> Dict[str, str]:
    """返回 {产品: 产品树哈希}，供前端加载时记录"本地基准版本"用于乐观锁。"""
    trees = get_all_trees()
    return {product: product_hash(tree) for product, tree in trees.items()}


def get_func_hashes() -> Dict[str, Dict[str, str]]:
    """返回 {产品: {'界面名||功能名': 功能节点哈希}}，供前端记录"本地加载时的各功能版本"
    用于功能级冲突检测（只有同一产品同一界面同一功能被双方修改才判定冲突）。"""
    trees = get_all_trees()
    result: Dict[str, Dict[str, str]] = {}
    for product, tree in trees.items():
        m: Dict[str, str] = {}
        for it in (tree or {}).get("interfaces", []) or []:
            iname = _norm_name(it.get("name"))
            if not iname:
                continue
            for f in (it.get("functions", []) or []):
                fnm = _norm_name(f.get("name"))
                if fnm:
                    m[f"{iname}||{fnm}"] = func_node_hash(f)
        if m:
            result[product] = m
    return result


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
    """审批通过后，把某功能行的修改应用到 DB（行表 module_tree_nodes）。

    以 new_json 里的功能名定位（其次用 func_key），只替换存在的字段
    （name/keywords/anchor/engineers），不动其它。返回是否成功。
    """
    func_name = _norm_name((new_json or {}).get("name")) or _norm_name(func_key)
    db = _get_db()
    try:
        node = (
            db.query(ModuleTreeNode)
            .filter(ModuleTreeNode.product == product, ModuleTreeNode.func_name == func_name)
            .first()
        )
        if not node:
            logger.error("apply_function_change: 功能行不存在 %s/%s", product, func_name)
            return False
        if new_json:
            if "name" in new_json:
                node.func_name = _norm_name(new_json["name"])
            if "keywords" in new_json:
                node.keywords = new_json.get("keywords") or []
            if "anchor" in new_json:
                node.anchor = new_json.get("anchor") or ""
            if "engineers" in new_json:
                node.engineers = new_json.get("engineers") or []
            iface = new_json.get("iface_name") or new_json.get("iface")
            if iface:
                node.iface_name = _norm_name(iface)
        db.commit()
        # 统一收尾
        after_write()
        return True
    except Exception:
        db.rollback()
        logger.exception("apply_function_change 失败 %s/%s", product, func_name)
        return False
    finally:
        db.close()


def sync_to_user_profiles(trees: Dict[str, Any]) -> int:
    """把树上分配的工程师同步回 users.responsibility_modules（三层结构）。

    以 module_tree 为权威：重算 responsibility_modules[产品] = {界面名: [功能名]}。
    仅覆盖树中涉及的产品 key，工程师在其它产品的模块保留。返回被更新的工程师数。
    """
    from app.models.identity import UserDB

    # 收集 { 产品: { 工程师id: {界面名: set(功能名)} } }
    product_engineers: Dict[str, Dict[str, Dict[str, set]]] = {}
    for product, tree in trees.items():
        pe = product_engineers.setdefault(product, {})
        for iface in (tree or {}).get("interfaces", []) or []:
            iface_name = iface.get("name") or ""
            if not iface_name:
                continue
            for fn in (iface.get("functions", []) or []):
                fn_name = fn.get("name") or ""
                if not fn_name:
                    continue
                for eid in (fn.get("engineers") or []):
                    if not eid:
                        continue
                    by_iface = pe.setdefault(eid, {})
                    by_iface.setdefault(iface_name, set()).add(fn_name)

    if not product_engineers:
        return 0

    # 收集所有涉及的工程师 id
    all_eids = set()
    for pe in product_engineers.values():
        all_eids.update(pe.keys())

    db = _get_db()
    try:
        rows = db.query(UserDB).filter(UserDB.id.in_(all_eids)).all()
        user_map = {u.id: u for u in rows}
        updated = 0
        for product, pe in product_engineers.items():
            for eid, by_iface in pe.items():
                u = user_map.get(eid)
                if not u:
                    continue
                # 保留其它产品 key，只覆盖当前产品
                current = u.responsibility_modules
                if isinstance(current, str):
                    try:
                        current = json.loads(current)
                    except Exception:
                        current = {}
                if not isinstance(current, dict):
                    current = {}
                current = {k: v for k, v in current.items() if v is not None}
                if by_iface:
                    current[product] = {
                        iface_name: sorted(funcs)
                        for iface_name, funcs in sorted(by_iface.items())
                        if funcs
                    }
                else:
                    current.pop(product, None)
                try:
                    u.responsibility_modules = current
                    updated += 1
                except Exception:
                    logger.exception("同步 responsibility_modules 失败: %s", eid)
        db.commit()
        logger.info("已将 module_tree 工程师同步到 users 画像(三层): %d 人", updated)
        return updated
    except Exception:
        db.rollback()
        logger.exception("同步 module_tree 工程师到 users 画像失败")
        return 0
    finally:
        db.close()


def _clear_removed_products_from_profiles(removed: List[str]) -> int:
    """删除产品后，清理 users.responsibility_modules 中这些产品 key（不影响其它产品）。"""
    if not removed:
        return 0
    from app.models.identity import UserDB

    db = _get_db()
    try:
        # 只扫描有画像的用户，避免全表空扫
        rows = db.query(UserDB).filter(UserDB.responsibility_modules.isnot(None)).all()
        updated = 0
        removed_set = set(removed)
        for u in rows:
            current = u.responsibility_modules
            if isinstance(current, str):
                try:
                    current = json.loads(current)
                except Exception:
                    current = {}
            if not isinstance(current, dict):
                continue
            hit = False
            for product in list(current.keys()):
                if product in removed_set:
                    del current[product]
                    hit = True
            if hit:
                try:
                    u.responsibility_modules = current
                    updated += 1
                except Exception:
                    logger.exception("清理被删产品责任模块失败: %s", u.id)
        db.commit()
        logger.info("已清理被删产品的 responsibility_modules: %d 人", updated)
        return updated
    except Exception:
        db.rollback()
        logger.exception("清理被删产品责任模块失败")
        return 0
    finally:
        db.close()


def _norm_name(s) -> str:
    return (s or "").strip()


def func_node_hash(fn: Optional[Dict[str, Any]]) -> str:
    """单功能节点内容哈希（功能级冲突检测基准）。"""
    try:
        raw = json.dumps(fn or {}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(fn)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def upsert_node(
    product: str,
    iface_name: str,
    iface_order: int,
    func_name: str,
    func_order: int,
    keywords: Optional[list] = None,
    anchor: Optional[str] = None,
    engineers: Optional[list] = None,
    node_id: Optional[int] = None,
) -> Dict[str, Any]:
    """按 id 新增/更新一个功能行（id 有则 update，否则 insert）。

    按行 id 精确定位，多人改不同行互不覆盖。写成功后统一收尾 after_write()，
    返回 {"id": 行id, "synced": n, "ai_reload": ...}；失败返回 {"id": None}。
    """
    db = _get_db()
    try:
        if node_id:
            node = db.query(ModuleTreeNode).filter(ModuleTreeNode.id == node_id).first()
            if not node:
                return {"id": None}
            node.iface_name = _norm_name(iface_name)
            node.iface_order = iface_order
            node.func_name = _norm_name(func_name)
            node.func_order = func_order
            node.keywords = keywords or []
            node.anchor = anchor or ""
            node.engineers = engineers or []
        else:
            node = ModuleTreeNode(
                product=product,
                iface_name=_norm_name(iface_name),
                iface_order=iface_order or 0,
                func_name=_norm_name(func_name),
                func_order=func_order or 0,
                keywords=keywords or [],
                anchor=anchor or "",
                engineers=engineers or [],
            )
            db.add(node)
        db.commit()
        db.refresh(node)
        w = after_write()  # 统一收尾
        return {"id": node.id, **w}
    except Exception:
        db.rollback()
        logger.exception("upsert node 失败: product=%s func=%s", product, func_name)
        return {"id": None}
    finally:
        db.close()


def delete_nodes(ids: List[int]) -> Dict[str, Any]:
    """按行 id 批量删除功能行。返回 {"deleted": n, "synced": ..., "ai_reload": ...}。

    前端删除整个产品时会一次删光该产品所有行；删除前先收集这些行所属的产品，
    交给 after_write(removed=...) 清理这些产品在画像里残留的 key。
    """
    db = _get_db()
    try:
        rows = db.query(ModuleTreeNode).filter(ModuleTreeNode.id.in_(ids)).all()
        removed_products = sorted({r.product for r in rows if r.product})
        n = db.query(ModuleTreeNode).filter(ModuleTreeNode.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        w = after_write(removed=removed_products)  # 统一收尾 + 清理被删产品画像
        return {"deleted": n or 0, "products": removed_products, **w}
    except Exception:
        db.rollback()
        logger.exception("delete nodes 失败: ids=%s", ids)
        return {"deleted": 0, "products": []}
    finally:
        db.close()


def _notify_ai_reload() -> Optional[str]:
    """通知 AI 服务重载派单配置与画像缓存（尽力而为，失败不阻断）。

    AI 服务不可用时返回错误串，不影响后端写库。
    """
    try:
        import httpx
        from app.core.config import settings
        ai_url = settings.AI_SERVICE_URL.rstrip("/")
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(f"{ai_url}/api/ai/assigner/reload")
            if resp.status_code == 200:
                return "ok"
        return str(resp.status_code)
    except Exception as e:
        logger.warning("通知AI画像重载失败: %s", e)
        return f"AI 热更新失败: {e}"


def after_write(removed: Optional[List[str]] = None) -> Dict[str, Any]:
    """写树后的统一收尾：同步用户画像 + 清理被删产品画像 + 通知 AI。

    适用：单行写/删（upsert_node / delete_node）、审批应用（apply_function_change）。
    - 以行表当前全量树为权威重算 users.responsibility_modules（三层画像）；
    - removed：被整产品删除的产品名，额外清理其在画像里的 key；
    - 最后通知 AI reload。

    返回 {"synced": n, "ai_reload": 通知结果}。
    """
    all_trees = _aggregate_from_nodes()
    synced = sync_to_user_profiles(all_trees)
    if removed:
        synced += _clear_removed_products_from_profiles(removed)
    ai_reload = _notify_ai_reload()
    return {"synced": synced, "ai_reload": ai_reload}
