import json
import logging
from datetime import datetime

operation_logger = logging.getLogger('WX.operation')

def log_operation(
    timestamp=None,
    level="INFO",
    message="API请求处理完成",
    client_ip=None,
    method=None,
    path=None,
    status_code=None,
    processing_time=None,
    query_params=None,
    request_body=None,
    response_body=None,
    operator=None,
    summary=None,
    tag="Operation"
):
    if timestamp is None:
        timestamp = datetime.now().isoformat()
    
    log_data = {
        "timestamp": timestamp,
        "level": level,
        "message": message,
        "client_ip": client_ip,
        "method": method,
        "path": path,
        "status_code": status_code,
        "processing_time": round(processing_time, 2) if processing_time is not None else None,
        "operator": operator,
        "summary": summary,
        "tag": tag
    }
    
    if status_code != 200 and response_body is not None:
        log_data["response_body"] = response_body
    
    if query_params is not None:
        log_data["query_params"] = query_params
    
    if request_body is not None:
        log_data["request_body"] = request_body
    
    operation_logger.info(json.dumps(log_data, ensure_ascii=False))