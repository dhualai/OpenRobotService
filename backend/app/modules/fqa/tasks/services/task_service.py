from app.modules.fqa.tasks.tasks import process_task
import uuid

class TaskService:
    @staticmethod
    def create_task(data: dict) -> dict:
        task_id = str(uuid.uuid4())
        
        task = process_task.delay(task_id, data)
        
        return {
            "task_id": task_id,
            "task_celery_id": task.id,
            "status": "pending",
            "message": "任务已提交"
        }
    
    @staticmethod
    def get_task_status(task_id: str) -> dict:
        return {
            "task_id": task_id,
            "status": "completed",
            "message": "任务处理完成"
        }