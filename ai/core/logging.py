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


class ReadableFormatter(logging.Formatter):
    """让日志行首整洁，源码定位 (module.funcName:lineno) 移至行尾（仅 ASSIGNER / TASK_AGENT 使用）。

    原格式:  2026-08-13 20:10:18 - ASSIGNER - INFO - dispatch_flow.aassign:101 - [派单:457] Step0 ...
    新格式:  2026-08-13 20:10:18 - ASSIGNER - INFO - [派单:457] Step0 ...（ dispatch_flow.aassign:101 ）

    关键改进：
      1. 源码定位 (module.funcName:lineno) 从行首移到行尾 → 不打断行首的流程标识（[派单:xxx]）阅读。
      2. 保持紧凑的 ` - ` 分隔风格，避免固定宽度留白。
      3. 已由父类完整处理 exc_info / 多行堆栈，仅把定位信息追加到行尾。
    """

    def __init__(self, datefmt: str = "%Y-%m-%d %H:%M:%S"):
        # fmt 不含 module/funcName/lineno，定位信息由 format() 追加到行尾
        super().__init__(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt=datefmt,
        )

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        loc = f"（ {record.module}.{record.funcName}:{record.lineno}）"
        # 防止 record 复用导致的重复追加（同一条记录仅加成一次）
        if s.endswith(loc):
            return s
        return f"{s} {loc}"


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
            # AI 主日志 / root 保持原格式，不受本次可读性改造影响
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            # 仅供 ASSIGNER / TASK_AGENT：源码定位(module.funcName:lineno)移至行尾，行首固定宽度对齐
            "readable": {
                "()": ReadableFormatter,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": "DEBUG",
                "formatter": "standard",
                "stream": "ext://sys.stdout",
            },
            # 仅 ASSIGNER / TASK_AGENT 使用：使这两个模块的控制台日志同样整洁（不影响 AI 主日志）
            "module_console": {
                "class": "logging.StreamHandler",
                "level": "DEBUG",
                "formatter": "readable",
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
                "formatter": "readable",
                "filename": task_log_file,
                "when": "midnight",
                "interval": 1,
                "backupCount": 30,
                "encoding": "utf-8",
            },
            "assigner_file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "level": "DEBUG",
                "formatter": "readable",
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
                "level": "INFO",
                "handlers": ["module_console", "task_agent_file"],
                "propagate": False,
            },
            _ASSIGNER_LOGGER: {
                "level": "INFO",
                "handlers": ["module_console", "assigner_file"],
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
