"""配置加载器：统一加载 assigner/config/config.yaml 下的派单配置

配置项与消费方对应关系（与 config.yaml 头部注释保持一致）：
- module_keywords      → recall/history_recall.py（L3 历史召回：历史工单标签提取）
- job_level_penalty    → ranking/ranker.py（职级折扣）
- department_routing   → filtering/dept_router.py（R2/R3 融合与门槛）
- departments          → 已不再从 yaml 读；只认 DB departments.profile_text
- product_routing      → filtering/product_router.py（产品收紧）
- vague_strong_signals → ranking/tags.py（Step2 模糊强信号）
"""

from pathlib import Path
from typing import Any, Dict

try:
    import yaml
    def _load_yaml(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
except ImportError:
    raise RuntimeError("PyYAML 是必要依赖，请安装: pip install pyyaml")


CLUSTER_OVERRIDE_KEYS = ("cluster_merge", "cluster_assign", "cluster_min_size")
_OVERRIDE_FILE = Path(__file__).parent / "config" / "runtime_overrides.yaml"


def load_runtime_overrides() -> dict:
    if not _OVERRIDE_FILE.exists():
        return {}
    try:
        data = _load_yaml(_OVERRIDE_FILE) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_float(raw):
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val:  # NaN
        return None
    return val


def normalize_cluster_params(
    *,
    cluster_merge=None,
    cluster_assign=None,
    cluster_min_size=None,
) -> dict:
    """开发者模式写入前校验。空值跳过；越界直接报错。"""
    out = {}
    merge = _as_float(cluster_merge) if cluster_merge is not None else None
    if merge is not None and merge > 0:
        if not 0.10 <= merge <= 0.99:
            raise ValueError("合并门槛应在 0.10～0.99")
        out["cluster_merge"] = round(merge, 4)
    assign = _as_float(cluster_assign) if cluster_assign is not None else None
    if assign is not None and assign > 0:
        if not 0.10 <= assign <= 0.99:
            raise ValueError("进簇门槛应在 0.10～0.99")
        out["cluster_assign"] = round(assign, 4)
    if cluster_min_size is not None and str(cluster_min_size).strip() != "":
        size_f = _as_float(cluster_min_size)
        if size_f is None:
            raise ValueError("最小团必须是整数")
        size = int(size_f)
        if size < 2:
            # 空输入会变成 0，忽略而不是整单保存失败
            pass
        elif size > 20:
            raise ValueError("最小团应在 2～20")
        else:
            out["cluster_min_size"] = size
    if not out:
        raise ValueError("没有要保存的簇参数")
    return out


def save_cluster_overrides(
    *,
    cluster_merge=None,
    cluster_assign=None,
    cluster_min_size=None,
) -> dict:
    """把簇门槛写进 runtime_overrides.yaml，不改 config.yaml 正文。"""
    patch = normalize_cluster_params(
        cluster_merge=cluster_merge,
        cluster_assign=cluster_assign,
        cluster_min_size=cluster_min_size,
    )
    data = load_runtime_overrides()
    hr = dict(data.get("history_recall") or {})
    hr.update(patch)
    data["history_recall"] = hr
    _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_OVERRIDE_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return hr


class AssignerConfig:
    """派单配置对象：从 config/config.yaml 一次性加载全部派单参数。

    各属性含义：
    - module_keywords:      {模块名: [关键词]}，供 L3 历史召回提取历史工单标签
    - module_classify:      {产品: {功能name: 功能name}}，责任树派生，供 L3 问题域等使用
    - job_level_penalty:    {职级: 惩罚系数}，精排后按职级打折
    - department_routing:   部门路由融合权重与 hard/soft 门槛
    - departments:          部门画像（只认 DB，yaml 不补漏；空则 dept_profiles_missing）
    - product_routing:        产品收紧规则
    - project_manager_id:   Step7 最后一档配置项目经理 users.id（项目字段都空才用）
    - project_manager_name: 配置项目经理姓名（查不到 users 时用）
    - history_recall:       {retrieve_top_k, half_life_days, decay_floor, sim_threshold, fault_code_boost, robot_type_boost, cluster_merge=0.85, cluster_assign=0.80, cluster_*}，L3 双路参数
    - vague_strong_signals: {enabled}，Step2 只认 dispatch_hint=severe
    - llm_recall:           {single_top_k, batch_top_k, single_round_max, batch_size}，L1 单轮/分批人数
    - preferred_floor:      倾向接单人精排保底（默认 0.9）；对接人只打标
    - llm_decision_topk:    Step6 窗口；<=0 不截窗（本版默认 0）
    """

    _CONFIG_DIR = Path(__file__).parent / "config"

    def __init__(self):
        self.module_keywords: Dict[str, list] = {}
        self.module_anchor_texts: Dict[str, str] = {}
        self.module_classify: Dict[str, Dict[str, str]] = {}
        self.module_tree: Dict[str, Any] = {}
        self.job_level_penalty: Dict[int, float] = {}
        # 倾向接单人精排保底：total = max(加权后分数, preferred_floor)。对接人只打标。
        self.preferred_floor: float = 0.9
        # 用户倾向处理人（预留）：前端未传字段时整体不生效；传了即启用。
        self.preferred_assignee_enabled: bool = True
        self.preferred_assignee_force_keep: bool = True
        self.department_routing: Dict[str, Any] = {}
        # 部门派发审查开关：R2 判完部门后，用独立 LLM 单轮复核"部门派得对不对"
        # （post-validator，防单个 LLM 误判部门导致派错）。可回退。
        self.dept_audit_enabled: bool = True
        self.departments: list = []
        self.departments_without_profile: list = []  # 已批准但没写职责描述
        self.dept_profiles_missing: bool = True      # 库里没有任何可用部门画像
        self.product_routing: Dict[str, Any] = {}
        # Step7 配置兜底项目经理：仅当本单对接人、project.project_manager_id 都空时使用。
        self.project_manager_id: str = ""
        self.project_manager_name: str = ""
        self.history_recall: Dict[str, Any] = {}
        # 是否在“摇人吧服务号”项目下强制优先模块总负责人
        self.yaorenba_force_module_owner: bool = True
        # Step6 窗口：<=0 表示精排全量不截窗（本版默认 0）
        self.llm_decision_topk: int = 0
        self.vague_strong_signals: Dict[str, Any] = {"enabled": True}
        self.llm_recall: Dict[str, Any] = {
            "single_top_k": 5, "batch_top_k": 3, "single_round_max": 12, "batch_size": 8,
        }
        self._load_all()

    def _load_all(self):
        """加载全部派单配置。

        模块树（module_tree / module_classify / module_keywords / module_anchor_texts）
        一律从 DB 行表 `module_tree_nodes` 加载（DB 为唯一权威，随树编辑自动更新，
        进程内 AssignerConfig 即内存缓存；reload 时重新拉 DB）。config.yaml 不再作为
        模块树回退源（该兜底已废弃，将彻底移除）。
        """
        config = _load_yaml(self._CONFIG_DIR / "config.yaml") or {}
        # ── 三套 module_* 配置：直接从 DB 行表加载（不再回退 config.yaml 的 module_tree）──
        db_loaded = self._load_module_from_db()
        if db_loaded:
            self.module_tree, self.module_classify, self.module_keywords, self.module_anchor_texts = db_loaded
        else:
            self.module_tree = {}
            self.module_keywords = {}
            self.module_anchor_texts = {}
            self.module_classify = {}
        # job_level_penalty 的 key 在 YAML 中是整数，需显式转 int
        raw = config.get("job_level_penalty", {})
        self.job_level_penalty = {int(k): v for k, v in raw.items()}
        try:
            self.preferred_floor = float(config.get("preferred_floor", 0.9))
        except (TypeError, ValueError):
            self.preferred_floor = 0.9
        # 用户倾向处理人（预留）总开关与强制保留开关（缺失时默认 True/True，前端传字段即启用）
        self.preferred_assignee_enabled = bool(config.get("preferred_assignee_enabled", True))
        self.preferred_assignee_force_keep = bool(config.get("preferred_assignee_force_keep", True))
        self.department_routing = config.get("department_routing", {})
        # 部门画像只认 DB，yaml 不补漏。没有可用画像时 dept_profiles_missing=True，派单 tip 提醒补充。
        self.departments = self._load_departments_from_db()
        self.dept_profiles_missing = not bool(self.departments)
        # 可由 config.yaml 覆盖：部门派发审查开关（false 则不做二次复核）
        self.dept_audit_enabled = bool(config.get("dept_audit_enabled", True))
        self.product_routing = config.get("product_routing", {})
        # 可由 config.yaml 覆盖：兜底转派的项目经理 users.id（默认空串；空=维持现状兜底）
        self.project_manager_id = (config.get("project_manager_id") or "").strip()
        self.project_manager_name = (config.get("project_manager_name") or "").strip()
        self.history_recall = config.get("history_recall", {}) or {}
        self._merge_runtime_overrides()
        # 可由 config.yaml 覆盖：是否在摇人吧服务号项目下优先模块总负责人
        self.yaorenba_force_module_owner = bool(config.get("yaorenba_force_module_owner", True))
        # Step6 窗口：<=0 表示精排全量不截窗（本版默认 0）
        try:
            self.llm_decision_topk = int(config.get("llm_decision_topk", 0))
        except (TypeError, ValueError):
            self.llm_decision_topk = 0
        if self.llm_decision_topk < 0:
            self.llm_decision_topk = 0
        raw_vague = config.get("vague_strong_signals") or {}
        self.vague_strong_signals = {
            "enabled": bool(raw_vague.get("enabled", True)),
        }
        raw_l1 = config.get("llm_recall") or {}
        def _i(key, default):
            try:
                return max(1, int(raw_l1.get(key, default)))
            except (TypeError, ValueError):
                return default
        try:
            single = raw_l1.get("single_top_k", raw_l1.get("final_top_k", 5))
            single = max(1, int(single))
        except (TypeError, ValueError):
            single = 5
        self.llm_recall = {
            "single_top_k": single,
            "batch_top_k": _i("batch_top_k", 3),
            "single_round_max": _i("single_round_max", 12),
            "batch_size": _i("batch_size", 8),
        }

    def _load_module_from_db(self):
        """从 DB 行表 module_tree_nodes 加载模块树并派生三张匹配表。

        聚合逻辑与后端 module_tree_service 一致（按 product / iface_order / func_order 排序，
        构造成 `{产品: {interfaces: [...]}}`），再走 `_build_from_tree` 派生：
            module_classify / module_keywords / module_anchor_texts。

        Returns:
            (module_tree, classify, keywords, anchors)；DB 不可用、表缺失或为空时返回 None，
            调用方将保持三表为空（config.yaml 不再作为模块树回退源）。
        """
        try:
            from app.core.db import SessionLocal
            from app.models.module_tree_node import ModuleTreeNode
        except Exception:
            return None

        try:
            db = SessionLocal()
            try:
                rows = db.query(ModuleTreeNode).order_by(
                    ModuleTreeNode.product,
                    ModuleTreeNode.iface_order,
                    ModuleTreeNode.func_order,
                ).all()
            finally:
                db.close()
        except Exception:
            return None

        if not rows:
            return None

        # 聚合 {产品: {interfaces: [...]}}（与后端 _aggregate_from_nodes 口径一致）
        tree: Dict[str, Any] = {}
        for r in rows:
            pnode = tree.setdefault(r.product, {"interfaces": []})
            iface = next((it for it in pnode["interfaces"] if it["name"] == r.iface_name), None)
            if iface is None:
                iface = {"name": r.iface_name, "functions": []}
                pnode["interfaces"].append(iface)
            iface["functions"].append({
                "id": r.id,
                "name": r.func_name,
                "keywords": r.keywords or [],
                "anchor": r.anchor or "",
                "engineers": r.engineers or [],
            })
        if not tree:
            return None

        classify, keywords, anchors = self._build_from_tree(tree)
        return tree, classify, keywords, anchors

    def _load_departments_from_db(self) -> list:
        """从 DB departments 表加载部门职责画像（供 R2 / 审查）。

        只读 approved 且写了 profile_text 的部门。不回退 yaml。
        表不可用 / 全没写职责 → 返回 []，由调用方打 dept_profiles_missing。
        """
        self.departments_without_profile = []
        try:
            from app.core.db import SessionLocal
            from app.models.organization import Department
        except Exception:
            return []
        try:
            db = SessionLocal()
            try:
                rows = db.query(Department).filter(
                    Department.status == 'approved'
                ).all()
            finally:
                db.close()
        except Exception:
            return []
        result = []
        skipped = []
        for d in rows:
            name = (d.name or "").strip()
            profile = (d.profile_text or "").strip()
            if not name:
                continue
            if not profile:
                skipped.append(name)
                continue
            result.append({
                "name": name,
                "profile_text": profile,
                "examples": d.examples or [],
            })
        self.departments_without_profile = skipped
        return result

    def _merge_runtime_overrides(self):
        """开发者模式改过的簇门槛盖在 yaml 默认值上。"""
        extra = load_runtime_overrides()
        hr = extra.get("history_recall") if isinstance(extra, dict) else None
        if not isinstance(hr, dict):
            return
        if not isinstance(self.history_recall, dict):
            self.history_recall = {}
        for key in CLUSTER_OVERRIDE_KEYS:
            if key in hr and hr[key] is not None:
                self.history_recall[key] = hr[key]

    def reload(self):
        """重新加载配置（配置热更新入口，配合派单缓存失效使用）。"""
        self._load_all()

    @staticmethod
    def _build_from_tree(tree: Dict[str, Any]):
        """从「产品→界面→功能」树生成 module_classify / module_keywords / module_anchor_texts。

        语义：工程师领取的是「某产品→某界面→某功能」的**功能 name（中文）**，
        锚文本按**功能单独**生成（每功能一条锚）。保证下游无感知：
        - module_classify[产品][功能name] = 功能name
              下游拿工程师 responsibility_modules 里的功能 name 查 cat_map.get(mod)，
              得到锚 key 后缀（= 功能 name），再拼「产品-后缀」匹配锚。值=功能名即自洽。
        - module_keywords[产品-功能name]  = [该功能的 keywords 去重]
        - module_anchor_texts[产品-功能name] = 该功能自身的 anchor（无则回退功能名）
        注意：产品名不含「-」，功能 name 为中文，故锚 key 用「产品-功能name」可安全
        被下游 `key.split('-',1)` 拆回 (产品, 功能name)。
        """
        classify: Dict[str, Dict[str, str]] = {}
        keywords: Dict[str, list] = {}
        anchors: Dict[str, str] = {}
        for product, pnode in (tree or {}).items():
            classify[product] = {}
            for iface in (pnode or {}).get("interfaces", []) or []:
                for fn in (iface.get("functions", []) or []):
                    # 领取/锚粒度 = 功能 name（中文）；无 name 时回退功能 key
                    fname = (fn.get("name") or fn.get("key") or "").strip()
                    if not fname:
                        continue
                    classify[product][fname] = fname
                    # 关键词去重保序
                    seen = set()
                    kws = [str(kw).strip() for kw in (fn.get("keywords") or [])]
                    kws = [k for k in kws if k and not (k in seen or seen.add(k))]
                    mod_key = f"{product}-{fname}"
                    if kws:
                        keywords[mod_key] = kws
                    # 锚：优先功能自身 anchor，无则回退功能名
                    anchor = (fn.get("anchor") or "").strip()
                    anchors[mod_key] = anchor or fname
        return classify, keywords, anchors
