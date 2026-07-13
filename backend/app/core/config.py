import os
import re
from typing import Optional, List, Dict
from pydantic_settings import BaseSettings
from pydantic import Field, model_validator

class Settings(BaseSettings):
    APP_NAME: str = Field(default="用户认证与权限管理服务")
    APP_VERSION: str = Field(default="1.0.0")
    API_V1_STR: str = Field(default="/api")
    AUTH_STR: str = Field(default="/auth")
    
    APP_ENV: str = Field(default="dev")
    
    SECRET_KEY: str = Field(default="")
    JWT_SECRET: str = Field(default="")
    ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7)
    
    BACKEND_CORS_ORIGINS: List[str] = Field(default=["*"])
    FRONTEND_BASE_URL: str = Field(default="http://127.0.0.1:5173")
    
    DATABASE_URL: Optional[str] = Field(default=None)
    
    ADMIN_USERNAME: str = Field(default="admin")
    ADMIN_PASSWORD: str = Field(default="123456")
    
    @property
    def DB_CONFIG(self) -> dict:
        if not self.DATABASE_URL:
            return {
                'user': 'root',
                'password': '123456',
                'host': '127.0.0.1',
                'port': '3306',
                'database': 'helpdesk'
            }
        match = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)', self.DATABASE_URL)
        if match:
            return {
                'user': match.group(1),
                'password': match.group(2),
                'host': match.group(3),
                'port': match.group(4),
                'database': match.group(5)
            }
        return {
            'user': 'root',
            'password': '123456',
            'host': '127.0.0.1',
            'port': '3306',
            'database': 'helpdesk'
        }
    
    DATA_SERVICE_URL: str = Field(default="http://localhost:8002")
    DATA_DEBUG_SERVICE_URL: str = Field(default="http://localhost:8012")
    AUTH_SERVICE_URL: str = Field(default="http://localhost:8001")
    AI_SERVICE_URL: str = Field(default="http://localhost:8010")
    FAQ_SERVER_URL: str = Field(default="http://localhost:8005")
    
    USER_CENTER_BASE_URL: str = Field(default="http://localhost:8001")
    
    MINIO_ENDPOINT: str = Field(default="localhost:9000")
    MINIO_ACCESS_KEY: str = Field(default="")
    MINIO_SECRET_KEY: str = Field(default="")
    MINIO_BUCKET: str = Field(default="helpdesk")
    MINIO_SECURE: bool = Field(default=False)
    
    COMMENT_BUCKET: str = Field(default="helpdesk-comment")
    
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_DB: int = Field(default=0)
    
    AI_MODEL_URL: str = Field(default="http://localhost:8005")
    AI_MODEL_NAME: str = Field(default="qwen2")
    AI_API_KEY: str = Field(default="")
    
    WECHAT_API_BASE_URL: str = Field(default="https://api.weixin.qq.com")
    
    WECHAT_TOKEN: str = Field(default="")
    WECHAT_APP_ID: str = Field(default="")
    WECHAT_APP_SECRET: str = Field(default="")
    WECHAT_ENCODING_AES_KEY: str = Field(default="")
    
    SUGGESTIONS_NOTIFICATION_USERS: List[str] = Field(default=[])
    
    MQTT_BROKER: str = Field(default="")
    MQTT_PORT: int = Field(default=8084)
    MQTT_USERNAME: str = Field(default="")
    MQTT_PASSWORD: str = Field(default="")
    MQTT_CLIENT_ID: str = Field(default="DAS_MQTT_WX")
    
    @property
    def WECHAT_CONFIG(self) -> Dict:
        return {
            'token': self.WECHAT_TOKEN,
            'app_id': self.WECHAT_APP_ID,
            'app_secret': self.WECHAT_APP_SECRET,
            'encoding_aes_key': self.WECHAT_ENCODING_AES_KEY
        }
    
    @property
    def WECHAT_TOKEN_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/token"
    
    @property
    def WECHAT_SEND_MESSAGE_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/message/custom/send"
    
    @property
    def WECHAT_TEMPLATE_MESSAGE_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/message/template/send"
    
    @property
    def WECHAT_USER_LIST_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/user/get"
    
    @property
    def WECHAT_MENU_CREATE_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/menu/create"
    
    @property
    def WECHAT_MENU_GET_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/menu/get"
    
    @property
    def WECHAT_MENU_DELETE_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/menu/delete"
    
    @property
    def WECHAT_MENU_ADDCONDITIONAL_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/menu/addconditional"
    
    @property
    def WECHAT_MENU_DELCONDITIONAL_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/menu/delconditional"
    
    @property
    def WECHAT_MENU_TRYCATCH_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/menu/trymatch"
    
    @property
    def WECHAT_TAGS_GET_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/tags/get"
    
    @property
    def WECHAT_TAGS_CREATE_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/tags/create"
    
    @property
    def WECHAT_TAGS_UPDATE_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/tags/update"
    
    @property
    def WECHAT_TAGS_DELETE_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/tags/delete"
    
    @property
    def WECHAT_TAGS_MEMBERS_BATCHTAGGING_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/tags/members/batchtagging"
    
    @property
    def WECHAT_TAGS_MEMBERS_BATCHUNTAGGING_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/tags/members/batchuntagging"
    
    @property
    def WECHAT_TAGS_MEMBERS_GET_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/user/tag/get"
    
    @property
    def WECHAT_TAGS_GET_ID_LIST_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/tags/getidlist"
    
    @model_validator(mode='after')
    def validate_secret_key(self) -> 'Settings':
        if not self.SECRET_KEY and self.JWT_SECRET:
            self.SECRET_KEY = self.JWT_SECRET
        return self
    
    @model_validator(mode='after')
    def validate_production_config(self) -> 'Settings':
        if self.APP_ENV == 'production':
            errors = []
            if not self.DATABASE_URL:
                errors.append("DATABASE_URL is required in production")
            if not self.SECRET_KEY:
                errors.append("SECRET_KEY is required in production")
            if not self.WECHAT_APP_ID:
                errors.append("WECHAT_APP_ID is required in production")
            if not self.WECHAT_APP_SECRET:
                errors.append("WECHAT_APP_SECRET is required in production")
            if errors:
                raise ValueError(f"Production configuration errors: {', '.join(errors)}")
        return self
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "allow"

settings = Settings()