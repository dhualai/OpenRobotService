from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Type, TypeVar, Generic
from sqlalchemy.orm import DeclarativeBase

ModelType = TypeVar('ModelType', bound=DeclarativeBase)


class DatabaseUtils:
    """数据库工具类，提供通用的数据库操作方法"""

    @staticmethod
    async def get_by_id(db: AsyncSession, model: Type[ModelType], obj_id: int) -> Optional[ModelType]:
        """根据ID获取单个对象的通用方法"""
        return await db.get(model, obj_id)

    @staticmethod
    async def commit_and_refresh(db: AsyncSession, obj: ModelType) -> ModelType:
        """提交并刷新对象的通用方法"""
        await db.commit()
        await db.refresh(obj)
        return obj

    @staticmethod
    async def create_and_commit(db: AsyncSession, model: Type[ModelType], **kwargs) -> ModelType:
        """创建并提交对象的通用方法"""
        obj = model(**kwargs)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj