from fastapi import APIRouter, Query, Body
from fastapi.responses import JSONResponse
from typing import List
from app.wechat.services.wechat_service import wechat_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tag", tags=["标签管理接口"])


@router.get("", summary="获取所有标签")
def get_tags():
    logger.info("尝试获取所有标签")
    result = wechat_service.get_tags()
    if result:
        logger.info(f"成功获取标签，数量: {len(result) if isinstance(result, list) else 1}")
        return JSONResponse(status_code=200, content={
            "code": 0,
            "message": "获取标签成功",
            "data": result
        })
    else:
        logger.error("获取标签失败")
        return JSONResponse(status_code=500, content={
            "code": 500,
            "message": "获取标签失败"
        })


@router.post("", summary="创建标签")
def create_tag(name: str = Body(..., description="标签名称", embed=True)):
    logger.info(f"尝试创建标签，名称: {name}")
    result = wechat_service.create_tag(name)
    if result:
        logger.info(f"成功创建标签: {result}")
        return JSONResponse(status_code=200, content={
            "code": 0,
            "message": "创建标签成功",
            "data": result
        })
    else:
        logger.error(f"创建标签失败，名称: {name}")
        return JSONResponse(status_code=500, content={
            "code": 500,
            "message": "创建标签失败"
        })


@router.put("/{tag_id}", summary="更新标签")
def update_tag(tag_id: int, name: str = Body(..., description="新标签名称", embed=True)):
    logger.info(f"尝试更新标签，ID: {tag_id}, 新名称: {name}")
    result = wechat_service.update_tag(tag_id, name)
    if result:
        logger.info(f"成功更新标签，ID: {tag_id}")
        return JSONResponse(status_code=200, content={
            "code": 0,
            "message": "更新标签成功"
        })
    else:
        logger.error(f"更新标签失败，ID: {tag_id}")
        return JSONResponse(status_code=500, content={
            "code": 500,
            "message": "更新标签失败"
        })


@router.delete("/{tag_id}", summary="删除标签")
def delete_tag(tag_id: int):
    logger.info(f"尝试删除标签，ID: {tag_id}")
    result = wechat_service.delete_tag(tag_id)
    if result:
        logger.info(f"成功删除标签，ID: {tag_id}")
        return JSONResponse(status_code=200, content={
            "code": 0,
            "message": "删除标签成功"
        })
    else:
        logger.error(f"删除标签失败，ID: {tag_id}")
        return JSONResponse(status_code=500, content={
            "code": 500,
            "message": "删除标签失败"
        })


@router.post("/batch-tagging", summary="批量为用户打标签")
def batch_tagging(openid_list: List[str] = Body(..., description="用户openid列表"), 
                  tag_id: int = Body(..., description="标签ID")):
    if len(openid_list) > 100:
        logger.warning(f"openid列表超过限制，数量: {len(openid_list)}")
        return JSONResponse(status_code=400, content={
            "code": 400,
            "message": "openid列表最多支持100个用户"
        })
    
    logger.info(f"尝试批量为用户打标签，用户数量: {len(openid_list)}, 标签ID: {tag_id}")
    result = wechat_service.batch_tagging(openid_list, tag_id)
    if result:
        logger.info(f"成功批量为用户打标签，用户数量: {len(openid_list)}, 标签ID: {tag_id}")
        return JSONResponse(status_code=200, content={
            "code": 0,
            "message": "批量打标签成功"
        })
    else:
        logger.error(f"批量打标签失败，用户数量: {len(openid_list)}, 标签ID: {tag_id}")
        return JSONResponse(status_code=500, content={
            "code": 500,
            "message": "批量打标签失败"
        })


@router.post("/batch-untagging", summary="批量为用户取消标签")
def batch_untagging(openid_list: List[str] = Body(..., description="用户openid列表"), 
                    tag_id: int = Body(..., description="标签ID")):
    if len(openid_list) > 100:
        logger.warning(f"openid列表超过限制，数量: {len(openid_list)}")
        return JSONResponse(status_code=400, content={
            "code": 400,
            "message": "openid列表最多支持100个用户"
        })
    
    logger.info(f"尝试批量为用户取消标签，用户数量: {len(openid_list)}, 标签ID: {tag_id}")
    result = wechat_service.batch_untagging(openid_list, tag_id)
    if result:
        logger.info(f"成功批量为用户取消标签，用户数量: {len(openid_list)}, 标签ID: {tag_id}")
        return JSONResponse(status_code=200, content={
            "code": 0,
            "message": "批量取消标签成功"
        })
    else:
        logger.error(f"批量取消标签失败，用户数量: {len(openid_list)}, 标签ID: {tag_id}")
        return JSONResponse(status_code=500, content={
            "code": 500,
            "message": "批量取消标签失败"
        })


@router.get("/{tag_id}/fans", summary="获取标签下的粉丝列表")
def get_tag_fans(tag_id: int, next_openid: str = Query("", description="下一个openid，用于分页")):
    logger.info(f"尝试获取标签下的粉丝列表，标签ID: {tag_id}, next_openid: {next_openid}")
    result = wechat_service.get_tag_fans(tag_id, next_openid)
    if result:
        logger.info(f"成功获取标签下的粉丝列表，标签ID: {tag_id}")
        return JSONResponse(status_code=200, content={
            "code": 0,
            "message": "获取标签下粉丝列表成功",
            "data": result
        })
    else:
        logger.error(f"获取标签下粉丝列表失败，标签ID: {tag_id}")
        return JSONResponse(status_code=500, content={
            "code": 500,
            "message": "获取标签下粉丝列表失败"
        })


@router.get("/user/{openid}", summary="获取用户的标签列表")
def get_tag_id_list(openid: str):
    logger.info(f"尝试获取用户的标签列表，openid: {openid}")
    result = wechat_service.get_tag_id_list(openid)
    if result:
        logger.info(f"成功获取用户的标签列表，openid: {openid}")
        return JSONResponse(status_code=200, content={
            "code": 0,
            "message": "获取用户标签列表成功",
            "data": result
        })
    else:
        logger.error(f"获取用户标签列表失败，openid: {openid}")
        return JSONResponse(status_code=500, content={
            "code": 500,
            "message": "获取用户标签列表失败"
        })