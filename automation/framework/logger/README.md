# framework/logger/ — 统一日志模块

## 职责
为自动化框架提供统一的日志基础设施，支持四种输出方式：

| 输出方式 | 说明 | 实现 |
|----------|------|------|
| Console | 终端输出，支持 ANSI 颜色编码 | ConsoleColorHandler |
| File | 滚动文件输出，支持 JSON 或纯文本格式 | RotatingFileHandler |
| pytest | 原生集成，通过 caplog 捕获 | 直接使用 pytest 的 logging fixture |
| Allure | 将日志附加到 Allure 报告中 | AllureLogHandler |

## 使用方式

`python
from automation.framework.logger import setup_logging, get_logger, LogConfig

# 使用默认配置（console + file + allure）
setup_logging()

# 自定义配置
cfg = LogConfig(
    level='DEBUG',
    file_path='output/logs/debug.log',
    console_enabled=True,
    file_enabled=True,
    allure_enabled=True,
)
setup_logging(cfg)

# 获取 Logger
log = get_logger(__name__)
log.info('Test step {} completed', step)
log.error('Unexpected error', exc_info=True)
`

## 配置说明（LogConfig）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| level | INFO | 根日志级别 |
| console_enabled | True | 启用控制台输出 |
| console_level | DEBUG | 控制台日志级别 |
| console_use_colors | True | 使用 ANSI 颜色 |
| file_enabled | True | 启用文件输出 |
| file_path | output/logs/automation.log | 日志文件路径 |
| file_format | json | 文件格式（json/plain） |
| file_max_bytes | 10MB | 文件滚动大小 |
| file_backup_count | 5 | 保留备份文件数 |
| allure_enabled | True | 启用 Allure 附加 |
| allure_level | WARNING | Allure 最低日志级别 |

## 设计原则
- 基于 Python 标准 logging 模块，不重复造轮子
- 通过 LogConfig 实现配置驱动
- Allure 集成可降级（allure 不可用时静默跳过）
- 幂等初始化（setup_logging 多次调用只生效一次）
