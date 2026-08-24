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
from app.models.module_tree import ModuleTree

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


def bulk_upsert_delete(products: Dict[str, Any], removed: Optional[List[str]] = None) -> bool:
    """按提交的产品增量写 DB + 按需删除指定产品。

    - 只新增/更新 products 中涉及的产品行，**绝不删除/改动未提交的产品**（避免多人协作互相覆盖）。
    - removed 里列出的产品将被删除（删除产品场景）。
    - 返回 True/False；空 products、removed 均调用前由 save_trees 拦截。
    """
    db = _get_db()
    try:
        for product, tree in (products or {}).items():
            row = db.query(ModuleTree).filter(ModuleTree.product == product).first()
            if row:
                row.tree_json = tree or {"interfaces": []}
            else:
                db.add(ModuleTree(product=product, tree_json=tree or {"interfaces": []}))
        for product in (removed or []):
            db.query(ModuleTree).filter(ModuleTree.product == product).delete()
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("bulk_upsert_delete 失败")
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


def merge_product_changes(
    db_tree: Optional[Dict[str, Any]],
    submitted: Dict[str, Any],
    changed_funcs: Optional[List[str]] = None,
    new_interfaces: Optional[List[str]] = None,
    removed_interfaces: Optional[List[str]] = None,
    removed_funcs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """把前端提交的产品树，**按节点**合并到 DB 当前版本（节点级并发安全）。

    规则（关键：不同界面/不同功能互不影响；只有本次改动过的节点才覆盖）：
    - 前端提交界面里，DB 没有的同名界面 → 作为“新增界面”加入。
    - DB 已有界面：只覆盖 changed_funcs 里点名的功能节点；其余功能保留 DB 最新值（不覆盖他人改动）。
    - changed_funcs 条目用「界面名||功能名」定位；功能不存在则按新增加入。
    - removed_interfaces / removed_funcs：显式删除（用 name 或 key 匹配）。
    - 绝不删除 DB 中有、但前端旧快照里没有的未点名节点（避免误删他人新加内容）。
    """
    db_ifaces = (db_tree or {}).get("interfaces", []) or []
    new_iface_names = {_norm_name(n) for n in (new_interfaces or [])}

    # 1) 生成 db 界面索引（按 name）；被删除的界面（removed_interfaces 按 name）不纳入
    db_by_iface_name: Dict[str, dict] = {}
    db_removed_iface_names = {_norm_name(k) for k in (removed_interfaces or [])}
    for it in db_ifaces:
        nm = _norm_name(it.get("name"))
        if nm and nm not in db_by_iface_name and nm not in db_removed_iface_names:
            db_by_iface_name[nm] = dict(it)

    # 2) 解析要移除的功能（key 或 name）
    removed_func_set = {_norm_name(k) for k in (removed_funcs or [])}

    # 3) 计算每个界面本次要覆盖的功能名集合
    changed_by_iface: Dict[str, set] = {}
    for entry in (changed_funcs or []):
        if "||" in entry:
            iface_nm, _, func_nm = entry.partition("||")
            changed_by_iface.setdefault(_norm_name(iface_nm), set()).add(_norm_name(func_nm))

    # 4) 遍历前端提交，构建结果 interfaces
    out_ifaces: List[dict] = []
    submitted_iface_names = set()
    for sub_iface in (submitted.get("interfaces", []) or []):
        iface_nm = _norm_name(sub_iface.get("name"))
        if not iface_nm:
            continue
        submitted_iface_names.add(iface_nm)
        # 新增界面：直接整体纳入
        if iface_nm in new_iface_names or iface_nm not in db_by_iface_name:
            out_ifaces.append(dict(sub_iface))
            continue

        # DB 已有界面：从 DB 版本出发合并功能
        db_iface = db_by_iface_name[iface_nm]
        db_funcs = db_iface.get("functions", []) or []
        changed = changed_by_iface.get(iface_nm, set())
        db_by_name: Dict[str, dict] = {}
        for f in db_funcs:
            fnm = _norm_name(f.get("name"))
            if fnm:
                db_by_name.setdefault(fnm, dict(f))

        result_funcs: List[dict] = []
        seen_func_names: set = set()
        # 先放 DB 未改动的功能（保留最新）
        for fnm, dbf in db_by_name.items():
            if fnm in removed_func_set:
                continue
            if fnm in changed:
                continue  # 交给提交覆盖
            seen_func_names.add(fnm)
            result_funcs.append(dbf)
        # 再放本次改动的功能（用提交版本）
        for sub_fn in (sub_iface.get("functions", []) or []):
            fnm = _norm_name(sub_fn.get("name"))
            if not fnm or fnm in removed_func_set:
                continue
            if fnm in changed or fnm not in db_by_name:
                if fnm not in seen_func_names:
                    seen_func_names.add(fnm)
                    result_funcs.append(dict(sub_fn))
        # 删除：removed_funcs 已在上面跳过；同时处理移除“仅存在于 db 且被删”的功能
        out_ifaces.append({**db_iface, "functions": result_funcs})

    # 5) DB 里有、但前端提交没提（且不在删除列表）的界面保留（防误删他人新加）
    for it in db_ifaces:
        nm = _norm_name(it.get("name"))
        if not nm or nm in submitted_iface_names or nm in db_removed_iface_names:
            continue
        out_ifaces.append(dict(it))

    return {"interfaces": out_ifaces}


def func_node_hash(fn: Optional[Dict[str, Any]]) -> str:
    """单功能节点内容哈希（功能级冲突检测基准）。"""
    try:
        raw = json.dumps(fn or {}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(fn)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def save_trees(
    trees: Dict[str, Any],
    removed: Optional[List[str]] = None,
    per_product: Optional[Dict[str, Any]] = None,
    func_hashes: Optional[Dict[str, Dict[str, str]]] = None,
    force: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """统一保存（节点级增量 + 功能级冲突检测）：按产品写 DB + 同步画像 + 导出 config。

    核心：
    - 仅覆盖提交的产品；对每个产品，只把**本次改动过的功能节点**合并进 DB 当前最新版，
      其它节点全部保留 DB 值 —— 不同界面/不同功能各自保存、互不覆盖（多人并发安全）。
    - per_product={产品:{changed_funcs,new_interfaces,removed_interfaces,removed_funcs}} 声明本次改动范围。
    - 功能级冲突：func_hashes={产品:{'界面名||功能名': 用户加载时该功能哈希}}。
      若 DB 当前该功能哈希 ≠ 用户加载时哈希（该功能已被他人改过）且用户本次也改了它 →
      跳过该功能覆盖（保留 DB 版）并记入 conflicted_funcs；force 里的产品跳过检测直接覆盖。
    - removed 删除产品；空提交拒绝防清空；导出 config 用 DB 全量。

    返回 {"db","synced","export","conflicted_funcs"?:list,"rejected"?}。
    """
    removed = removed or []
    per_product = per_product or {}
    func_hashes = func_hashes or {}
    force = list(force or [])
    if not trees and not removed:
        return {"db": False, "synced": 0, "export": False, "rejected": "empty"}

    # 保存前统一重算 界面/功能 的 key（前端中文名 → 前两字拼音+哈希）
    normalized = renormalize_keys(trees)

    conflicted_funcs: List[str] = []
    writable: Dict[str, Any] = {}

    for product, tree in normalized.items():
        pp = per_product.get(product) or {}
        changed = pp.get("changed_funcs") or []
        new_ifaces = pp.get("new_interfaces") or []
        rem_ifaces = pp.get("removed_interfaces") or []
        rem_funcs = pp.get("removed_funcs") or []
        db_cur = get_product_tree(product)

        # 强制覆盖或全新产品：直接采用提交整树
        if product in force or not db_cur:
            writable[product] = tree
            continue

        # 功能级冲突检测：DB 中本次改动功能较用户加载时已变 → 该功能冲突，跳过覆盖
        local_hashes = func_hashes.get(product) or {}
        db_iface_funcs: Dict[str, Dict[str, str]] = {}
        for it in db_cur.get("interfaces", []) or []:
            iname = _norm_name(it.get("name"))
            if not iname:
                continue
            m: Dict[str, str] = {}
            for f in (it.get("functions", []) or []):
                fnm = _norm_name(f.get("name"))
                if fnm:
                    m[fnm] = func_node_hash(f)
            db_iface_funcs[iname] = m

        real_changed: List[str] = []
        for entry in changed:
            if "||" not in entry:
                continue
            iname, _, fnm = entry.partition("||")
            iname, fnm = _norm_name(iname), _norm_name(fnm)
            base_h = local_hashes.get(entry)
            db_h = (db_iface_funcs.get(iname) or {}).get(fnm)
            if base_h is not None and db_h is not None and db_h != base_h:
                conflicted_funcs.append(f"{product}||{iname}||{fnm}")
                continue
            real_changed.append(entry)

        merged = merge_product_changes(
            db_cur,
            tree,
            changed_funcs=real_changed,
            new_interfaces=new_ifaces,
            removed_interfaces=rem_ifaces,
            removed_funcs=rem_funcs,
        )
        writable[product] = merged

    # 若所有提交的产品都无可写内容且无删除 → 视为冲突/空，不写 DB
    if not writable and not removed:
        return {"db": False, "synced": 0, "export": False, "conflicted_funcs": conflicted_funcs}

    ok_db = bulk_upsert_delete(writable, removed)
    if not ok_db:
        return {"db": False, "synced": 0, "export": False, "conflicted_funcs": conflicted_funcs}

    # 同步用户画像：只覆盖提交成功的产品（sync_to_user_profiles 已产品级安全）＋ 删除产品清理
    synced = sync_to_user_profiles(writable)
    if removed:
        synced += _clear_removed_products_from_profiles(removed)

    # 导出 config：用 DB 全量（合并本次），避免把其它产品从 config 抹掉
    ok_export = export_to_config(get_all_trees())
    return {"db": True, "synced": synced, "export": ok_export, "conflicted_funcs": conflicted_funcs}
