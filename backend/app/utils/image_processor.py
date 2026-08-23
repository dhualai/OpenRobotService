import re
import base64
from typing import List, Dict, Tuple
from io import BytesIO
from app.utils.minio_client import minio_client
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class ImageProcessor:
    BASE64_IMAGE_PATTERN = re.compile(
        r'<img[^>]+src=["\']data:image/(\w+);base64,([^"\']+)["\']',
        re.IGNORECASE
    )

    @classmethod
    def get_minio_url_pattern(cls):
        endpoint = settings.MINIO_ENDPOINT
        escaped = re.escape(endpoint)
        escaped = escaped.replace('localhost', '(?:localhost|127\\.0\\.0\\.1)')
        return re.compile(
            rf'<img[^>]+src=["\']([^"\']*https?://{escaped}[^"\']*)["\']',
            re.IGNORECASE
        )

    @classmethod
    def extract_base64_images(cls, content: str) -> List[Dict[str, str]]:
        images = []
        matches = cls.BASE64_IMAGE_PATTERN.findall(content)

        for match in matches:
            image_type, base64_data = match
            original_src = f"data:image/{image_type};base64,{base64_data}"

            images.append({
                "original_src": original_src,
                "image_type": image_type,
                "base64_data": base64_data
            })

        return images

    @classmethod
    def decode_base64_to_bytes(cls, base64_data: str) -> bytes:
        return base64.b64decode(base64_data)

    @classmethod
    def upload_to_minio(cls, image_bytes: bytes, image_type: str, ticket_id: int, comment_id: int, image_index: int) -> str:
        object_path = f"{settings.COMMENT_BUCKET}/ticket-comments/{ticket_id}/{comment_id}_{image_index}.{image_type}"

        content_type = f"image/{image_type}"

        success = minio_client.upload_bytes(image_bytes, object_path, content_type)

        if not success:
            raise Exception(f"上传图片到MinIO失败: {object_path}")

        return object_path

    @classmethod
    def get_minio_url(cls, object_path: str) -> str:
        return minio_client.get_presigned_url(object_path, expires_minutes=1440)

    @classmethod
    def process_content_for_storage(cls, content: str, ticket_id: int, comment_id: int) -> Tuple[str, List[str]]:
        if not content:
            return content, []

        images = cls.extract_base64_images(content)

        if not images:
            return content, []

        processed_content = content
        object_paths = []

        for index, image_info in enumerate(images):
            try:
                image_bytes = cls.decode_base64_to_bytes(image_info["base64_data"])

                object_path = cls.upload_to_minio(
                    image_bytes,
                    image_info["image_type"],
                    ticket_id,
                    comment_id,
                    index
                )

                minio_url = cls.get_minio_url(object_path)

                processed_content = processed_content.replace(
                    image_info["original_src"],
                    minio_url
                )

                object_paths.append(object_path)

                logger.info(f"成功上传图片到MinIO: {object_path}")

            except Exception as e:
                logger.error(f"处理图片失败: {str(e)}")

        return processed_content, object_paths

    @classmethod
    def process_content_for_response(cls, content: str) -> str:
        if not content:
            return content

        try:
            pattern = cls.get_minio_url_pattern()
            logger.info(f"MinIO URL模式: {pattern.pattern}")
            
            matches = pattern.findall(content)
            logger.info(f"匹配到MinIO URL数量: {len(matches)}")

            if not matches:
                return content

            processed_content = content

            for minio_url in matches:
                try:
                    logger.info(f"处理MinIO URL: {minio_url[:100]}..." if len(minio_url) > 100 else f"处理MinIO URL: {minio_url}")
                    
                    object_path = cls.extract_object_path_from_url(minio_url)
                    logger.info(f"提取对象路径: {object_path}")

                    if not object_path:
                        continue

                    image_bytes = cls.download_from_minio(object_path)
                    logger.info(f"下载图片字节数: {len(image_bytes) if image_bytes else 0}")

                    if not image_bytes:
                        continue

                    image_type = cls.get_image_type_from_path(object_path)
                    base64_data = base64.b64encode(image_bytes).decode('utf-8')
                    base64_src = f"data:image/{image_type};base64,{base64_data}"

                    processed_content = processed_content.replace(minio_url, base64_src)

                    logger.info(f"成功转换MinIO URL为base64: {object_path}")

                except Exception as e:
                    logger.error(f"转换MinIO URL失败: url={minio_url[:50]}..., error={str(e)}", exc_info=True)

            return processed_content
        except Exception as e:
            logger.error(f"process_content_for_response整体失败: content_length={len(content)}, error={str(e)}", exc_info=True)
            raise

    @classmethod
    def extract_object_path_from_url(cls, url: str) -> str:
        try:
            url_without_query = url.split('?')[0]
            parts = url_without_query.split('/')

            bucket_index = -1
            for i, part in enumerate(parts):
                if part == settings.COMMENT_BUCKET:
                    bucket_index = i
                    break

            if bucket_index >= 0 and bucket_index + 1 < len(parts):
                object_path = '/'.join(parts[bucket_index:])
                return object_path

        except Exception as e:
            logger.error(f"提取对象路径失败: {str(e)}")

        return ""

    @classmethod
    def download_from_minio(cls, object_path: str) -> bytes:
        try:
            bucket_name = object_path.split('/')[0]
            object_name = '/'.join(object_path.split('/')[1:])

            response = minio_client.client.get_object(bucket_name, object_name)
            return response.read()

        except Exception as e:
            logger.error(f"从MinIO下载图片失败: {str(e)}")
            return b""

    @classmethod
    def get_image_type_from_path(cls, object_path: str) -> str:
        try:
            if '.' in object_path:
                return object_path.rsplit('.', 1)[1].lower()
        except Exception as e:
            logger.error(f"提取图片类型失败: {str(e)}")

        return "png"