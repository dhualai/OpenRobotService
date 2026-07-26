'''Log configuration model.'''

from typing import Optional
from pydantic import BaseModel, Field


class LogConfig(BaseModel):
    '''Configuration for the unified logging system.'''

    # Root logger level
    level: str = Field(default='INFO', description='Root logger level')

    # Console handler
    console_enabled: bool = Field(default=True, description='Enable console logging')
    console_level: str = Field(default='DEBUG', description='Console handler log level')
    console_format: str = Field(default='color', description='Console format: color / plain')
    console_use_colors: bool = Field(default=True, description='Use ANSI colors in console')

    # File handler
    file_enabled: bool = Field(default=True, description='Enable file logging')
    file_level: str = Field(default='DEBUG', description='File handler log level')
    file_path: str = Field(default='output/logs/automation.log', description='Log file path')
    file_format: str = Field(default='json', description='File format: json / plain')
    file_max_bytes: int = Field(default=10 * 1024 * 1024, description='Max log file size before rotation')
    file_backup_count: int = Field(default=5, description='Number of backup log files to keep')

    # Allure handler
    allure_enabled: bool = Field(default=True, description='Enable Allure log attachment')
    allure_level: str = Field(default='WARNING', description='Min log level for Allure attachment')

    # Logger naming
    framework_logger_name: str = Field(default='automation', description='Root logger name for framework components')

