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
from typing import Any, Dict

try:
    import yaml
    def _load_yaml(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
except ImportError:
    raise RuntimeError("PyYAML 是必要依赖，请安装: pip install pyyaml")


class AssignerConfig:
    """派单配置对象：从 config/config.yaml 一次性加载全部派单参数。

    各属性含义：
    - module_keywords:      {模块名: [关键词]}，供 L3 历史召回提取历史工单标签
    - module_anchor_texts:  {模块名: 锚文本}，供 L2 语义召回做 Embedding 比对
    - module_classify:      {产品: {细分模块: 类别}}，供 L2 语义召回把细分模块映射到「产品-类别」锚
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
        self.departments: list = []
        self.product_routing: Dict[str, Any] = {}
        self.department_rules: Dict[str, Any] = {}
        self.decision_thresholds: Dict[str, float] = {}
        self.load_balance: Dict[str, Any] = {}
        self.history_recall: Dict[str, Any] = {}
        # 新增：LLM 覆写数值排名的最小差距阈值（当 top - second >= 此阈值时，直接选 top，LLM 不覆写）
        self.llm_respect_ranking_threshold: float = 0.3
        # 新增：是否在“摇人吧服务号”项目下强制优先模块总负责人
        self.yaorenba_force_module_owner: bool = True
        self._load_all()

    def _load_all(self):
        """从 config.yaml 读取并填充全部配置属性（缺失时保持默认空值）。"""
        config = _load_yaml(self._CONFIG_DIR / "config.yaml") or {}
        # ── 三套 module_* 配置：优先从「产品→界面→功能」树自动生成，否则回退手工配置 ──
        module_tree = config.get("module_tree", {})
        if module_tree:
            self.module_tree = module_tree
            self.module_classify, self.module_keywords, self.module_anchor_texts = \
                self._build_from_tree(module_tree)
        else:
            self.module_tree = {}
            self.module_keywords = config.get("module_keywords", {})
            self.module_anchor_texts = config.get("module_anchor_texts", {})
            self.module_classify = config.get("module_classify", {})
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
        self.departments = config.get("departments", [])
        self.product_routing = config.get("product_routing", {})
        self.department_rules = config.get("department_rules", {})
        self.decision_thresholds = config.get("decision_thresholds", {})
        self.load_balance = config.get("load_balance", {})
        self.history_recall = config.get("history_recall", {})
        # 可由 config.yaml 覆盖：LLM 覆写数值排名的最小差距阈值
        try:
            self.llm_respect_ranking_threshold = float(config.get("llm_respect_ranking_threshold", 0.3))
        except (TypeError, ValueError):
            self.llm_respect_ranking_threshold = 0.3
        # 可由 config.yaml 覆盖：是否在摇人吧服务号项目下优先模块总负责人
        self.yaorenba_force_module_owner = bool(config.get("yaorenba_force_module_owner", True))

    def reload(self):
        """重新加载配置（配置热更新入口，配合派单缓存失效使用）。"""
        self._load_all()

    @staticmethod
    def _build_from_tree(tree: Dict[str, Any]):
        """从「产品→界面→功能」树生成 module_classify / module_keywords / module_anchor_texts。

        映射约定（保证下游无感知）：
        - module_classify[产品][功能key] = 界面key   （功能key 需与工程师 responsibility_modules 细分名一致）
        - module_keywords[产品-界面key]  = [该界面下所有功能的 keywords 去重]
        - module_anchor_texts[产品-界面key] = 该界面下所有功能的 anchor 逗号拼接
        """
        classify: Dict[str, Dict[str, str]] = {}
        keywords: Dict[str, list] = {}
        anchors: Dict[str, str] = {}
        for product, pnode in (tree or {}).items():
            classify[product] = {}
            for iface in (pnode or {}).get("interfaces", []) or []:
                ikey = (iface.get("key") or iface.get("name") or "").strip()
                if not ikey:
                    continue
                kw_list: list = []
                anchor_parts: list = []
                for fn in (iface.get("functions", []) or []):
                    fkey = (fn.get("key") or fn.get("name") or "").strip()
                    if fkey:
                        classify[product][fkey] = ikey
                    for kw in (fn.get("keywords") or []):
                        if kw:
                            kw_list.append(str(kw))
                    if fn.get("anchor"):
                        anchor_parts.append(str(fn["anchor"]))
                mod_key = f"{product}-{ikey}"
                # 关键词去重保序
                seen = set()
                keywords[mod_key] = [k for k in kw_list if not (k in seen or seen.add(k))]
                anchors[mod_key] = "，".join(anchor_parts) if anchor_parts else mod_key
        return classify, keywords, anchors
