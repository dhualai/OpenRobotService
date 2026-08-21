import os
from typing import Optional
from datetime import timedelta
from io import BytesIO

import alibabacloud_oss_v2 as oss
from app.core.config import settings


class OSSClient:
    """阿里云OSS客户端类，单例模式。

    配置从 app.core.config.settings 读取（环境变量 / .env），
    对应字段：ALIYUN_OSS_ACCESS_KEY_ID / SECRET / ENDPOINT / REGION / BUCKET / UPLOAD_DIR / PART_SIZE_MB
    """

    _instance: Optional["OSSClient"] = None
    _client: Optional[oss.Client] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_initialized(self):
        if self._client is None:
            self._client = self._create_client()

    def _create_client(self) -> oss.Client:
        credentials_provider = oss.credentials.StaticCredentialsProvider(
            settings.ALIYUN_OSS_ACCESS_KEY_ID,
            settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
        )
        sdk_cfg = oss.config.load_default()
        sdk_cfg.credentials_provider = credentials_provider
        sdk_cfg.region = settings.ALIYUN_OSS_REGION
        sdk_cfg.endpoint = settings.ALIYUN_OSS_ENDPOINT
        return oss.Client(sdk_cfg)

    @property
    def client(self) -> oss.Client:
        self._ensure_initialized()
        return self._client

    @property
    def config(self) -> dict:
        """返回 dict 形式配置（保持与 helpdesk-backend INI 方案的接口兼容，
        上游 `from oss_client.config['bucket_name']` 等调用仍可用）。"""
        return {
            "access_key_id": settings.ALIYUN_OSS_ACCESS_KEY_ID,
            "access_key_secret": settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
            "endpoint": settings.ALIYUN_OSS_ENDPOINT,
            "region": settings.ALIYUN_OSS_REGION,
            "bucket_name": settings.ALIYUN_OSS_BUCKET,
            "upload_dir": settings.ALIYUN_OSS_UPLOAD_DIR,
            "part_size_mb": settings.ALIYUN_OSS_PART_SIZE_MB,
        }

    def get_presigned_url(self, object_path: str, expires_minutes: int = 5) -> str:
        """object_path 格式: bucket_name/object_name"""
        self._ensure_initialized()
        bucket_name = object_path.split("/")[0]
        object_name = "/".join(object_path.split("/")[1:])
        result = self._client.presign(
            oss.GetObjectRequest(bucket=bucket_name, key=object_name),
            expires=timedelta(minutes=expires_minutes),
        )
        return result.url

    def upload_file(
        self,
        file_path: str,
        object_path: str,
        content_type: Optional[str] = None,
    ) -> bool:
        """上传本地文件；object_path 格式 bucket_name/object_name。"""
        try:
            self._ensure_initialized()
            bucket_name = object_path.split("/")[0]
            object_name = "/".join(object_path.split("/")[1:])
            self._upload_file_multipart(bucket_name, object_name, file_path, content_type)
            return True
        except Exception as e:
            print(f"上传文件到OSS失败: {e}")
            return False

    def upload_bytes(
        self,
        file_bytes: bytes,
        object_path: str,
        content_type: Optional[str] = None,
    ) -> bool:
        """上传字节数据；object_path 格式 bucket_name/object_name。"""
        try:
            self._ensure_initialized()
            bucket_name = object_path.split("/")[0]
            object_name = "/".join(object_path.split("/")[1:])

            file_obj = BytesIO(file_bytes)
            file_size = len(file_bytes)

            if file_size > 100 * 1024 * 1024:
                part_size = settings.ALIYUN_OSS_PART_SIZE_MB * 1024 * 1024

                init_result = self._client.initiate_multipart_upload(
                    oss.InitiateMultipartUploadRequest(
                        bucket=bucket_name,
                        key=object_name,
                        content_type=content_type,
                    )
                )
                upload_id = init_result.upload_id

                upload_parts = []
                part_number = 1
                for start in range(0, file_size, part_size):
                    n = min(part_size, file_size - start)
                    part_obj = BytesIO(file_bytes[start : start + n])
                    result = self._client.upload_part(
                        oss.UploadPartRequest(
                            bucket=bucket_name,
                            key=object_name,
                            upload_id=upload_id,
                            part_number=part_number,
                            body=part_obj,
                        )
                    )
                    upload_parts.append(oss.UploadPart(part_number=part_number, etag=result.etag))
                    part_number += 1
                    progress = (start + n) / file_size * 100
                    print(f"\r[OSS上传进度] {progress:.1f}%  (part {part_number - 1})", end="", flush=True)
                print()

                parts = sorted(upload_parts, key=lambda p: p.part_number)
                self._client.complete_multipart_upload(
                    oss.CompleteMultipartUploadRequest(
                        bucket=bucket_name,
                        key=object_name,
                        upload_id=upload_id,
                        complete_multipart_upload=oss.CompleteMultipartUpload(parts=parts),
                    )
                )
                print(f"[OSS上传完成] {object_name}")
            else:
                self._client.put_object(
                    oss.PutObjectRequest(
                        bucket=bucket_name,
                        key=object_name,
                        body=file_obj,
                        content_type=content_type,
                    )
                )
            return True
        except Exception as e:
            print(f"上传字节数据到OSS失败: {e}")
            return False

    def _upload_file_multipart(
        self,
        bucket_name: str,
        object_name: str,
        local_path: str,
        content_type: Optional[str] = None,
    ):
        file_size = os.path.getsize(local_path)
        part_size = settings.ALIYUN_OSS_PART_SIZE_MB * 1024 * 1024

        init_result = self._client.initiate_multipart_upload(
            oss.InitiateMultipartUploadRequest(
                bucket=bucket_name,
                key=object_name,
                content_type=content_type,
            )
        )
        upload_id = init_result.upload_id

        upload_parts = []
        part_number = 1
        with open(local_path, "rb") as f:
            for start in range(0, file_size, part_size):
                n = min(part_size, file_size - start)
                reader = oss.io_utils.SectionReader(
                    oss.io_utils.ReadAtReader(f), start, n
                )
                result = self._client.upload_part(
                    oss.UploadPartRequest(
                        bucket=bucket_name,
                        key=object_name,
                        upload_id=upload_id,
                        part_number=part_number,
                        body=reader,
                    )
                )
                upload_parts.append(oss.UploadPart(part_number=part_number, etag=result.etag))
                part_number += 1
                progress = (start + n) / file_size * 100
                print(f"\r[OSS上传进度] {progress:.1f}%  (part {part_number - 1})", end="", flush=True)
        print()

        parts = sorted(upload_parts, key=lambda p: p.part_number)
        self._client.complete_multipart_upload(
            oss.CompleteMultipartUploadRequest(
                bucket=bucket_name,
                key=object_name,
                upload_id=upload_id,
                complete_multipart_upload=oss.CompleteMultipartUpload(parts=parts),
            )
        )
        print(f"[OSS上传完成] {object_name}")

    def delete_file(self, object_path: str) -> bool:
        try:
            self._ensure_initialized()
            bucket_name = object_path.split("/")[0]
            object_name = "/".join(object_path.split("/")[1:])
            self._client.delete_object(
                oss.DeleteObjectRequest(bucket=bucket_name, key=object_name)
            )
            return True
        except Exception as e:
            print(f"删除OSS文件失败: {e}")
            return False

    def get_file_info(self, object_path: str):
        """HeadObject，返回含 content_length / content_type / last_modified 等的对象。"""
        try:
            self._ensure_initialized()
            bucket_name = object_path.split("/")[0]
            object_name = "/".join(object_path.split("/")[1:])
            result = self._client.head_object(
                oss.HeadObjectRequest(bucket=bucket_name, key=object_name)
            )
            return result
        except Exception as e:
            print(f"获取OSS文件信息失败: {e}")
            return None

    def list_files(self, prefix: str = "") -> list:
        """列出配置桶内的文件。返回 [{key, size, last_modified, content_type}, ...]。"""
        self._ensure_initialized()
        bucket_name = settings.ALIYUN_OSS_BUCKET
        upload_dir = (settings.ALIYUN_OSS_UPLOAD_DIR or "").rstrip("/")

        if upload_dir:
            full_prefix = f"{upload_dir}/{prefix}" if prefix else upload_dir
        else:
            full_prefix = prefix

        if full_prefix and not full_prefix.endswith("/"):
            full_prefix += "/"

        files = []
        paginator = self._client.list_objects_v2_paginator()
        for page in paginator.iter_page(
            oss.ListObjectsV2Request(bucket=bucket_name, prefix=full_prefix)
        ):
            if page.contents:
                for obj in page.contents:
                    if obj.key.endswith("/"):
                        continue
                    files.append(
                        {
                            "key": obj.key,
                            "size": obj.size,
                            "last_modified": obj.last_modified,
                            "content_type": getattr(obj, "content_type", None),
                        }
                    )
        return files


oss_client = OSSClient()
