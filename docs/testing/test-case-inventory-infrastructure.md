# 基础设施测试用例清单

> 格式按 [template-test-case.md](template-test-case.md)

---

## Config

### INFRA-TC-001 — test_members

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 ConfigEnv 枚举成员

**测试点：** 验证 ConfigEnv 枚举包含正确的环境值

**前置条件：** ConfigEnv 类已导入

**测试步骤：**
1. 检查枚举成员 → local/sit/uat/ci 都存在

**结果：** PASS

---

## Config

### INFRA-TC-002 — test_from_str_valid

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 from_str 有效转换

**测试点：** 验证 from_str 方法能正确转换字符串到枚举

**前置条件：** ConfigEnv 类已导入

**测试步骤：**
1. from_str("local") → ConfigEnv.LOCAL

**结果：** PASS

---

## Config

### INFRA-TC-003 — test_from_str_invalid

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 from_str 无效转换

**测试点：** 验证 from_str 对无效字符串抛出异常

**前置条件：** ConfigEnv 类已导入

**测试步骤：**
1. from_str("invalid") → 抛出 ValueError

**结果：** PASS

---

## Config

### INFRA-TC-004 — test_enum_value_behavior

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 枚举值行为

**测试点：** 验证枚举的 value 属性

**前置条件：** ConfigEnv 类已导入

**测试步骤：**
1. 验证各成员 value 正确

**结果：** PASS

---

## Config

### INFRA-TC-005 — test_all_enums_covered

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 所有环境覆盖

**测试点：** 验证枚举覆盖了所有预期的环境

**前置条件：** ConfigEnv 类已导入

**测试步骤：**
1. 检查枚举成员数量 → 符合预期

**结果：** PASS

---

## Config

### INFRA-TC-006 — test_load_profile

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 加载 YAML 配置

**测试点：** 验证 ConfigLoader 能正确加载 YAML 配置文件

**前置条件：** 配置目录存在

**测试步骤：**
1. load_profile("local") → 返回解析后的 dict

**结果：** PASS

---

## Config

### INFRA-TC-007 — test_load_raw

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 加载原始配置

**测试点：** 验证 ConfigLoader 的 load_raw 方法

**前置条件：** 配置目录存在

**测试步骤：**
1. load_raw("local") → 返回原始 YAML 内容

**结果：** PASS

---

## Config

### INFRA-TC-008 — test_load_profile_not_found

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 配置不存在处理

**测试点：** 验证配置文件不存在时抛出异常

**前置条件：** 不存在的配置名

**测试步骤：**
1. load_profile("nonexistent") → 抛出 FileNotFoundError

**结果：** PASS

---

## Config

### INFRA-TC-009 — test_get_config_caches

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 配置缓存

**测试点：** 验证配置加载后缓存

**前置条件：** 配置已加载

**测试步骤：**
1. 多次调用 get_config() → 返回同一实例

**结果：** PASS

---

## Config

### INFRA-TC-010 — test_env_property

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 env 属性

**测试点：** 验证 ConfigLoader 的 env 属性正确返回环境

**前置条件：** ConfigLoader 已初始化

**测试步骤：**
1. loader.env → 当前环境值

**结果：** PASS

---

## Config

### INFRA-TC-011 — test_detect_env_from_env_var

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 环境变量检测

**测试点：** 验证 AUTOMATION_ENV 环境变量能被检测

**前置条件：** 环境变量已设置

**测试步骤：**
1. 检出 AUTOMATION_ENV → 环境值匹配

**结果：** PASS

---

## Config

### INFRA-TC-012 — test_default_env

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 默认环境

**测试点：** 验证无环境变量时默认使用 local

**前置条件：** 环境变量未设置

**测试步骤：**
1. ConfigLoader() → env=local

**结果：** PASS

---

## Config

### INFRA-TC-013 — test_resolve_env

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 环境解析

**测试点：** 验证环境字符串解析

**前置条件：** ConfigLoader 已初始化

**测试步骤：**
1. resolve_env("auto") → 自动检测环境

**结果：** PASS

---

## Config

### INFRA-TC-014 — test_load_with_defaults_for_missing_fields

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 缺失字段默认值

**测试点：** 验证配置中缺失的字段使用默认值

**前置条件：** 配置存在缺失字段

**测试步骤：**
1. 加载配置 → 缺失字段使用默认值

**结果：** PASS

---

## Config

### INFRA-TC-015 — test_load_config_with_env

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 load_config 指定环境

**测试点：** 验证 load_config 可指定环境

**前置条件：** 配置目录存在

**测试步骤：**
1. load_config(env="sit") → 返回 sit 环境配置

**结果：** PASS

---

## Config

### INFRA-TC-016 — test_load_config_uses_env_var

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 load_config 使用环境变量

**测试点：** 验证 load_config 读取环境变量

**前置条件：** AUTOMATION_ENV 已设置

**测试步骤：**
1. load_config() → 读取环境变量指定环境

**结果：** PASS

---

## Config

### INFRA-TC-017 — test_load_config_defaults_to_local

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 load_config 默认 local

**测试点：** 验证无环境变量时默认 local

**前置条件：** 环境变量未设置

**测试步骤：**
1. load_config() → env=local

**结果：** PASS

---

## Config

### INFRA-TC-018 — test_load_config_with_env_override

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 环境变量覆盖

**测试点：** 验证环境变量可覆盖默认环境

**前置条件：** 环境变量已设置

**测试步骤：**
1. load_config() → 返回环境变量指定的配置

**结果：** PASS

---

## Config

### INFRA-TC-019 — test_load_config_not_found

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 配置不存在异常

**测试点：** 验证配置不存在时抛出异常

**前置条件：** 不存在的环境

**测试步骤：**
1. load_config(env="invalid") → 抛出异常

**结果：** PASS

---

## Config

### INFRA-TC-020 — test_load_config_returns_automation_config

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 返回 AutomationConfig

**测试点：** 验证返回类型

**前置条件：** 配置存在

**测试步骤：**
1. load_config() → 返回 AutomationConfig 对象

**结果：** PASS

---

## Config

### INFRA-TC-021 — test_get_env_default

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 get_env 默认值

**测试点：** 验证 get_env 返回默认值

**前置条件：** 无环境变量

**测试步骤：**
1. get_env() → 默认值

**结果：** PASS

---

## Config

### INFRA-TC-022 — test_get_env_from_var

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 get_env 环境变量

**测试点：** 验证 get_env 读取环境变量

**前置条件：** 环境变量已设置

**测试步骤：**
1. get_env() → 环境变量值

**结果：** PASS

---

## Config

### INFRA-TC-023 — test_get_env_case_insensitive

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 get_env 大小写不敏感

**测试点：** 验证环境变量大小写不敏感

**前置条件：** 环境变量已设置

**测试步骤：**
1. get_env() → 大小写不影响结果

**结果：** PASS

---

## Config

### INFRA-TC-024 — test_is_env_match

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 is_env 匹配

**测试点：** 验证 is_env 匹配当前环境

**前置条件：** ConfigLoader 已初始化

**测试步骤：**
1. is_env("local") → True/False

**结果：** PASS

---

## Config

### INFRA-TC-025 — test_is_env_default

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 is_env 默认

**测试点：** 验证 is_env 无参时匹配当前环境

**前置条件：** ConfigLoader 已初始化

**测试步骤：**
1. is_env() → True

**结果：** PASS

---

## Logger

### INFRA-TC-026 — test_setup_defaults

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 默认设置

**测试点：** 验证默认设置下创建 Console+File+Allure handler

**前置条件：** Logger 未初始化

**测试步骤：**
1. setup_logging() → root 有 3 个 handler

**结果：** PASS

---

## Logger

### INFRA-TC-027 — test_setup_twice_idempotent

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 重复设置幂等

**测试点：** 验证重复调用 setup_logging 不会重复添加 handler

**前置条件：** 已初始化 logger

**测试步骤：**
1. setup_logging() 两次 → handler 数量不变

**结果：** PASS

---

## Logger

### INFRA-TC-028 — test_setup_only_console

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 仅 console

**测试点：** 验证仅添加 console handler

**前置条件：** Logger 未初始化

**测试步骤：**
1. setup_logging(console_only=True) → 仅 ConsoleHandler

**结果：** PASS

---

## Logger

### INFRA-TC-029 — test_setup_different_level

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 不同日志级别

**测试点：** 验证不同日志级别过滤

**前置条件：** Logger 未初始化

**测试步骤：**
1. setup_logging(level=logging.WARNING) → 仅 WARNING 及以上

**结果：** PASS

---

## Logger

### INFRA-TC-030 — test_get_logger_basic

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 获取 logger

**测试点：** 验证 get_logger 返回正确名称的 logger

**前置条件：** Logger 已配置

**测试步骤：**
1. get_logger("test") → 返回名为 "test" 的 logger

**结果：** PASS

---

## Logger

### INFRA-TC-031 — test_get_logger_already_prefixed

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 已存在前缀

**测试点：** 验证已含 automation. 前缀时不重复添加

**前置条件：** Logger 已配置

**测试步骤：**
1. get_logger("automation.xxx") → 名称不变

**结果：** PASS

---

## Logger

### INFRA-TC-032 — test_get_logger_configures_correctly

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 正确配置

**测试点：** 验证 logger 配置正确

**前置条件：** Logger 已配置

**测试步骤：**
1. get_logger("test") → level/handlers/filter 正确

**结果：** PASS

---

## Logger

### INFRA-TC-033 — test_console_creates_handler

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 console handler 创建

**测试点：** 验证 ConsoleHandler 创建正确

**前置条件：** ConsoleHandler 类可用

**测试步骤：**
1. ConsoleHandler() → handler 实例

**结果：** PASS

---

## Logger

### INFRA-TC-034 — test_console_level_filtering

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 console 级别过滤

**测试点：** 验证 console handler 级别过滤

**前置条件：** ConsoleHandler 已创建

**测试步骤：**
1. handler 设置 level → 按级别过滤

**结果：** PASS

---

## Logger

### INFRA-TC-035 — test_file_creates_log

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 log 文件创建

**测试点：** 验证日志文件被创建

**前置条件：** FileHandler 配置正确

**测试步骤：**
1. setup_logging() → log 文件存在

**结果：** PASS

---

## Logger

### INFRA-TC-036 — test_file_json_format

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 JSON 格式日志

**测试点：** 验证 JSON 格式的日志输出

**前置条件：** FileHandler 已配置

**测试步骤：**
1. 写入日志 → 文件内容为 JSON

**结果：** PASS

---

## Logger

### INFRA-TC-037 — test_file_creates_parent_dir

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 创建父目录

**测试点：** 验证日志目录不存在时自动创建

**前置条件：** 日志目录不存在

**测试步骤：**
1. setup_logging() → 父目录被创建

**结果：** PASS

---

## Logger

### INFRA-TC-038 — test_allure_handler_attached

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Allure handler 附加

**测试点：** 验证 AllureLogHandler 正确附加

**前置条件：** Logger 未初始化

**测试步骤：**
1. setup_logging() → 含 AllureLogHandler

**结果：** PASS

---

## Logger

### INFRA-TC-039 — test_allure_handler_graceful

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Allure handler 优雅处理

**测试点：** 验证 AllureLogHandler 无 Allure 环境时优雅降级

**前置条件：** 无 Allure 环境

**测试步骤：**
1. AllureLogHandler().emit() → 不崩溃

**结果：** PASS

---

## Logger

### INFRA-TC-040 — test_reset_clears_handlers

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 reset 清空 handlers

**测试点：** 验证 reset_logging 清空所有 handler

**前置条件：** Logger 已配置

**测试步骤：**
1. reset_logging() → root handlers 为空

**结果：** PASS

---

## Logger

### INFRA-TC-041 — test_setup_after_reset_works

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 reset 后重新配置

**测试点：** 验证 reset 后 setup 正常工作

**前置条件：** Logger 已 reset

**测试步骤：**
1. reset -> setup → handler 数量正常

**结果：** PASS

---

## Clients

### INFRA-TC-042 — test_connect

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 ApiClient 连接

**测试点：** 验证 ApiClient.connect() 正确初始化 httpx.AsyncClient

**前置条件：** ApiConfig 已提供

**测试步骤：**
1. ApiClient(config).connect() → is_connected=True

**结果：** PASS

---

## Clients

### INFRA-TC-043 — test_connect_sets_base_url

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 base_url 设置

**测试点：** 验证 base_url 正确设置

**前置条件：** ApiConfig 已提供

**测试步骤：**
1. client.base_url → 等于配置值

**结果：** PASS

---

## Clients

### INFRA-TC-044 — test_close

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 ApiClient 关闭

**测试点：** 验证 ApiClient.close() 正确释放资源

**前置条件：** ApiClient 已连接

**测试步骤：**
1. client.close() → is_connected=False, _client=None

**结果：** PASS

---

## Clients

### INFRA-TC-045 — test_async_context_manager

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 异步上下文管理器

**测试点：** 验证 async with 语法正确工作

**前置条件：** ApiConfig 已提供

**测试步骤：**
1. async with ApiClient() as client → 自动 connect/close

**结果：** PASS

---

## Clients

### INFRA-TC-046 — test_request_without_connect_raises

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 未连接时请求

**测试点：** 验证未连接时调用 request 抛出异常

**前置条件：** ApiClient 未连接

**测试步骤：**
1. client.request("GET","/test") → ClientConnectionError

**结果：** PASS

---

## Clients

### INFRA-TC-047 — test_successful_request

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 成功请求

**测试点：** 验证成功请求返回正确响应

**前置条件：** ApiClient 已连接；Mock 后端

**测试步骤：**
1. client.request("GET","/test") → 200

**结果：** PASS

---

## Clients

### INFRA-TC-048 — test_request_authentication_error

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 认证错误

**测试点：** 验证 401 响应抛出 AuthenticationError

**前置条件：** ApiClient 已连接

**测试步骤：**
1. client.request() → 401 响应 → AuthenticationError

**结果：** PASS

---

## Clients

### INFRA-TC-049 — test_request_connection_error

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 连接错误

**测试点：** 验证连接失败抛出 ClientConnectionError

**前置条件：** ApiClient 已连接

**测试步骤：**
1. 连接不上后端 → ClientConnectionError

**结果：** PASS

---

## Clients

### INFRA-TC-050 — test_request_timeout

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 超时错误

**测试点：** 验证超时抛出 ClientTimeoutError

**前置条件：** ApiClient 已连接

**测试步骤：**
1. 请求超时 → ClientTimeoutError

**结果：** PASS

---

## Clients

### INFRA-TC-051 — test_config_loaded_from_defaults

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 默认配置加载

**测试点：** 验证无配置时使用默认值

**前置条件：** 无参数

**测试步骤：**
1. ApiClient() → 使用默认配置

**结果：** PASS

---

## Clients

### INFRA-TC-052 — test_retry_config_defaults

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 RetryConfig 默认值

**测试点：** 验证 RetryConfig 默认参数

**前置条件：** RetryConfig 类已导入

**测试步骤：**
1. RetryConfig() → max_retries=3, delay=1.0

**结果：** PASS

---

## Clients

### INFRA-TC-053 — test_retry_config_custom_values

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 RetryConfig 自定义值

**测试点：** 验证 RetryConfig 自定义参数

**前置条件：** RetryConfig 类已导入

**测试步骤：**
1. RetryConfig(max_retries=5) → max_retries=5

**结果：** PASS

---

## Clients

### INFRA-TC-054 — test_sync_success_on_first_attempt

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 同步首次成功

**测试点：** 验证同步 retry 首次成功不重试

**前置条件：** retry 装饰器已导入

**测试步骤：**
1. 函数首次成功 → 不重试

**结果：** PASS

---

## Clients

### INFRA-TC-055 — test_sync_retry_on_failure_then_success

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 同步重试后成功

**测试点：** 验证同步 retry 失败后重试成功

**前置条件：** retry 装饰器已导入

**测试步骤：**
1. 失败 N 次后成功 → 重试 N 次

**结果：** PASS

---

## Clients

### INFRA-TC-056 — test_sync_exhaust_retries

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 同步耗尽重试

**测试点：** 验证同步 retry 耗尽重试次数后抛出

**前置条件：** retry 装饰器已导入

**测试步骤：**
1. 持续失败 → 重试耗尽 → 抛出异常

**结果：** PASS

---

## Clients

### INFRA-TC-057 — test_sync_non_retryable_exception

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 同步不可重试异常

**测试点：** 验证不可重试异常不重试

**前置条件：** retry 装饰器已导入

**测试步骤：**
1. 抛出不可重试异常 → 不重试，直接抛出

**结果：** PASS

---

## Clients

### INFRA-TC-058 — test_async_success

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 异步首次成功

**测试点：** 验证异步 retry 首次成功不重试

**前置条件：** async_retry 装饰器已导入

**测试步骤：**
1. async 函数首次成功 → 不重试

**结果：** PASS

---

## Clients

### INFRA-TC-059 — test_async_retry_then_success

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 异步重试后成功

**测试点：** 验证异步 retry 失败后重试成功

**前置条件：** async_retry 装饰器已导入

**测试步骤：**
1. 失败 N 次后成功 → 重试 N 次

**结果：** PASS

---

## Clients

### INFRA-TC-060 — test_async_exhaust

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 异步耗尽重试

**测试点：** 验证异步 retry 耗尽重试

**前置条件：** async_retry 装饰器已导入

**测试步骤：**
1. 持续失败 → 重试耗尽 → 抛出异常

**结果：** PASS

---

## Clients

### INFRA-TC-061 — test_base_initial_state

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 BaseClient 初始状态

**测试点：** 验证 BaseClient 初始状态

**前置条件：** BaseClient 实例已创建

**测试步骤：**
1. is_connected=False, _client=None

**结果：** PASS

---

## Clients

### INFRA-TC-062 — test_base_connect_not_implemented

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 connect 未实现

**测试点：** 验证未实现 connect 抛出 NotImplementedError

**前置条件：** BaseClient 子类未实现 connect

**测试步骤：**
1. connect() → NotImplementedError

**结果：** PASS

---

## Clients

### INFRA-TC-063 — test_base_close_not_implemented

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 close 未实现

**测试点：** 验证未实现 close 抛出 NotImplementedError

**前置条件：** BaseClient 子类未实现 close

**测试步骤：**
1. close() → NotImplementedError

**结果：** PASS

---

## Clients

### INFRA-TC-064 — test_mysql_connect

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 MySQLClient 连接

**测试点：** 验证 MySQLClient.connect() 初始化连接

**前置条件：** MySQLConfig 已提供

**测试步骤：**
1. MySQLClient(config).connect() → is_connected=True

**结果：** PASS

---

## Clients

### INFRA-TC-065 — test_mysql_connect_sets_cursor

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 MySQL cursor 设置

**测试点：** 验证 cursor 属性被设置

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. client._cursor → 不为 None

**结果：** PASS

---

## Clients

### INFRA-TC-066 — test_mysql_close

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 MySQLClient 关闭

**测试点：** 验证 MySQLClient.close() 释放资源

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. close() → is_connected=False

**结果：** PASS

---

## Clients

### INFRA-TC-067 — test_mysql_execute_without_connect_raises

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 MySQL 未连接执行

**测试点：** 验证未连接时 execute 抛出异常

**前置条件：** MySQLClient 未连接

**测试步骤：**
1. execute("SELECT 1") → RuntimeError

**结果：** PASS

---

## Clients

### INFRA-TC-068 — test_mysql_execute_success

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 MySQL 执行成功

**测试点：** 验证 execute 方法

**前置条件：** MySQLClient 已连接；Mock 后端

**测试步骤：**
1. execute("SELECT 1") → 返回结果

**结果：** PASS

---

## Clients

### INFRA-TC-069 — test_mysql_fetch_one

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 MySQL fetch_one

**测试点：** 验证 fetch_one 方法

**前置条件：** MySQLClient 已连接；Mock 后端

**测试步骤：**
1. fetch_one("SELECT 1") → 返回一行

**结果：** PASS

---

## Clients

### INFRA-TC-070 — test_redis_connect_missing_library

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Redis 缺少库

**测试点：** 验证 redis 库未安装时的处理

**前置条件：** redis 库未安装

**测试步骤：**
1. RedisClient(config).connect() → ImportError

**结果：** PASS

---

## Clients

### INFRA-TC-071 — test_redis_connect_success

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Redis 连接成功

**测试点：** 验证 RedisClient.connect() 成功

**前置条件：** redis 库已安装；Mock 后端

**测试步骤：**
1. RedisClient(config).connect() → is_connected=True

**结果：** PASS

---

## Clients

### INFRA-TC-072 — test_redis_get_without_connect_raises

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Redis 未连接 get

**测试点：** 验证未连接时 get 抛出异常

**前置条件：** RedisClient 未连接

**测试步骤：**
1. get("key") → RuntimeError

**结果：** PASS

---

## Clients

### INFRA-TC-073 — test_redis_set_without_connect_raises

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Redis 未连接 set

**测试点：** 验证未连接时 set 抛出异常

**前置条件：** RedisClient 未连接

**测试步骤：**
1. set("key", "val") → RuntimeError

**结果：** PASS

---

## Clients

### INFRA-TC-074 — test_redis_delete_without_connect_raises

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Redis 未连接 delete

**测试点：** 验证未连接时 delete 抛出异常

**前置条件：** RedisClient 未连接

**测试步骤：**
1. delete("key") → RuntimeError

**结果：** PASS

---

## Clients

### INFRA-TC-075 — test_redis_exists_without_connect_raises

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Redis 未连接 exists

**测试点：** 验证未连接时 exists 抛出异常

**前置条件：** RedisClient 未连接

**测试步骤：**
1. exists("key") → RuntimeError

**结果：** PASS

---

## Clients

### INFRA-TC-076 — test_qdrant_connect_missing_library

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Qdrant 缺少库

**测试点：** 验证 qdrant 库未安装时的处理

**前置条件：** qdrant 库未安装

**测试步骤：**
1. QdrantClient(config).connect() → ImportError

**结果：** PASS

---

## Clients

### INFRA-TC-077 — test_qdrant_connect_success

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Qdrant 连接成功

**测试点：** 验证 QdrantClient.connect() 成功

**前置条件：** qdrant 库已安装；Mock 后端

**测试步骤：**
1. QdrantClient(config).connect() → is_connected=True

**结果：** PASS

---

## Clients

### INFRA-TC-078 — test_qdrant_search_without_connect_raises

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Qdrant 未连接 search

**测试点：** 验证未连接时 search 抛出异常

**前置条件：** QdrantClient 未连接

**测试步骤：**
1. search("query") → RuntimeError

**结果：** PASS

---

## Clients

### INFRA-TC-079 — test_qdrant_upsert_without_connect_raises

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Qdrant 未连接 upsert

**测试点：** 验证未连接时 upsert 抛出异常

**前置条件：** QdrantClient 未连接

**测试步骤：**
1. upsert("points") → RuntimeError

**结果：** PASS

---

## Clients

### INFRA-TC-080 — test_qdrant_close_collection_exists

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 Qdrant close/exists

**测试点：** 验证 QdrantClient close 和 collection_exists

**前置条件：** QdrantClient 已连接

**测试步骤：**
1. close() → is_connected=False / collection_exists("x") → bool

**结果：** PASS

---

## Assertions

### INFRA-TC-081 — test_equal

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 assert_equal 相等

**测试点：** 验证相等的值断言通过

**前置条件：** assert_equal 函数已导入

**测试步骤：**
1. assert_equal(1, 1) → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-082 — test_not_equal

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 assert_equal 不等

**测试点：** 验证不相等的值断言失败

**前置条件：** assert_equal 函数已导入

**测试步骤：**
1. assert_equal(1, 2) → 断言失败

**结果：** PASS

---

## Assertions

### INFRA-TC-083 — test_custom_message

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 自定义断言消息

**测试点：** 验证自定义错误消息

**前置条件：** assert_equal 函数已导入

**测试步骤：**
1. assert_equal(1, 2, "custom msg") → 消息包含 "custom msg"

**结果：** PASS

---

## Assertions

### INFRA-TC-084 — test_non_empty

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 非空

**测试点：** 验证非空集合通过

**前置条件：** assert_not_empty 函数已导入

**测试步骤：**
1. assert_not_empty([1,2]) → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-085 — test_empty_list

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 空列表

**测试点：** 验证空列表断言失败

**前置条件：** assert_not_empty 函数已导入

**测试步骤：**
1. assert_not_empty([]) → 断言失败

**结果：** PASS

---

## Assertions

### INFRA-TC-086 — test_empty_string

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 空字符串

**测试点：** 验证空字符串断言失败

**前置条件：** assert_not_empty 函数已导入

**测试步骤：**
1. assert_not_empty("") → 断言失败

**结果：** PASS

---

## Assertions

### INFRA-TC-087 — test_contains_list

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 包含列表元素

**测试点：** 验证列表包含元素

**前置条件：** assert_contains 函数已导入

**测试步骤：**
1. assert_contains([1,2,3], 2) → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-088 — test_contains_dict

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 包含字典键

**测试点：** 验证字典包含键

**前置条件：** assert_contains 函数已导入

**测试步骤：**
1. assert_contains({"a":1}, "a") → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-089 — test_not_contains

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 不包含

**测试点：** 验证不包含时断言失败

**前置条件：** assert_contains 函数已导入

**测试步骤：**
1. assert_contains([1,2], 3) → 断言失败

**结果：** PASS

---

## Assertions

### INFRA-TC-090 — test_subset_match

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 dict 子集匹配

**测试点：** 验证字典子集匹配

**前置条件：** assert_dict_contains_subset 已导入

**测试步骤：**
1. assert_dict_contains_subset({"a":1}, {"a":1,"b":2}) → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-091 — test_nested_subset

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 嵌套子集匹配

**测试点：** 验证嵌套字典子集匹配

**前置条件：** assert_dict_contains_subset 已导入

**测试步骤：**
1. 嵌套字典子集匹配 → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-092 — test_subset_mismatch

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 子集不匹配

**测试点：** 验证子集不匹配时断言失败

**前置条件：** assert_dict_contains_subset 已导入

**测试步骤：**
1. 子集不匹配 → 断言失败

**结果：** PASS

---

## Assertions

### INFRA-TC-093 — test_records_match

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 记录匹配

**测试点：** 验证数据库记录匹配

**前置条件：** assert_records_match 已导入

**测试步骤：**
1. 记录匹配 → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-094 — test_records_count_mismatch

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 记录数不匹配

**测试点：** 验证记录数不匹配时断言失败

**前置条件：** assert_records_match 已导入

**测试步骤：**
1. 记录数不匹配 → 断言失败

**结果：** PASS

---

## Assertions

### INFRA-TC-095 — test_status_code_match

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 status_code 匹配

**测试点：** 验证 HTTP 状态码匹配

**前置条件：** assert_status_code 已导入

**测试步骤：**
1. assert_status_code(response, 200) → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-096 — test_status_code_mismatch

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 status_code 不匹配

**测试点：** 验证状态码不匹配时断言失败

**前置条件：** assert_status_code 已导入

**测试步骤：**
1. assert_status_code(response, 404) → 断言失败

**结果：** PASS

---

## Assertions

### INFRA-TC-097 — test_valid_json

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 有效 JSON

**测试点：** 验证 JSON 响应断言

**前置条件：** assert_json_response 已导入

**测试步骤：**
1. JSON 响应 → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-098 — test_non_json

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 非 JSON 响应

**测试点：** 验证非 JSON 响应断言失败

**前置条件：** assert_json_response 已导入

**测试步骤：**
1. 非 JSON 响应 → 断言失败

**结果：** PASS

---

## Assertions

### INFRA-TC-099 — test_error_match

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 错误信息匹配

**测试点：** 验证错误响应信息匹配

**前置条件：** assert_error_response 已导入

**测试步骤：**
1. 错误信息匹配 → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-100 — test_error_status_only

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 仅状态码匹配

**测试点：** 验证仅匹配状态码

**前置条件：** assert_error_response 已导入

**测试步骤：**
1. 仅状态码匹配 → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-101 — test_max_duration_pass

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 最大时长通过

**测试点：** 验证执行时间在最大时长内

**前置条件：** assert_max_duration 已导入

**测试步骤：**
1. 快速函数 → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-102 — test_min_duration_pass

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 最小时长通过

**测试点：** 验证执行时间在最短时长外

**前置条件：** assert_min_duration 已导入

**测试步骤：**
1. 慢速函数 → 通过

**结果：** PASS

---

## Assertions

### INFRA-TC-103 — test_duration_between_pass

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 时长区间通过

**测试点：** 验证执行时间在区间内

**前置条件：** assert_duration_between 已导入

**测试步骤：**
1. 中速函数 → 通过

**结果：** PASS

---

## Fixtures

### INFRA-TC-104 — test_config_loaded

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 config fixture 加载

**测试点：** 验证 config fixture 正确返回配置

**前置条件：** Conftest 已配置

**测试步骤：**
1. config fixture → AutomationConfig 对象

**结果：** PASS

---

## Fixtures

### INFRA-TC-105 — test_config_env_logic

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 config env 逻辑

**测试点：** 验证 config fixture 的环境判断逻辑

**前置条件：** Conftest 已配置

**测试步骤：**
1. config.is_local() → True/False

**结果：** PASS

---

## Fixtures

### INFRA-TC-106 — test_logger

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 logger fixture

**测试点：** 验证 logger fixture 正确设置日志

**前置条件：** Conftest 已配置

**测试步骤：**
1. setup_logging() → logger 可用

**结果：** PASS

---

## Fixtures

### INFRA-TC-107 — test_api_client_creates_with_config

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 api_client 创建

**测试点：** 验证 api_client fixture 使用配置

**前置条件：** Conftest 已配置

**测试步骤：**
1. api_client → 使用 config 中的 api 配置

**结果：** PASS

---

## Fixtures

### INFRA-TC-108 — test_mysql_client_creates_with_config

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 mysql_client 创建

**测试点：** 验证 mysql_client fixture 使用配置

**前置条件：** Conftest 已配置

**测试步骤：**
1. mysql_client → 使用 config 中的 mysql 配置

**结果：** PASS

---

## Fixtures

### INFRA-TC-109 — test_redis_client_creates_with_config

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 redis_client 创建

**测试点：** 验证 redis_client fixture 使用配置

**前置条件：** Conftest 已配置

**测试步骤：**
1. redis_client → 使用 config 中的 redis 配置

**结果：** PASS

---

## Fixtures

### INFRA-TC-110 — test_qdrant_client_creates_with_config

**属性：** 优先级 P2 · 自动化 · 冒烟 是 · 功能点 qdrant_client 创建

**测试点：** 验证 qdrant_client fixture 使用配置

**前置条件：** Conftest 已配置

**测试步骤：**
1. qdrant_client → 使用 config 中的 qdrant 配置

**结果：** PASS

---
