from fastapi import APIRouter, HTTPException, Query
from app.modules.fqa.utils.minio_client import minio_client
from app.core.config import settings

router = APIRouter(prefix="/minio", tags=["minio"])


@router.get("/presigned-url")
async def get_presigned_url(
    bucket_name: str = Query(..., description="MinIO bucket名称"),
    object_name: str = Query(..., description="对象名称"),
    expires_minutes: int = Query(5, ge=1, le=10080, description="URL有效期（分钟），默认5分钟，最大10080分钟（7天）")
):
    try:
        object_path = f"{bucket_name}/{object_name}"
        
        presigned_url = minio_client.get_presigned_url(
            object_path=object_path,
            expires_minutes=expires_minutes
        )
        for bucket_name in [settings.MINIO_BUCKET, settings.COMMENT_BUCKET, settings.FILE_IMAGES]:
            if bucket_name in presigned_url:
                presigned_url = presigned_url.replace(bucket_name, f'minio-api/{bucket_name}')

        return {
            "presigned_url": presigned_url,
            "bucket_name": bucket_name,
            "object_name": object_name,
            "expires_in_minutes": expires_minutes
        }
    except Exception as e:
        import traceback
        error_detail = f"获取预签名URL失败: {str(e)}\n{traceback.format_exc()}"
        print(error_detail)
        raise HTTPException(status_code=500, detail=error_detail)