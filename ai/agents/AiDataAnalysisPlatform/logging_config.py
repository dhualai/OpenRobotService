"""AI 数据分析平台 · 模块化日志系统

每个子模块拥有独立的日志文件（INFO / ERROR 分离），
同时共享彩色控制台输出。

日志目录：ai/logs/DataAnalysisLogs/
单文件上限：80 MB，保留 8 个历史备份。

使用方式::

    from .logging_config import get_logger
    logger = get_logger("Router")
    logger.info("请求处理完成")
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict

# ── 常量配置 ────────────────────────────────────────────────
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "DataAnalysisLogs"
_MAX_BYTES_MB = 80          # 单文件上限（MB）
_BACKUP_COUNT = 8           # 保留备份数
_CONSOLE_LEVEL = logging.DEBUG
_FILE_LEVEL = logging.INFO

# 日志格式
_FORMATTER_STR = "%(asctime)s [%(levelname)s][%(filename)s:%(lineno)d]: %(message)s"

# 已初始化的 logger 缓存
_initialized_loggers: Dict[str, logging.Logger] = {}
_console_handler: logging.StreamHandler | None = None


class _InfoOnlyFilter(logging.Filter):
    """仅放行 INFO 级别的日志记录（用于 INFO 文件处理器）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == logging.INFO


def _get_console_handler() -> logging.StreamHandler:
    """获取（或创建）全局共享的彩色控制台 Handler。"""
    global _console_handler
    if _console_handler is not None:
        return _console_handler

    console_handler = logging.StreamHandler()
    console_handler.setLevel(_CONSOLE_LEVEL)

    try:
        import colorlog
        color_formatter = colorlog.ColoredFormatter(
            f"%(log_color)s{_FORMATTER_STR}",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
        console_handler.setFormatter(color_formatter)
    except ImportError:
        # colorlog 未安装时降级为普通格式
        console_handler.setFormatter(logging.Formatter(_FORMATTER_STR))

    _console_handler = console_handler
    return console_handler


def get_logger(module_name: str) -> logging.Logger:
    """获取指定模块的独立 Logger。

    - 首次调用时自动创建 INFO 文件 + ERROR 文件 + 控制台三个 Handler。
    - 后续调用同一 module_name 直接返回缓存实例。

    Args:
        module_name: 模块标识，用于命名日志文件（如 Router、Agent、LLMClient）。

    Returns:
        配置好的 ``logging.Logger`` 实例。
    """
    if module_name in _initialized_loggers:
        return _initialized_loggers[module_name]

    # 确保日志目录存在
    os.makedirs(_LOG_DIR, exist_ok=True)

    # 创建模块级 logger
    module_logger = logging.getLogger(f"DataAnalysis.{module_name}")
    module_logger.setLevel(logging.DEBUG)
    module_logger.propagate = False  # 不向父 logger 传播，避免重复输出

    formatter = logging.Formatter(_FORMATTER_STR)

    # ── INFO 文件处理器（仅记录 INFO 级别）──────────────────
    info_file = os.path.join(_LOG_DIR, f"{module_name}.info.log")
    info_handler = RotatingFileHandler(
        info_file,
        maxBytes=_MAX_BYTES_MB * 1024 * 1024,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    info_handler.setLevel(_FILE_LEVEL)
    info_handler.addFilter(_InfoOnlyFilter())
    info_handler.setFormatter(formatter)
    module_logger.addHandler(info_handler)

    # ── ERROR 文件处理器（ERROR 及以上）────────────────────
    error_file = os.path.join(_LOG_DIR, f"{module_name}.error.log")
    error_handler = RotatingFileHandler(
        error_file,
        maxBytes=_MAX_BYTES_MB * 1024 * 1024,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    module_logger.addHandler(error_handler)

    # ── 控制台处理器（全局共享）────────────────────────────
    module_logger.addHandler(_get_console_handler())

    _initialized_loggers[module_name] = module_logger
    return module_logger
