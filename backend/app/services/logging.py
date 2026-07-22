import logging
import logging.config
import json
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, Any

LOG_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logging_config.json')

DEFAULT_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "standard",
            "stream": "ext://sys.stdout"
        },
        "file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "level": "INFO",
            "formatter": "standard",
            "filename": os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs', 'backend.log'),
            "when": "midnight",
            "interval": 1,
            "backupCount": 7,
            "encoding": "utf-8"
        }
    },
    "loggers": {
        "DAS": {
            "level": "INFO",
            "handlers": ["console", "file"],
            "propagate": False
        },
        "uvicorn": {
            "level": "INFO",
            "handlers": ["console", "file"],
            "propagate": False
        },
        "uvicorn.error": {
            "level": "INFO",
            "handlers": ["console", "file"],
            "propagate": False
        },
        "uvicorn.access": {
            "level": "INFO",
            "handlers": ["console", "file"],
            "propagate": False
        }
    },
    "root": {
        "level": "INFO",
        "handlers": ["console", "file"]
    }
}

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage()
        }
        
        if hasattr(record, 'client_ip'):
            log_record['client_ip'] = record.client_ip
        if hasattr(record, 'path'):
            log_record['path'] = record.path
        if hasattr(record, 'method'):
            log_record['method'] = record.method
        if hasattr(record, 'status_code'):
            log_record['status_code'] = record.status_code
        if hasattr(record, 'request_body'):
            log_record['request_body'] = record.request_body
        if hasattr(record, 'response_body'):
            log_record['response_body'] = record.response_body
        if hasattr(record, 'processing_time'):
            log_record['processing_time'] = record.processing_time
            
        return json.dumps(log_record, ensure_ascii=False)

def setup_logging():
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    if os.path.exists(LOG_CONFIG_PATH):
        with open(LOG_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
    else:
        config = DEFAULT_LOG_CONFIG
        with open(LOG_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    
    logging.config.dictConfig(config)
    
    logger = logging.getLogger("DAS")
    logger.info("日志系统初始化完成")
    return logger

def get_logger(name: str = "DAS") -> logging.Logger:
    return logging.getLogger(name)