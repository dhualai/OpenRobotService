"""配置加载器：统一加载 assigner/config/config.yaml 下的派单配置

配置项与消费方对应关系（与 config.yaml 头部注释保持一致）：
- module_keywords      → recall/history_recall.py（L3 历史召回：历史工单标签提取）
- module_anchor_texts  → recall/semantic_recall.py（L2 语义召回：Embedding 锚文本）
- ranker_weights       → ranking/ranker.py（三路召回加权）
- job_level_penalty    → ranking/ranker.py（职级折扣）
- department_keywords  → filtering/dept_router.py（R5 strong 关键词）
- department_routing   → filtering/dept_router.py（R2/R3 融合与门槛）
- departments          → filtering/signals/llm_dept_signal.py（部门画像）
- product_routing      → filtering/product_router.py（产品收紧）
- decision_thresholds  → ranking/fallback_decision.py（兜底置信度阈值）
"""

from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
    def _load_yaml(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
except ImportError:
    raise RuntimeError("PyYAML 是必要依赖，请安装: pip install pyyaml")


def _merge_departments(db_depts: list, cfg_depts: list) -> list:
    """合并 DB 与 config 的部门画像：DB 优先，config 按部门名补漏。

    - DB 已配置职责描述的部门 → 用 DB 最新；
    - DB 未配置、config 有该部门画像 → 用 config 补上（过渡期兼容，避免部分迁移丢数据）。
    """
    merged: Dict[str, dict] = {}
    for d in cfg_depts:
        name = (d or {}).get("name") or ""
        if name:
            merged.setdefault(name, d)          # 先放 config，DB 同名覆盖
    for d in db_depts:
        name = (d or {}).get("name") or ""
        if name:
            merged[name] = d                    # DB 优先覆盖
    return list(merged.values())


class AssignerConfig:
    """派单配置对象：从 config/config.yaml 一次性加载全部派单参数。

    各属性含义：
    - module_keywords:      {模块名: [关键词]}，供 L3 历史召回提取历史工单标签
    - module_anchor_texts:  {模块名: 锚文本}，供 L2 语义召回做 Embedding 比对
    - module_classify:      {产品: {功能name: 功能name}}，供 L2 语义召回把工程师功能名映射到「产品-功能」锚
    - ranker_weights:       {llm_match, semantic_match, history_match} 三路权重
    - job_level_penalty:    {职级: 惩罚系数}，精排后按职级打折
    - department_keywords:  {部门: {strong: [...]}}，R5 强关键词
    - department_routing:   部门路由融合权重与 hard/soft 门槛
    - departments:          部门画像（R2 LLM 分类）
    - product_routing:        产品收紧规则
    - department_rules:       R1 确定性规则（预留）
    - decision_thresholds:  {auto, recommend}，规则兜底决策的置信度阈值
    - load_balance:         {enabled, step}，全体候选人按在途工单数负载均衡（查询带缓存）
    - history_recall:       {top_k, half_life_days, sim_threshold, fault_code_boost, robot_type_boost, decay_weight}，L3 历史召回增强参数
    """

    _CONFIG_DIR = Path(__file__).parent / "config"

    def __init__(self):
        self.module_keywords: Dict[str, list] = {}
        self.module_anchor_texts: Dict[str, str] = {}
        self.module_classify: Dict[str, Dict[str, str]] = {}
        self.module_tree: Dict[str, Any] = {}
        self.ranker_weights: Dict[str, Any] = {}
        self.job_level_penalty: Dict[int, float] = {}
        self.contact_bonus: float = 2.0
        # 用户倾向处理人（预留）：前端未传字段时整体不生效；传了即启用。加权系数复用 contact_bonus。
        self.preferred_assignee_enabled: bool = True
        self.preferred_assignee_force_keep: bool = True
        self.department_keywords: Dict[str, dict] = {}
        self.department_routing: Dict[str, Any] = {}
        # 部门派发审查开关：R2 判完部门后，用独立 LLM 单轮复核"部门派得对不对"
        # （post-validator，防单个 LLM 误判部门导致派错）。可回退。
        self.dept_audit_enabled: bool = True
        self.departments: list = []
        self.product_routing: Dict[str, Any] = {}
        self.department_rules: Dict[str, Any] = {}
        self.decision_thresholds: Dict[str, float] = {}
        self.load_balance: Dict[str, Any] = {}
        self.history_recall: Dict[str, Any] = {}
        # L2 锚文本语义召回开关：有 LLM(L1) 强语义判断后，锚文本/关键词匹配反而干扰
        # （对产品经理等"负责非功能模块"的候选人结构化不公平，易被表面字眼误导）。
        # 默认按 config.yaml 控制（当前置 false，只看 L1 LLM + L3 历史）。
        self.semantic_recall_enabled: bool = True
        # 新增：LLM 覆写数值排名的最小差距阈值（当 top - second >= 此阈值时，直接选 top，LLM 不覆写）
        self.llm_respect_ranking_threshold: float = 0.3
        # 新增：是否在“摇人吧服务号”项目下强制优先模块总负责人
        self.yaorenba_force_module_owner: bool = True
        # 新增：LLM 综合决策的覆写窗口 = 精排前 K 名（default 3）。
        # 即使触发让 LLM 重选，也只能在精排 Top-K 内选，不能选到低排名的人。
        self.llm_decision_topk: int = 3
        # 新增：精排第一名的评分阈值。总分>=此阈值时直接采用第一名（保证派单尊重排名）；
        # 仅当第一名总分<此阈值（说明整体得分很低、候选都不理想）时才触发 LLM 再决定一遍。
        self.llm_decision_low_score_threshold: float = 0.5
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
        self.ranker_weights = config.get("ranker_weights", {})
        # job_level_penalty 的 key 在 YAML 中是整数，需显式转 int
        raw = config.get("job_level_penalty", {})
        self.job_level_penalty = {int(k): v for k, v in raw.items()}
        # 项目对接人精排加权系数（≥1 起加权，=1 不加权；允许空/缺失则默认 2.0）
        try:
            self.contact_bonus = float(config.get("contact_bonus", 2.0))
        except (TypeError, ValueError):
            self.contact_bonus = 2.0
        # 用户倾向处理人（预留）总开关与强制保留开关（缺失时默认 True/True，前端传字段即启用）
        self.preferred_assignee_enabled = bool(config.get("preferred_assignee_enabled", True))
        self.preferred_assignee_force_keep = bool(config.get("preferred_assignee_force_keep", True))
        self.department_keywords = config.get("department_keywords", {})
        self.department_routing = config.get("department_routing", {})
        # 部门画像：以 DB departments 表为权威（随部门职责维护热更新），
        # config.yaml 按部门名补漏（DB 未配置职责描述的部门用 config 画像，兼容旧配置过渡期）。
        db_depts = self._load_departments_from_db() or []
        cfg_depts = config.get("departments", []) or []
        self.departments = _merge_departments(db_depts, cfg_depts)
        # 可由 config.yaml 覆盖：部门派发审查开关（false 则不做二次复核）
        self.dept_audit_enabled = bool(config.get("dept_audit_enabled", True))
        self.product_routing = config.get("product_routing", {})
        self.department_rules = config.get("department_rules", {})
        self.decision_thresholds = config.get("decision_thresholds", {})
        self.load_balance = config.get("load_balance", {})
        self.history_recall = config.get("history_recall", {})
        # 可由 config.yaml 覆盖：L2 锚文本语义召回开关（false = 只看 L1 LLM + L3 历史）
        self.semantic_recall_enabled = bool(config.get("semantic_recall_enabled", True))
        # 可由 config.yaml 覆盖：LLM 覆写数值排名的最小差距阈值
        try:
            self.llm_respect_ranking_threshold = float(config.get("llm_respect_ranking_threshold", 0.3))
        except (TypeError, ValueError):
            self.llm_respect_ranking_threshold = 0.3
        # 可由 config.yaml 覆盖：是否在摇人吧服务号项目下优先模块总负责人
        self.yaorenba_force_module_owner = bool(config.get("yaorenba_force_module_owner", True))
        # 可由 config.yaml 覆盖：LLM 覆写窗口（精排前 K 名）
        try:
            self.llm_decision_topk = int(config.get("llm_decision_topk", 3))
        except (TypeError, ValueError):
            self.llm_decision_topk = 3
        if self.llm_decision_topk < 1:
            self.llm_decision_topk = 1
        # 可由 config.yaml 覆盖：第一名评分阈值（低于此值才触发 LLM 再决定）
        try:
            self.llm_decision_low_score_threshold = float(config.get("llm_decision_low_score_threshold", 0.6))
        except (TypeError, ValueError):
            self.llm_decision_low_score_threshold = 0.6

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

    def _load_departments_from_db(self) -> Optional[list]:
        """从 DB departments 表加载部门职责画像（供 R2 LLM 部门分类）。

        读 approved 部门组装 [{name, profile_text, examples}]，与 config.yaml 结构一致。
        表不可用 / 无批准部门 / 部门未配职责描述时返回 None（调用方回退 config.yaml）。
        """
        try:
            from app.core.db import SessionLocal
            from app.models.organization import Department
        except Exception:
            return None
        try:
            db = SessionLocal()
            try:
                rows = db.query(Department).filter(
                    Department.status == 'approved'
                ).all()
            finally:
                db.close()
        except Exception:
            return None
        result = []
        for d in rows:
            name = (d.name or "").strip()
            profile = (d.profile_text or "").strip()
            if not name or not profile:
                continue  # 无职责描述的部门不参与 R2 分类
            result.append({
                "name": name,
                "profile_text": profile,
                "examples": d.examples or [],
            })
        return result or None

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
