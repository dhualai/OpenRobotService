from minio import Minio
from minio.error import S3Error
from datetime import timedelta
from typing import Optional
from app.core.config import settings
from io import BytesIO
import logging
import urllib3
from urllib3.util.retry import Retry
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# ── 本地经 nginx 代理访问 MinIO 时，被拦截后缀的绕过（与 ai/core/minio_client.py 一致）──
# 服务器 nginx 存在 `location ~* \.(php|py|pl|sh|cgi|ini|conf|sql|bak|tar|gz|zip|log)$ { deny all; }`
# 本地开发为远程连生产 MinIO 把 MINIO_API_PREFIX 设为 /minio-api（经 nginx /minio-api/ 代理），
# 对象名以 .zip/.log/.gz/.tar 等结尾时，multipart 初始化请求会被 nginx 403 拦截。
# 仅当 MINIO_API_PREFIX 非空（即本地/经 nginx 代理；生产直连 prefix 为空）时，给被拦截后缀的
# object key 追加《.localproxy》安全后缀，使 URL 不再以 .zip 等结尾而绕开拦截；生产直连不生效。
_BLOCKED_SUFFIXES = (
    ".php", ".py", ".pl", ".sh", ".cgi", ".ini", ".conf",
    ".sql", ".bak", ".tar", ".gz", ".zip", ".log",
)
_LOCAL_SAFE_SUFFIX = ".localproxy"


def _via_proxy() -> bool:
    """是否经 nginx 反向代理访问 MinIO（MINIO_API_PREFIX 非空；生产直连为空则 False）。"""
    return bool(getattr(settings, "MINIO_API_PREFIX", "") or "")


def _local_safe_key(object_name: str) -> str:
    """仅经 nginx 代理（生产直连 prefix 为空）时，对被拦截后缀的 object key 追加安全后缀；否则原样返回。

    例如 xxx.zip → xxx.zip.localproxy（URL 不再以 .zip 结尾，绕开 nginx deny all）。
    上传/读取统一走此归一化，保证对象名一致、能正常读写。
    """
    if not _via_proxy():
        return object_name
    low = object_name.lower()
    if any(low.endswith(s) for s in _BLOCKED_SUFFIXES) and not low.endswith(_LOCAL_SAFE_SUFFIX):
        return object_name + _LOCAL_SAFE_SUFFIX
    return object_name


class MinIOClient:
    _instance: Optional['MinIOClient'] = None
    _client: Optional[Minio] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            
            class MinIOPoolManager(urllib3.PoolManager):
                def urlopen(self, method, url, **kwargs):
                    if url.startswith(('http://', 'https://')):
                        parsed = urlparse(url)
                        path = parsed.path if parsed.path.startswith('/') else '/' + parsed.path
                        prefix = settings.MINIO_API_PREFIX or ''
                        new_path = (prefix + path) if prefix else path
                        if new_path != parsed.path:
                            url = urlunparse(parsed._replace(path=new_path))
                    return super().urlopen(method, url, **kwargs)
            
            cls._instance._client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE,
                http_client=MinIOPoolManager(
                    num_pools=10,
                    maxsize=10,
                    retries=Retry(
                        total=3,
                        backoff_factor=0.2,
                        status_forcelist=[500, 502, 503, 504]
                    ),
                    timeout=urllib3.Timeout(connect=10.0, read=60.0)
                )
            )
        return cls._instance

    @property
    def client(self) -> Minio:
        return self._client

    @staticmethod
    def _with_api_prefix(url: str) -> str:
        """给预签名 URL 补上对象存储网关前缀（如生产的 /minio-api）。

        预签名 URL 由 SDK 在本地签名生成，不经过上方 PoolManager 的路径重写，
        因此生产环境下返回给浏览器的 URL 会缺 /minio-api 前缀，
        直接访问被 nginx 404（图片裂图/文件下载失败）。此处显式补上。
        签名仍然有效：nginx `location /minio-api/ { proxy_pass http://minio_api/; }`
        会把前缀剥掉，MinIO 看到的仍是签名时的 /{bucket}/{object} 路径。
        本地直连（MINIO_API_PREFIX 为空）时原样返回。
        """
        prefix = settings.MINIO_API_PREFIX or ''
        if not prefix:
            return url
        parsed = urlparse(url)
        path = parsed.path if parsed.path.startswith('/') else '/' + parsed.path
        return urlunparse(parsed._replace(path=prefix + path))

    def get_presigned_url(
        self,
        object_path: str,
        expires_minutes: int = 5
    ) -> str:
        bucket_name = object_path.split('/')[0]
        object_name = '/'.join(object_path.split('/')[1:])
        return self._with_api_prefix(self.client.presigned_get_object(
            bucket_name,
            _local_safe_key(object_name),
            expires=timedelta(minutes=expires_minutes)
        ))

    def get_presigned_put_url(
        self,
        object_path: str,
        expires_minutes: int = 60
    ) -> str:
        bucket_name = object_path.split('/')[0]
        object_name = '/'.join(object_path.split('/')[1:])
        return self._with_api_prefix(self.client.presigned_put_object(
            bucket_name,
            _local_safe_key(object_name),
            expires=timedelta(minutes=expires_minutes)
        ))

    def upload_file(
        self,
        file_path: str,
        object_path: str,
        content_type: Optional[str] = None
    ) -> bool:
        try:
            bucket_name = object_path.split('/')[0]
            object_name = '/'.join(object_path.split('/')[1:])
            self.client.fput_object(
                bucket_name,
                _local_safe_key(object_name),
                file_path,
                content_type=content_type
            )
            return True
        except S3Error as e:
            print(f"上传文件失败: {e}")
            return False

    def upload_bytes(
        self,
        file_bytes: bytes,
        object_path: str,
        content_type: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> bool:
        try:
            bucket_name = object_path.split('/')[0]
            object_name = '/'.join(object_path.split('/')[1:])
            file_obj = BytesIO(file_bytes)
            self.client.put_object(
                bucket_name,
                _local_safe_key(object_name),
                file_obj,
                len(file_bytes),
                content_type=content_type
            )
            return True
        except S3Error as e:
            logger.error(
                "上传字节失败: %s (bucket=%s, object=%s)",
                e, object_path.split('/')[0], '/'.join(object_path.split('/')[1:]),
            )
            if raise_on_error:
                raise
            return False

    def delete_file(self, object_path: str) -> bool:
        try:
            bucket_name = object_path.split('/')[0]
            object_name = '/'.join(object_path.split('/')[1:])
            self.client.remove_object(bucket_name, _local_safe_key(object_name))
            return True
        except S3Error as e:
            print(f"删除文件失败: {e}")
            return False

    def check_bucket_exists(self, bucket_name: Optional[str] = None) -> bool:
        try:
            bucket = bucket_name or settings.MINIO_BUCKET
            return self.client.bucket_exists(bucket)
        except S3Error as e:
            print(f"检查bucket失败: {e}")
            return False

    def create_bucket(self, bucket_name: Optional[str] = None) -> bool:
        try:
            bucket = bucket_name or settings.MINIO_BUCKET
            if not self.check_bucket_exists(bucket):
                self.client.make_bucket(bucket)
            return True
        except S3Error as e:
            print(f"创建bucket失败: {e}")
            return False

    def get_file_info(self, object_path: str):
        try:
            bucket_name = object_path.split('/')[0]
            object_name = '/'.join(object_path.split('/')[1:])
            return self.client.stat_object(bucket_name, _local_safe_key(object_name))
        except S3Error as e:
            print(f"获取文件信息失败: {e}")
            return None


minio_client = MinIOClient()


def ensure_minio_buckets(buckets: Optional[list] = None) -> None:
    """应用启动时确保所需 bucket 存在。

    若 MinIO 未启动或不可达，仅打印警告而不抛出异常，避免阻塞应用启动。
    """
    if buckets is None:
        buckets = [settings.MINIO_BUCKET, settings.COMMENT_BUCKET, settings.FILE_IMAGES]
    for name in buckets:
        try:
            minio_client.create_bucket(name)
        except Exception as exc:  # noqa: BLE001 - 启动期容忍对象存储暂时不可用
            print(f"[MinIO] 确保 bucket 失败（若 MinIO 尚未启动可忽略）: {name} -> {exc}")