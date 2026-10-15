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

    # 服务监听端口（main.py 启动时读取，优先来自 backend/.env）
    PORT: int = Field(default=8400, description="后端服务监听端口")

    SECRET_KEY: str = Field(default="")
    JWT_SECRET: str = Field(default="")
    ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7)
    
    BACKEND_CORS_ORIGINS: List[str] = Field(default=["*"])
    FRONTEND_BASE_URL: str = Field(default="http://127.0.0.1:5173")
    
    DATABASE_URL: Optional[str] = Field(default=None)
    
    ADMIN_USERNAME: str = Field(default="admin")
    ADMIN_PASSWORD: str = Field(default="usp2026@EP")
    
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
    
    AI_SERVICE_URL: str = Field(default="http://localhost:8401")
    
    MINIO_ENDPOINT: str = Field(default="localhost:9000")
    MINIO_ACCESS_KEY: str = Field(default="")
    MINIO_SECRET_KEY: str = Field(default="")
    MINIO_BUCKET: str = Field(default="helpdesk")
    MINIO_SECURE: bool = Field(default=False)
    # 对象存储请求路径前缀。
    # - 本地直连 MinIO 时留空（""）；
    # - 生产经 nginx 网关代理时设为 "/minio-api"（见 deploy/nginx/conf/nginx.conf）。
    MINIO_API_PREFIX: str = Field(default="")
    # 图片类资源专用 bucket（预签名 URL 路由会遍历匹配），默认与 MINIO_BUCKET 区分开。
    FILE_IMAGES: str = Field(default="helpdesk-images")
    
    COMMENT_BUCKET: str = Field(default="helpdesk-comment")

    # ===== 阿里云 OSS（大文件分流：>1GB 写 OSS；同步 OSS 桶内容到 DB 资源表）=====
    ALIYUN_OSS_ACCESS_KEY_ID: str = Field(default="")
    ALIYUN_OSS_ACCESS_KEY_SECRET: str = Field(default="")
    ALIYUN_OSS_ENDPOINT: str = Field(default="https://oss-cn-hangzhou.aliyuncs.com")
    ALIYUN_OSS_REGION: str = Field(default="cn-hangzhou")
    ALIYUN_OSS_BUCKET: str = Field(default="")
    # 桶内统一上传目录前缀（空字符串=桶根目录），例 "uploads" -> 所有文件落在 bucket/uploads/
    ALIYUN_OSS_UPLOAD_DIR: str = Field(default="")
    # 分片上传每片大小（MB），仅对 >100MB 大文件生效
    ALIYUN_OSS_PART_SIZE_MB: int = Field(default=10)
    # 资源预签名下载链接有效期（分钟），修改 .env 后重启后端生效
    RESOURCE_DOWNLOAD_URL_EXPIRES_MINUTES: int = Field(default=3, description="资源分享/下载预签名 URL 有效期（分钟）")
    
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_DB: int = Field(default=0)
    
    AI_MODEL_URL: str = Field(default="https://api.deepseek.com")
    AI_MODEL_NAME: str = Field(default="deepseek-v4-flash")
    AI_API_KEY: str = Field(default="")
    
    AI_SERVICE_PROVIDER: str = Field(default="openai")
    LLM_API_KEY: str = Field(default="")
    LLM_API_URL: str = Field(default="https://api.deepseek.com/chat/completions")
    LLM_MODEL_NAME: str = Field(default="deepseek-v4-flash")
    LLM_TEMPERATURE: float = Field(default=0.7)
    LLM_STREAM: bool = Field(default=False)
    # 二次派单感知增强（M3 高情商回复）：未派到指定人时，tip_detail 是否用 AI 润色。
    # 默认 False=纯模板（零 LLM 成本、文案确定可复用）；True 时才调 ModelService 润色（失败仍降级模板）。
    REDISPATCH_TIP_AI_POLISH: bool = Field(default=False)

    # 协商回合上限：接单人↔提单人来回应答最大次数（含首次）。
    # 达到最后一轮前端展示升级上报，用户点击现有升级上报通道替代管理员介入。
    TICKET_STEP_MAX_NEGOTIATION_ROUNDS: int = Field(default=3)

    CUSTOM_AI_BASE_URL: str = Field(default="")
    CUSTOM_AI_API_PATH: str = Field(default="/api/ask")
    
    MQTT_TOPIC_REQUEST: str = Field(default="ai/request")
    MQTT_TOPIC_RESPONSE: str = Field(default="ai/response")
    MQTT_USER: str = Field(default="")
    MQTT_PASSWORD: str = Field(default="")
    
    WECHAT_API_BASE_URL: str = Field(default="https://api.weixin.qq.com")

    WECHAT_TOKEN: str = Field(default="")
    WECHAT_APP_ID: str = Field(default="")
    WECHAT_APP_SECRET: str = Field(default="")
    WECHAT_ENCODING_AES_KEY: str = Field(default="")

    # 企业微信群机器人 webhook（消息推送用）。形如：
    # https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
    # 留空则企业微信通知渠道不启用。
    WECHAT_WORK_WEBHOOK_URL: str = Field(default="")

    SUGGESTIONS_NOTIFICATION_USERS: List[str] = Field(default=[])

    # ===== 微信支付（独立 Go 微服务 wxpay-service，见 docs/微信支付接入技术方案.md）=====
    # 业务形态：奖励记录 + 立即提现（无卡包）。密钥全部在 Go 侧，本服务不持有。
    WXPAY_ENABLED: bool = Field(default=False, description="微信支付总开关，关闭时相关接口返回 503")
    WXPAY_SERVICE_URL: str = Field(default="http://127.0.0.1:9100", description="wxpay-service 内网地址")
    WXPAY_INTERNAL_TOKEN: str = Field(default="", description="内部调用鉴权令牌（FastAPI<->Go 双向共用）")
    WXPAY_MCH_ID: str = Field(default="", description="商户号，仅用于前端调起参数回传")
    WXPAY_APP_ID: str = Field(default="", description="服务号 appid，留空则回退 WECHAT_APP_ID")
    WXPAY_TRANSFER_SCENE_ID: str = Field(default="1000", description="商家转账场景 ID")
    WXPAY_USER_RECV_PERCEPTION: str = Field(default="现金奖励", description="收款感知：现金奖励/活动奖励")
    WXPAY_REWARD_EXPIRE_DAYS: int = Field(default=30, description="奖励有效期（天），超期未提现即失效")
    WXPAY_MIN_WITHDRAW_FEN: int = Field(default=100, description="最低提现金额（分），默认 1 元")
    WXPAY_MAX_TRANSFER_FEN: int = Field(default=20000, description="单笔转账上限（分），默认 200 元")
    WXPAY_RECONCILE_INTERVAL_MINUTES: int = Field(default=10, description="兜底查单/巡检周期（分钟）")
    WXPAY_SERVICE_TIMEOUT: float = Field(default=10.0, description="调用 Go 服务超时（秒）")
    
    MQTT_BROKER: str = Field(default="")
    MQTT_PORT: int = Field(default=8084)
    MQTT_USERNAME: str = Field(default="")
    MQTT_PASSWORD: str = Field(default="")
    MQTT_CLIENT_ID: str = Field(default="DAS_MQTT_WX")

    # ===== Meilisearch 全文检索（可降级：MEILI_ENABLED=False 时回退 ilike）=====
    MEILI_ENABLED: bool = Field(default=True)
    MEILI_HOST_URL: str = Field(default="http://localhost:7700")
    MEILI_MASTER_KEY: str = Field(default="")

    # ===== 外部任务源（插件化，见 INTEGRATION_DESIGN.md）=====
    TASK_SOURCES_ENABLED: List[str] = Field(default=[])   # 启用的任务源，如 ["zentao"]
    ZENTAO_BASE_URL: str = Field(default="")
    ZENTAO_ACCOUNT: str = Field(default="")
    ZENTAO_PASSWORD: str = Field(default="")
    ZENTAO_VERIFY_SSL: bool = Field(default=True)
    ZENTAO_PROJECT_IDS: str = Field(default="")            # "[1,2,3]" 或 "1,2,3" 或 "1;2;3"
    HELPDESK_SYNC_API_KEY: str = Field(default="")          # 外部任务源同步接口 API Key（Airflow 用，X-API-Key）
    ROBOT_ALARM_API_KEY: str = Field(default="")            # 设备报警提醒对外接口 API Key（URL ?key= 方式鉴权）
    USP_PROJECT_SYNC_KEY: str = Field(default="")           # 项目导出接口内部服务鉴权（X-API-Key Header，跳过 JWT 权限码校验）

    # ===== 项目在线状态监控（/api/health?project_code=xxx 心跳判定）=====
    # 超过此秒数未收到心跳视为离线（默认 5 分钟）
    PROJECT_ONLINE_THRESHOLD_SECONDS: int = Field(default=300, description="项目在线判定阈值（秒）")

    @property
    def WXPAY_APP_ID_EFFECTIVE(self) -> str:
        """前端调起确认页用的 appid：优先微信支付专用配置，回退服务号 appid。"""
        return self.WXPAY_APP_ID or self.WECHAT_APP_ID

    @property
    def WECHAT_CONFIG(self) -> Dict:
        return {
            'token': self.WECHAT_TOKEN,
            'app_id': self.WECHAT_APP_ID,
            'app_secret': self.WECHAT_APP_SECRET,
            'encoding_aes_key': self.WECHAT_ENCODING_AES_KEY,
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
    def WECHAT_USER_INFO_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/user/info"

    @property
    def WECHAT_USER_BATCH_INFO_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/user/info/batchget"

    @property
    def WECHAT_USER_SUMMARY_URL(self) -> str:
        # datacube 数据分析接口无 cgi-bin 前缀（区别于 cgi 类接口）
        return f"{self.WECHAT_API_BASE_URL}/datacube/getusersummary"
    
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
    
    @property
    def WECHAT_QRCODE_CREATE_URL(self) -> str:
        return f"{self.WECHAT_API_BASE_URL}/cgi-bin/qrcode/create"
    
    @model_validator(mode='after')
    def validate_secret_key(self) -> 'Settings':
        if not self.SECRET_KEY and self.JWT_SECRET:
            self.SECRET_KEY = self.JWT_SECRET
        if not self.LLM_API_KEY and self.AI_API_KEY:
            self.LLM_API_KEY = self.AI_API_KEY
        if not self.MQTT_USER and self.MQTT_USERNAME:
            self.MQTT_USER = self.MQTT_USERNAME
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