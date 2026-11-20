"""企业微信项目数据源插件。

注册 WecomProjectAdapter 供同步使用。
"""
from app.integrations.registry import registry
from app.integrations.sources.wecom.adapter import WecomProjectAdapter

registry.register(WecomProjectAdapter())

__all__ = ["WecomProjectAdapter"]