"""配置加载器：统一加载 assigner/config/ 下的配置"""

import re
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
    def _load_yaml(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
except ImportError:
    raise RuntimeError("PyYAML 是必要依赖，请安装: pip install pyyaml")


def _load_prompts_txt(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    prompts = {}
    for match in re.finditer(r"^===\s*(.+?)\s*===(.*?)(?=^===|\Z)", content, re.MULTILINE | re.DOTALL):
        prompts[match.group(1).strip()] = match.group(2).strip()
    return prompts


class AssignerConfig:
    _CONFIG_DIR = Path(__file__).parent / "config"

    def __init__(self):
        self.module_keywords: Dict[str, list] = {}
        self.category_module_map: Dict[str, list] = {}
        self.ranker_weights: Dict[str, Any] = {}
        self.job_level_penalty: Dict[int, float] = {}
        self.decision_thresholds: Dict[str, float] = {}
        self.prompts: Dict[str, str] = {}
        self._load_all()

    def _load_all(self):
        config = _load_yaml(self._CONFIG_DIR / "assigner_config.yaml") or {}
        self.module_keywords = config.get("module_keywords", {})
        self.category_module_map = config.get("category_module_map", {})
        self.ranker_weights = config.get("ranker_weights", {})
        raw = config.get("job_level_penalty", {})
        self.job_level_penalty = {int(k): v for k, v in raw.items()}
        self.decision_thresholds = config.get("decision_thresholds", {})
        self.prompts = _load_prompts_txt(self._CONFIG_DIR / "prompts.txt")

    def reload(self):
        self._load_all()
