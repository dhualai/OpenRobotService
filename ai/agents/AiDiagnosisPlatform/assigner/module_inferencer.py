"""责任模块推断器：基于问题描述 + 车型推断责任模块

设计原则：
- LLM 优先推断（异步，使用 ai.core）
- 基础设施不可用时，回退到规则 / 关键词模式
- 规则关键词从 assigner_config.yaml 动态加载，支持热更新
"""

import json
import re
from typing import List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig


class ModuleInferencer:
    """责任模块推断器"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    async def ainfer(self, description: str, vehicle_model: Optional[str] = None) -> List[str]:
        """异步推断责任模块列表。"""
        # 1. 优先 LLM 推断
        try:
            modules = await self._llm_infer(description, vehicle_model)
            if modules:
                return modules
        except Exception:
            pass

        # 2. 规则兜底
        return self._rule_infer(description, vehicle_model)

    async def _llm_infer(self, description: str, vehicle_model: Optional[str] = None) -> List[str]:
        """调用 LLM 进行模块推断。"""
        prompt_template = self._config.prompts.get("module_inference", "")
        vehicle_info = f"\n车型: {vehicle_model}" if vehicle_model else ""
        prompt = prompt_template.format(
            description=description, vehicle_info=vehicle_info
        )

        from ai.core import get_llm_client
        llm = await get_llm_client()
        response = await llm.complete(prompt)

        try:
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                modules = data.get("modules", [])
                valid_modules = set(self._config.module_keywords.keys())
                return [m for m in modules if m in valid_modules]
        except (json.JSONDecodeError, AttributeError):
            pass
        return []

    def _rule_infer(self, description: str, vehicle_model: Optional[str] = None) -> List[str]:
        """基于关键词规则推断责任模块。"""
        text = (description or "").lower()
        if vehicle_model:
            text += " " + vehicle_model.lower()

        matched = []
        for module, keywords in self._config.module_keywords.items():
            for kw in keywords:
                if kw.lower() in text:
                    matched.append(module)
                    break
        return matched
