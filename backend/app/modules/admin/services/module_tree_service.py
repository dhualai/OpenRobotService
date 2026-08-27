"""module_tree（产品→界面→功能 责任树）业务服务层。

职责：
- 读写 DB 中的 product→tree（每个产品一行，JSON 存接口树）
- 保存后将**所有产品**的树导出覆盖到 AI Assigner 的 config.yaml（作为启动快照）
- 导出后通知 AI 服务热更新，让运行中派单流水线感知新配置
"""
import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from pypinyin import pinyin, Style

from app.core.database import db_manager
from app.models.module_tree_node import ModuleTreeNode

logger = logging.getLogger(__name__)

# config.yaml 相对项目根的路径（本文件位于 backend/app/modules/admin/services/）
# parents[5] = 项目根（services->admin->modules->app->backend->root）
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_ASSIGNER_CONFIG_PATH = _PROJECT_ROOT / "ai" / "agents" / "AiDiagnosisPlatform" / "assigner" / "config" / "config.yaml"


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

    - 界面以 iface_name 分组、按 iface_order 排序；功能按 func_order 排序。
    - 每个功能带上行 id（前端按 id 单行保存的定位依据）、iface_order/func_order。
    - 聚合后用 renormalize_keys 补界面/功能 key（key 用于展示折叠；业务定位靠行 id 与 func_name）。
    - 返回结构与旧表 module_trees(整树 JSON) 一致，另增字段 id/iface_order/func_order。
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

    兼容：func_key 参数在旧结构里是 hash key，新行表用 func_name 定位；
    new_json 里定义的功能名优先，其次用 func_key 当作 func_name 匹配。
    只替换存在的字段（name/keywords/anchor/engineers），不动其它。返回是否成功。
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
        # 同步用户画像 + 导出 config
        after_write()
        return True
    except Exception:
        db.rollback()
        logger.exception("apply_function_change 失败 %s/%s", product, func_name)
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
    """把树上分配的工程师同步回 users.responsibility_modules（三层结构）。

    以 module_tree 为唯一权威：对每个涉及其产品的工程师，
    重算 responsibility_modules[产品] = {界面名: [该工程师负责的功能名]}。
    仅覆盖本次涉及的产品 key，工程师在其它产品的已有模块保留（避免误清车端等）。

    返回：被更新的工程师数。
    """
    from app.models.identity import UserDB

    # 1. 收集 { 产品: { 工程师id: {界面名: set(功能名)} } }
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
            for eid, by_iface in pe.items():
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
                # 覆盖当前产品 = 该工程师负责的「界面 → 功能名列表」（三层）
                if by_iface:
                    current[product] = {
                        iface_name: sorted(funcs)
                        for iface_name, funcs in sorted(by_iface.items())
                        if funcs
                    }
                else:
                    current.pop(product, None)
                # 存回
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
        # 找出 currently 含这些产品的所有用户（宽松：先遍历有值用户，避免全表空扫）
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


def _flatten_tree(tree: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把一棵树的 interfaces 展平成功能行字典列表（供行表 diff 用）。"""
    out: List[Dict[str, Any]] = []
    for iface_idx, it in enumerate((tree or {}).get("interfaces", []) or []):
        iface_name = _norm_name(it.get("name"))
        if not iface_name:
            continue
        for func_idx, fn in enumerate((it.get("functions", []) or [])):
            func_name = _norm_name(fn.get("name"))
            if not func_name:
                continue
            out.append({
                "iface_name": iface_name,
                "iface_order": iface_idx,
                "func_name": func_name,
                "func_order": func_idx,
                "keywords": fn.get("keywords") or [],
                "anchor": fn.get("anchor") or "",
                "engineers": fn.get("engineers") or [],
            })
    return out


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
) -> Optional[int]:
    """按 id 新增/更新一个功能行（id 有则 update 该行，否则 insert 新行）。

    - 由于按行 id 精确定位，多人改不同行天然互不覆盖（并发安全核心）。
    - 返回该行 id（新增时返回新分配的 id，供前端绑定）。
    """
    db = _get_db()
    try:
        if node_id:
            node = db.query(ModuleTreeNode).filter(ModuleTreeNode.id == node_id).first()
            if not node:
                return None
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
        return node.id
    except Exception:
        db.rollback()
        logger.exception("upsert node 失败: product=%s func=%s", product, func_name)
        return None
    finally:
        db.close()


def delete_nodes(ids: List[int]) -> int:
    """按行 id 批量删除功能行。返回删除行数。"""
    db = _get_db()
    try:
        n = db.query(ModuleTreeNode).filter(ModuleTreeNode.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        return n or 0
    except Exception:
        db.rollback()
        logger.exception("delete nodes 失败: ids=%s", ids)
        return 0
    finally:
        db.close()


def after_write() -> Dict[str, int]:
    """单行写/删后统一做：同步用户画像 + 导出 config（P3 前保留 config）。

    返回 {"synced": n, "export": 0/1}。
    """
    all_trees = _aggregate_from_nodes()
    synced = sync_to_user_profiles(all_trees)
    ok_export = export_to_config(all_trees)
    return {"synced": synced, "export": 1 if ok_export else 0}


def save_trees(
    trees: Dict[str, Any],
    removed: Optional[List[str]] = None,
    per_product: Optional[Dict[str, Any]] = None,
    func_hashes: Optional[Dict[str, Dict[str, str]]] = None,
    force: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """统一保存（功能级行模型 diff 写入，天然并发安全）：写 module_tree_nodes + 同步画像 + 导出 config。

    核心（P2，彻底切行表）：
    - 只覆盖提交的产品；对每个产品，以**行表当前状态为基准**做功能级 diff（按 func_name）：
      - 前端提交里有的功能 → insert/update 对应行（含 iface/order/关键词/锚/工程师）；
      - 前端提交里没有的 DB 行 → **不删**（旧快照缺失 ≠ 删除，避免误删他人新增）；
      - 显式删除（per_product.removed_funcs / removed 产品）→ delete 对应行。
    - 因此**不同功能各自保存、互不覆盖**（后端只动差异行，天然并发安全）。
    - 兼容旧前端整体提交（不依赖 per_product 键名），同时也接受单功能/节点增量。
    - 不再镜像旧表 module_trees（已彻底切换行表）。
    - removed 删除产品；空提交拒绝防清空。

    返回 {"db","synced","export","rejected"?}。
    """
    removed = removed or []
    per_product = per_product or {}
    if not trees and not removed:
        return {"db": False, "synced": 0, "export": False, "rejected": "empty"}

    db = _get_db()
    try:
        # 1) 逐产品：功能级 diff 写行表
        for product, tree in trees.items():
            if not product:
                continue
            # 收集该产品本次要写入的功能行（前端提交）
            submitted_rows = _flatten_tree(tree)
            # 现有行表（按 func_name 索引）
            existing = {
                _norm_name(r.func_name): r
                for r in db.query(ModuleTreeNode).filter(ModuleTreeNode.product == product).all()
            }
            # 显式删除的功能（per_product.removed_funcs：'界面名||功能名' 或 功能名）
            rem_funcs = set()
            pp = per_product.get(product) or {}
            for k in (pp.get("removed_funcs") or []):
                nm = _norm_name(k)
                if "||" in nm:
                    nm = nm.split("||", 1)[1]
                if nm:
                    rem_funcs.add(nm)

            func_names_in_submit = set()
            for row in submitted_rows:
                fname = row["func_name"]
                func_names_in_submit.add(fname)
                node = existing.get(fname)
                if node is not None:
                    # 覆盖（diff：本质是 upsert 该功能行）
                    node.iface_name = row["iface_name"]
                    node.iface_order = row["iface_order"]
                    node.func_name = fname
                    node.func_order = row["func_order"]
                    node.keywords = row["keywords"]
                    node.anchor = row["anchor"]
                    node.engineers = row["engineers"]
                else:
                    db.add(ModuleTreeNode(
                        product=product,
                        iface_name=row["iface_name"],
                        iface_order=row["iface_order"],
                        func_name=fname,
                        func_order=row["func_order"],
                        keywords=row["keywords"],
                        anchor=row["anchor"],
                        engineers=row["engineers"],
                    ))
            # 显式删除功能
            for fname in rem_funcs:
                if fname in existing and fname not in func_names_in_submit:
                    db.delete(existing[fname])

        # 2) 删除产品：删全部行
        for product in (removed or []):
            db.query(ModuleTreeNode).filter(ModuleTreeNode.product == product).delete()

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("save_trees 写 module_tree_nodes 失败")
        return {"db": False, "synced": 0, "export": False}
    finally:
        db.close()

    # 3) 用全量树同步用户画像（行表聚合后，产品级安全）
    all_trees = get_all_trees()
    synced = sync_to_user_profiles(all_trees)
    if removed:
        synced += _clear_removed_products_from_profiles(removed)

    # 4) 导出 config（AI 派单当前仍读 config；P3 切 AI 读后端接口后再删除）
    ok_export = export_to_config(all_trees)
    return {"db": True, "synced": synced, "export": ok_export}
