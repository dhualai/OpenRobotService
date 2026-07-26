import uuid
import time

async def generate_request_id() -> str:
    return str(uuid.uuid4())

async def get_current_timestamp() -> int:
    return int(time.time() * 1000)