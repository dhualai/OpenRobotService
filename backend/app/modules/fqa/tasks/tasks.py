from app.modules.fqa.tasks.queue import celery_app
import time

@celery_app.task
def process_task(task_id: str, data: dict):
    print(f"开始处理任务: {task_id}")
    print(f"任务数据: {data}")
    
    time.sleep(5)
    
    result = {
        "task_id": task_id,
        "status": "completed",
        "data": data,
        "result": "任务处理成功"
    }
    
    print(f"任务处理完成: {task_id}")
    return result