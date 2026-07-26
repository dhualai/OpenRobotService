'''automation.config - Configuration module for the automation framework.

This module provides environment-aware configuration loading from YAML profiles.
It is the single source of truth for all framework configuration settings.

Usage:
    from automation.infrastructure.config import load_config, get_env, ConfigEnv

    config = load_config()                      # auto-detect env
    config = load_config(env='sit')             # explicit env
    api_url = config.api.base_url
    db_cfg = config.database
'''

from automation.infrastructure.config.enums import ConfigEnv
from automation.infrastructure.config.models import (
    ApiConfig,
    AutomationConfig,
    DatabaseConfig,
    DeepSeekConfig,
    PlaywrightConfig,
    QdrantConfig,
    RedisConfig,
    WeChatConfig,
)
from automation.infrastructure.config.settings import get_env, is_env, load_config

__all__ = [
    'load_config',
    'get_env',
    'is_env',
    'ConfigEnv',
    'AutomationConfig',
    'ApiConfig',
    'DatabaseConfig',
    'RedisConfig',
    'QdrantConfig',
    'DeepSeekConfig',
    'WeChatConfig',
    'PlaywrightConfig',
]
