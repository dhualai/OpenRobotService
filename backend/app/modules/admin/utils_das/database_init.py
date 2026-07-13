from sqlalchemy import create_engine
from app.modules.admin.models.models import Base
from app.modules.admin.utils_das.config import DATABASE_URL
import logging

logger = logging.getLogger("DAS")

engine = create_engine(DATABASE_URL)

def init_das_db():
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("DAS数据库表初始化完成")
        return True
    except Exception as e:
        logger.error(f"DAS数据库表初始化失败: {str(e)}")
        return False

if __name__ == "__main__":
    init_das_db()