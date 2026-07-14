# 路径: backend/app/ai/__init__.py
"""
AI 模块初始化
导出主要组件供外部调用
"""

# ============================================================
# 领域身份（所有 LLM 调用都必须注入）
# ============================================================
SYSTEM_PROMPT = (
    "你是工业移动机器人（AGV/AMR）领域的技术支持专家，领域锁定，不做通用服务台。"
    "你所服务的产品是 USP（Universal Scheduling Platform）大调度系统，"
    "用于 AGV/AMR 的调度管理、车辆管理、设备管理、地图编辑与监控运维。"
    "你的用户可能是工程师、操作员或管理人员，直接针对问题本身回答，不用区分角色。"
    "USP 是网页端系统（PC浏览器访问），没有移动端APP。回答中严禁提及'手机'、'移动端'、'APP'、'屏幕阅读'等移动端概念。"
    "回答要求：清晰、结构化、适合网页端阅读。"
    "严禁给出手机、电脑等消费电子产品的通用回答，严禁超出 AGV/AMR 领域。"
)

from app.ai.config import get_ai_config, validate_ai_config, AIConfig
from app.ai.exceptions import (
    AIError,
    AITimeoutError,
    RetrieveEmptyError,
    LowConfidenceError,
    IntentNotMatchError,
    JSONParseError,
    ContextRewriteError,
    EmbeddingError,
    ServiceUnavailableError,
)

__all__ = [
    # 配置
    "get_ai_config",
    "validate_ai_config",
    "AIConfig",
    # 异常
    "AIError",
    "AITimeoutError",
    "RetrieveEmptyError",
    "LowConfidenceError",
    "IntentNotMatchError",
    "JSONParseError",
    "ContextRewriteError",
    "EmbeddingError",
    "ServiceUnavailableError",
]
