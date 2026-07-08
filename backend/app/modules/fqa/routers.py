from fastapi import APIRouter
from app.modules.fqa.qa.api.qa import router as qa_router
from app.modules.fqa.qa.api.conversation import router as conversation_router
from app.modules.fqa.qa.api.message import router as message_router
from app.modules.fqa.resource_manager.api.resource import router as resource_router
from app.modules.fqa.resource_manager.api.resource_folder import router as resource_folder_router
from app.modules.fqa.resource_manager.api.minio import router as minio_router
from app.modules.fqa.ticket.api.ticket import router as ticket_router
from app.modules.fqa.user.api.user import router as user_router
from app.modules.fqa.tasks.api.tasks import router as tasks_router

fqa_router = APIRouter(prefix="/fqa", tags=["fqa"])

fqa_router.include_router(qa_router)
fqa_router.include_router(conversation_router)
fqa_router.include_router(message_router)
fqa_router.include_router(resource_router)
fqa_router.include_router(resource_folder_router)
fqa_router.include_router(minio_router)
fqa_router.include_router(ticket_router)
fqa_router.include_router(user_router)
fqa_router.include_router(tasks_router)