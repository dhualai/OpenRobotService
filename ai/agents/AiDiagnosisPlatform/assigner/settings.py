"""配置加载器：统一加载 assigner/config/config.yaml 下的派单配置

配置项与消费方对应关系（与 config.yaml 头部注释保持一致）：
- module_keywords      → recall/history_recall.py（L3 历史召回：历史工单标签提取）
- module_anchor_texts  → recall/semantic_recall.py（L2 语义召回：Embedding 锚文本）
- ranker_weights       → ranking/ranker.py（三路召回加权）
- job_level_penalty    → ranking/ranker.py（职级折扣）
- department_keywords  → filtering/department_filter.py（部门过滤关键词）
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
    - department_keywords:  {部门: {strong, medium, weak}}，供部门过滤分级匹配
    - department_scenes:    {部门: {场景: 描述}}，供部门过滤 embedding 语义补漏
    - department_filter:    {embed_threshold}，部门过滤参数（embedding 匹配阈值）
    - decision_thresholds:  {auto, recommend}，规则兜底决策的置信度阈值
    - load_balance:         {enabled, step}，全体候选人按在途工单数负载均衡（查询带缓存）
    - history_recall:       {top_k, half_life_days, sim_threshold, fault_code_boost, robot_type_boost, decay_weight}，L3 历史召回增强参数
    """

    _CONFIG_DIR = Path(__file__).parent / "config"

    def __init__(self):
        self.module_keywords: Dict[str, list] = {}
        self.module_anchor_texts: Dict[str, str] = {}
        self.module_classify: Dict[str, Dict[str, str]] = {}
        self.ranker_weights: Dict[str, Any] = {}
        self.job_level_penalty: Dict[int, float] = {}
        self.department_keywords: Dict[str, dict] = {}
        self.department_scenes: Dict[str, dict] = {}
        self.department_filter: Dict[str, Any] = {}
        self.decision_thresholds: Dict[str, float] = {}
        self.load_balance: Dict[str, Any] = {}
        self.history_recall: Dict[str, Any] = {}
        self._load_all()

    def _load_all(self):
        """从 config.yaml 读取并填充全部配置属性（缺失时保持默认空值）。"""
        config = _load_yaml(self._CONFIG_DIR / "config.yaml") or {}
        self.module_keywords = config.get("module_keywords", {})
        self.module_anchor_texts = config.get("module_anchor_texts", {})
        self.module_classify = config.get("module_classify", {})
        self.ranker_weights = config.get("ranker_weights", {})
        # job_level_penalty 的 key 在 YAML 中是整数，需显式转 int
        raw = config.get("job_level_penalty", {})
        self.job_level_penalty = {int(k): v for k, v in raw.items()}
        self.department_keywords = config.get("department_keywords", {})
        self.department_scenes = config.get("department_scenes", {})
        self.department_filter = config.get("department_filter", {})
        self.decision_thresholds = config.get("decision_thresholds", {})
        self.load_balance = config.get("load_balance", {})
        self.history_recall = config.get("history_recall", {})

    def reload(self):
        """重新加载配置（配置热更新入口，配合派单缓存失效使用）。"""
        self._load_all()
