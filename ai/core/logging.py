"""
AI 模块日志系统

日志输出：
  - 控制台：DEBUG 级别（开发/调试用）
  - 文件：INFO 级别，按天轮转，保留 30 天
  - 文件路径：ai/logs/ai.log

使用方式：
    from ai.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("正常流程")
    logger.error("出错了", exc_info=True)   # 附带完整 traceback

注意：所有 except 块必须用 logger.error/warning + exc_info=True，
确保出问题时日志里有完整的堆栈信息，便于溯源。
"""
import logging
import logging.config
import os
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

# 日志根目录：ai/logs/
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOGGER_NAME = "AI"
_TASK_AGENT_LOGGER = "TASK_AGENT"
_ASSIGNER_LOGGER = "ASSIGNER"

# logger → handler 映射
_MODULE_HANDLERS = {
    _TASK_AGENT_LOGGER: "task_agent_file",
    _ASSIGNER_LOGGER: "assigner_file",
}


def _default_config() -> dict:
    log_file = str(_LOG_DIR / "ai.log")
    task_log_file = str(_LOG_DIR / "task_agent" / "task_agent.log")
    assigner_log_file = str(_LOG_DIR / "assigner" / "assigner.log")
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": "DEBUG",
                "formatter": "standard",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "level": "INFO",
                "formatter": "standard",
                "filename": log_file,
                "when": "midnight",
                "interval": 1,
                "backupCount": 30,
                "encoding": "utf-8",
            },
            "task_agent_file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "level": "DEBUG",
                "formatter": "standard",
                "filename": task_log_file,
                "when": "midnight",
                "interval": 1,
                "backupCount": 30,
                "encoding": "utf-8",
            },
            "assigner_file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "level": "DEBUG",
                "formatter": "standard",
                "filename": assigner_log_file,
                "when": "midnight",
                "interval": 1,
                "backupCount": 30,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            _LOGGER_NAME: {
                "level": "DEBUG",
                "handlers": ["console", "file"],
                "propagate": False,
            },
            _TASK_AGENT_LOGGER: {
                "level": "DEBUG",
                "handlers": ["console", "task_agent_file"],
                "propagate": False,
            },
            _ASSIGNER_LOGGER: {
                "level": "DEBUG",
                "handlers": ["console", "assigner_file"],
                "propagate": False,
            },
        },
        "root": {
            "level": "INFO",
            "handlers": ["console", "file"],
        },
    }


def setup_logging() -> logging.Logger:
    """初始化 AI 模块日志（在 run.py lifespan 中调用）"""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    (_LOG_DIR / "task_agent").mkdir(parents=True, exist_ok=True)
    (_LOG_DIR / "assigner").mkdir(parents=True, exist_ok=True)

    config = _default_config()
    logging.config.dictConfig(config)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.info("AI 日志系统初始化完成")
    logger.info(f"日志文件: {_LOG_DIR / 'ai.log'}")
    logger.info(f"任务Agent日志: {_LOG_DIR / 'task_agent' / 'task_agent.log'}")
    logger.info(f"派单日志: {_LOG_DIR / 'assigner' / 'assigner.log'}")
    return logger


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """获取 logger。'AI'→ai.log，'TASK_AGENT'→task_agent/task_agent.log，'ASSIGNER'→assigner/assigner.log。"""
    return logging.getLogger(name)
