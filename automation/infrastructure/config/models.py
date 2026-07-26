from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class ApiConfig(BaseModel):
    base_url: str = Field(default='http://localhost:8000', description='Backend API base URL')
    timeout: int = Field(default=30, description='Request timeout in seconds', ge=1, le=300)


class DatabaseConfig(BaseModel):
    host: str = Field(default='localhost', description='MySQL host')
    port: int = Field(default=3306, description='MySQL port', ge=1, le=65535)
    user: str = Field(default='root', description='MySQL user')
    password: str = Field(default='', description='MySQL password')
    database: str = Field(default='openrobot', description='MySQL database name')


class RedisConfig(BaseModel):
    host: str = Field(default='localhost', description='Redis host')
    port: int = Field(default=6379, description='Redis port', ge=1, le=65535)
    password: str = Field(default='', description='Redis password')
    db: int = Field(default=0, description='Redis database index', ge=0, le=15)


class QdrantConfig(BaseModel):
    host: str = Field(default='localhost', description='Qdrant host')
    port: int = Field(default=6333, description='Qdrant gRPC port', ge=1, le=65535)


class DeepSeekConfig(BaseModel):
    api_key: Optional[str] = Field(default=None, description='DeepSeek API key (from env var)')
    base_url: str = Field(default='https://api.deepseek.com', description='DeepSeek API endpoint')
    model: str = Field(default='deepseek-chat', description='Model name')


class WeChatConfig(BaseModel):
    token: str = Field(default='', description='WeChat verification token')
    encoding_aes_key: str = Field(default='', description='WeChat encoding AES key')
    app_id: str = Field(default='', description='WeChat App ID')


class PlaywrightConfig(BaseModel):
    browser: str = Field(default='chromium', description='Browser type: chromium/firefox/webkit')
    headless: bool = Field(default=True, description='Run browser in headless mode')


class AutomationConfig(BaseModel):
    env: str = Field(default='local', description='Active environment name')
    api: ApiConfig = Field(default_factory=ApiConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    wechat: WeChatConfig = Field(default_factory=WeChatConfig)
    playwright: PlaywrightConfig = Field(default_factory=PlaywrightConfig)
