from minio import Minio
from minio.error import S3Error
from datetime import timedelta
from typing import Optional
from app.core.config import settings
from io import BytesIO
import urllib3
from urllib3.util.retry import Retry
from urllib.parse import urlparse, urlunparse


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

    def get_presigned_url(
        self,
        object_path: str,
        expires_minutes: int = 5
    ) -> str:
        bucket_name = object_path.split('/')[0]
        object_name = '/'.join(object_path.split('/')[1:])
        return self.client.presigned_get_object(
            bucket_name,
            object_name,
            expires=timedelta(minutes=expires_minutes)
        )

    def get_presigned_put_url(
        self,
        object_path: str,
        expires_minutes: int = 60
    ) -> str:
        bucket_name = object_path.split('/')[0]
        object_name = '/'.join(object_path.split('/')[1:])
        return self.client.presigned_put_object(
            bucket_name,
            object_name,
            expires=timedelta(minutes=expires_minutes)
        )

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
                object_name,
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
        content_type: Optional[str] = None
    ) -> bool:
        try:
            bucket_name = object_path.split('/')[0]
            object_name = '/'.join(object_path.split('/')[1:])
            file_obj = BytesIO(file_bytes)
            self.client.put_object(
                bucket_name,
                object_name,
                file_obj,
                len(file_bytes),
                content_type=content_type
            )
            return True
        except S3Error as e:
            print(f"上传字节失败: {e}")
            return False

    def delete_file(self, object_path: str) -> bool:
        try:
            bucket_name = object_path.split('/')[0]
            object_name = '/'.join(object_path.split('/')[1:])
            self.client.remove_object(bucket_name, object_name)
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
            return self.client.stat_object(bucket_name, object_name)
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