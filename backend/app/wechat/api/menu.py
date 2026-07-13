from fastapi import APIRouter
from app.wechat.schemas.message import ApiResponse
from app.wechat.services.wechat_service import wechat_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["菜单管理"])


@router.post("/create_menu", response_model=ApiResponse)
async def api_create_menu():
    try:
        logger.info("尝试创建微信菜单")
        
        if wechat_service.create_wechat_menu():
            return ApiResponse(code=200, message="菜单创建成功")
        else:
            return ApiResponse(code=500, message="菜单创建失败")
    except Exception as e:
        logger.error(f'API创建菜单异常: {e}', exc_info=True)
        return ApiResponse(code=500, message="服务器内部错误")


@router.get("/get_menu", response_model=dict)
async def api_get_menu():
    try:
        logger.info("尝试获取微信菜单")
        
        menu_data = wechat_service.get_wechat_menu()
        if menu_data:
            return {"code": 200, "message": "获取成功", "data": menu_data}
        else:
            return {"code": 500, "message": "获取失败"}
    except Exception as e:
        logger.error(f'API获取菜单异常: {e}', exc_info=True)
        return {"code": 500, "message": "服务器内部错误"}


@router.delete("/delete_menu", response_model=ApiResponse)
async def api_delete_menu():
    try:
        logger.info("尝试删除微信菜单")
        
        if wechat_service.delete_wechat_menu():
            return ApiResponse(code=200, message="菜单删除成功")
        else:
            return ApiResponse(code=500, message="菜单删除失败")
    except Exception as e:
        logger.error(f'API删除菜单异常: {e}', exc_info=True)
        return ApiResponse(code=500, message="服务器内部错误")


@router.post("/create_conditional_menu", response_model=dict)
async def api_create_conditional_menu(menu_data: dict):
    try:
        logger.info("尝试创建个性化菜单")
        
        menuid = wechat_service.create_conditional_menu(menu_data)
        if menuid:
            return {"code": 200, "message": "个性化菜单创建成功", "data": {"menuid": menuid}}
        else:
            return {"code": 500, "message": "个性化菜单创建失败"}
    except Exception as e:
        logger.error(f'API创建个性化菜单异常: {e}', exc_info=True)
        return {"code": 500, "message": "服务器内部错误"}


@router.post("/create_conditional_menu_from_file", response_model=dict)
async def api_create_conditional_menu_from_file():
    try:
        logger.info("尝试从文件创建个性化菜单")
        
        menu_ids = wechat_service.create_conditional_menu_from_file()
        if menu_ids:
            return {"code": 200, "message": "个性化菜单创建成功", "data": {"menu_ids": menu_ids}}
        else:
            return {"code": 500, "message": "个性化菜单创建失败"}
    except Exception as e:
        logger.error(f'API从文件创建个性化菜单异常: {e}', exc_info=True)
        return {"code": 500, "message": "服务器内部错误"}


@router.delete("/delete_conditional_menu/{menuid}", response_model=ApiResponse)
async def api_delete_conditional_menu(menuid: str):
    try:
        logger.info(f"尝试删除个性化菜单: {menuid}")
        
        if wechat_service.delete_conditional_menu(menuid):
            return ApiResponse(code=200, message="个性化菜单删除成功")
        else:
            return ApiResponse(code=500, message="个性化菜单删除失败")
    except Exception as e:
        logger.error(f'API删除个性化菜单异常: {e}', exc_info=True)
        return ApiResponse(code=500, message="服务器内部错误")


@router.post("/try_match_menu", response_model=dict)
async def api_try_match_menu(user_id: str):
    try:
        logger.info(f"尝试测试个性化菜单，用户ID: {user_id}")
        
        menu_data = wechat_service.try_match_menu(user_id)
        if menu_data:
            return {"code": 200, "message": "测试成功", "data": menu_data}
        else:
            return {"code": 500, "message": "测试失败"}
    except Exception as e:
        logger.error(f'API测试个性化菜单异常: {e}', exc_info=True)
        return {"code": 500, "message": "服务器内部错误"}