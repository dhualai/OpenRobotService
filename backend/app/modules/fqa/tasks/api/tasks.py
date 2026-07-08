from fastapi import APIRouter
from app.modules.fqa.tasks.services.task_service import TaskService

router = APIRouter()

@router.post("/tasks")
async def create_task(data: dict):
    return TaskService.create_task(data)

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    return TaskService.get_task_status(task_id)