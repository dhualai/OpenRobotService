"""AI 模块 MinIO 客户端（独立于 backend，避免跨进程耦合）

backend 与 AI 是两个独立启动的服务，AI 进程不应 import backend 的
app.utils.minio_client（会间接拉起 app.core.config 等后端装配）。
本模块是 backend/app/utils/minio_client.py 的精简副本，配置改读
ai.config.get_ai_config()，保持接口一致，便于其他调用点平滑迁移。

用法：
    from ai.core.minio_client import minio_client
    minio_client.client.fget_object(bucket, object_name, local_path)
"""
from minio import Minio
from minio.error import S3Error
from datetime import timedelta
from typing import Optional
from io import BytesIO
import urllib3
from urllib3.util.retry import Retry
from urllib.parse import urlparse, urlunparse

from ai.config import get_ai_config
from ai.core.logging import get_logger

logger = get_logger("MINIO")

# 本地调试：经 nginx /minio-api/ 反向代理上传时，服务器 nginx 存在
#   location ~* \.(php|py|pl|sh|cgi|ini|conf|sql|bak|tar|gz|zip|log)$ { deny all; }
# 会使所有以 .zip/.gz/.tar/.log/.sql 等结尾的对象路径被 403 拦截（服务器不可改）。
# 仅当 MINIO_API_PREFIX 非空（即经 nginx 代理访问 MinIO）时，给被拦截后缀的 object
# key 追加安全后缀《.localproxy》绕开拦截；生产直连（prefix 为空）时不做任何改写。
_BLOCKED_SUFFIXES = (
    ".php", ".py", ".pl", ".sh", ".cgi", ".ini", ".conf",
    ".sql", ".bak", ".tar", ".gz", ".zip", ".log",
)
_LOCAL_SAFE_SUFFIX = ".localproxy"


def _via_proxy() -> bool:
    """是否经 nginx 反向代理访问 MinIO（MINIO_API_PREFIX 非空）。"""
    return bool(getattr(get_ai_config(), "minio_api_prefix", "") or "")


def _local_safe_key(object_path: str) -> str:
    """仅本地代理场景下，对被拦截后缀的 object key 追加安全后缀；否则原样返回。

    例如 xxx.zip → xxx.zip.localproxy（URL 不再以 .zip 结尾，绕开 nginx deny all）。
    上传/读取统一走此归一化，保证对象名一致、能正常读写。
    """
    if not _via_proxy():
        return object_path
    low = object_path.lower()
    if any(low.endswith(s) for s in _BLOCKED_SUFFIXES) and not low.endswith(_LOCAL_SAFE_SUFFIX):
        return object_path + _LOCAL_SAFE_SUFFIX
    return object_path


class AIMinIOClient:
    _instance: Optional["AIMinIOClient"] = None
    _client: Optional[Minio] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cfg = get_ai_config()

            class MinIOPoolManager(urllib3.PoolManager):
                """支持反向代理路径前缀（MINIO_API_PREFIX）。"""

                def urlopen(self, method, url, **kwargs):
                    if url.startswith(('http://', 'https://')):
                        parsed = urlparse(url)
                        path = parsed.path if parsed.path.startswith('/') else '/' + parsed.path
                        # getattr 兜底：旧版 config.py 无此字段时不崩，降级为直连（无前缀）
                        prefix = getattr(cfg, "minio_api_prefix", "") or ""
                        new_path = (prefix + path) if prefix else path
                        if new_path != parsed.path:
                            url = urlunparse(parsed._replace(path=new_path))
                    return super().urlopen(method, url, **kwargs)

            cls._instance._client = Minio(
                cfg.minio_endpoint,
                access_key=cfg.minio_access_key,
                secret_key=cfg.minio_secret_key,
                secure=cfg.minio_secure,
                http_client=MinIOPoolManager(
                    num_pools=10,
                    maxsize=10,
                    retries=Retry(
                        total=3,
                        backoff_factor=0.2,
                        status_forcelist=[500, 502, 503, 504]
                    ),
                    timeout=urllib3.Timeout(connect=10.0, read=60.0)
                ),
            )
        return cls._instance

    @property
    def client(self) -> Minio:
        """底层 Minio SDK 客户端（fget_object/put_object/stat_object 等）。"""
        return self._client

    # ── object_path 工具：bucket/key 形式 → (bucket, key) ──────────

    @staticmethod
    def _split(object_path: str) -> tuple[str, str]:
        bucket_name = object_path.split('/')[0]
        object_name = '/'.join(object_path.split('/')[1:])
        return bucket_name, object_name

    @staticmethod
    def _with_api_prefix(url: str) -> str:
        """给预签名 URL 补上对象存储网关前缀（如生产的 /minio-api）。

        预签名 URL 由 SDK 在本地签名生成，不经过上方 PoolManager 的路径重写，
        生产环境下浏览器直接访问会因缺 /minio-api 前缀被 nginx 404
        （历史会话图片裂图/文件下载失败）。此处显式补上。
        签名仍然有效：nginx `location /minio-api/ { proxy_pass http://minio_api/; }`
        会剥掉前缀，MinIO 看到的仍是签名时的 /{bucket}/{object} 路径。
        本地直连（minio_api_prefix 为空）时原样返回。
        """
        cfg = get_ai_config()
        prefix = getattr(cfg, "minio_api_prefix", "") or ""
        if not prefix:
            return url
        parsed = urlparse(url)
        path = parsed.path if parsed.path.startswith('/') else '/' + parsed.path
        return urlunparse(parsed._replace(path=prefix + path))

    def resolve_key(self, object_path: str) -> str:
        """对外暴露 object key 归一化（供 raw client 调用点统一使用）。

        仅本地代理场景会对被拦截后缀追加《.localproxy》；生产直连原样返回。
        """
        return _local_safe_key(object_path)

    def get_presigned_url(self, object_path: str, expires_minutes: int = 5) -> str:
        bucket_name, object_name = self._split(object_path)
        object_name = _local_safe_key(object_name)
        return self._with_api_prefix(self.client.presigned_get_object(
            bucket_name, object_name, expires=timedelta(minutes=expires_minutes)
        ))

    def fget_object(self, object_path: str, local_path: str) -> bool:
        """下载对象到本地文件路径（object_path = bucket/key）。"""
        try:
            bucket_name, object_name = self._split(object_path)
            object_name = _local_safe_key(object_name)
            self.client.fget_object(bucket_name, object_name, local_path)
            return True
        except S3Error as e:
            logger.error("下载文件失败: %s (object=%s)", e, object_path)
            return False

    def upload_bytes(self, file_bytes: bytes, object_path: str,
                     content_type: Optional[str] = None,
                     raise_on_error: bool = False) -> bool:
        try:
            bucket_name, object_name = self._split(object_path)
            object_name = _local_safe_key(object_name)
            file_obj = BytesIO(file_bytes)
            self.client.put_object(
                bucket_name, object_name, file_obj,
                len(file_bytes), content_type=content_type,
            )
            return True
        except S3Error as e:
            logger.error("上传字节失败: %s (object=%s)", e, object_path)
            if raise_on_error:
                raise
            return False

    def check_bucket_exists(self, bucket_name: Optional[str] = None) -> bool:
        try:
            cfg = get_ai_config()
            bucket = bucket_name or cfg.minio_bucket
            return self.client.bucket_exists(bucket)
        except S3Error as e:
            logger.warning("检查bucket失败: %s", e)
            return False

    def create_bucket(self, bucket_name: Optional[str] = None) -> bool:
        try:
            cfg = get_ai_config()
            bucket = bucket_name or cfg.minio_bucket
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
            return True
        except S3Error as e:
            logger.warning("创建bucket失败: %s", e)
            return False

    def get_file_info(self, object_path: str):
        try:
            bucket_name, object_name = self._split(object_path)
            object_name = _local_safe_key(object_name)
            return self.client.stat_object(bucket_name, object_name)
        except S3Error as e:
            logger.warning("获取文件信息失败: %s", e)
            return None


minio_client = AIMinIOClient()
