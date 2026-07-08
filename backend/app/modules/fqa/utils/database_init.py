from sqlalchemy import create_engine
from app.core.config import settings
from app.core.database import Base
import logging

logger = logging.getLogger("FQA")

engine = create_engine(settings.DATABASE_URL)

def init_fqa_db():
    try:
        from app.modules.fqa.qa.models.conversation import Conversation
        from app.modules.fqa.qa.models.message import Message
        from app.modules.fqa.resource_manager.models.resource import Resource
        from app.modules.fqa.resource_manager.models.resource_folder import ResourceFolder
        from app.modules.fqa.ticket.models.ticket import Ticket, TicketComment
        
        Base.metadata.create_all(bind=engine)
        
        logger.info("FQA数据库表初始化完成")
        return True
    except Exception as e:
        logger.error(f"FQA数据库表初始化失败: {str(e)}")
        return False

if __name__ == "__main__":
    init_fqa_db()