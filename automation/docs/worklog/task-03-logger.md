# Task-03: 统一日志模块实现

## 基本信息

| 字段 | 值 |
|------|-----|
| 任务编号 | TASK-03 |
| 任务名称 | 统一日志模块实现 |
| 模块路径 | automation/framework/logger/ |
| 设计文档 | docs/testing/framework-design.md |
| 分支 | hxg |
| 创建日期 | 2026-07-26 |
| 状态 | 已完成 |

## 完成内容

### 模块文件
| 文件 | 说明 |
|------|------|
| config.py | LogConfig Pydantic 模型（12 个配置字段） |
| handlers.py | ConsoleColorHandler / RotatingFileHandler / AllureLogHandler |
| core.py | setup_logging / get_logger / reset_logging |
| __init__.py | 公共 API 导出 |

### 四条输出路径
1. **Console**: ANSI 颜色编码的终端输出，每个级别不同颜色
2. **File**: RotatingFileHandler 滚动文件，支持 JSON 和 plain 两种格式
3. **pytest**: 通过 pytest 原生的 caplog fixture 捕获，无需额外适配
4. **Allure**: 自定义 AllureLogHandler 将 WARNING 及以上日志附加到 Allure 报告

### 单元测试（16 个）
- setup_logging 幂等性、默认值、自定义配置
- get_logger 命名规则（automation. 前缀）
- Console handler 创建、级别过滤
- File handler 文件创建、JSON 格式、目录自动创建
- Allure handler 注册、优雅降级
- reset_logging 清理和重新配置

### 清理
- 删除旧的 utomation/utils/logger.py 占位文件
- 日志模块统一迁移到 utomation/framework/logger/

## API 文档
`python
setup_logging(config: Optional[LogConfig] = None) -> None
get_logger(name: str) -> logging.Logger
reset_logging() -> None
`

## 参考
- 设计文档：docs/testing/framework-design.md
- 前序任务：task-01-framework-init.md, task-02-config.md
