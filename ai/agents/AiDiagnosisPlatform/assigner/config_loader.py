"""配置加载器：统一加载 assigner/config/ 下的配置"""

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
    _CONFIG_DIR = Path(__file__).parent / "config"

    def __init__(self):
        self.module_keywords: Dict[str, list] = {}
        self.ranker_weights: Dict[str, Any] = {}
        self.job_level_penalty: Dict[int, float] = {}
        self.department_scopes: Dict[str, dict] = {}
        self.decision_thresholds: Dict[str, float] = {}
        self._load_all()

    def _load_all(self):
        config = _load_yaml(self._CONFIG_DIR / "assigner_config.yaml") or {}
        self.module_keywords = config.get("module_keywords", {})
        self.ranker_weights = config.get("ranker_weights", {})
        raw = config.get("job_level_penalty", {})
        self.job_level_penalty = {int(k): v for k, v in raw.items()}
        self.department_scopes = config.get("department_scopes", {})
        self.decision_thresholds = config.get("decision_thresholds", {})

    def reload(self):
        self._load_all()
