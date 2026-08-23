# config/ — 全局配置层

## 职责
- 单一入口管理自动化框架的全部配置。
- 支持多环境（local / sit / uat）自动切换。
- 配置以 YAML profile 文件定义，敏感字段通过环境变量注入。

## 环境切换
| 环境 | 环境变量值 | 说明 |
|------|-----------|------|
| local | AUTOMATION_ENV=local | 本地开发环境（默认） |
| sit | AUTOMATION_ENV=sit | 系统集成测试环境 |
| uat | AUTOMATION_ENV=uat | 用户验收测试环境 |

切换方式：
`ash
# Linux/Mac
export AUTOMATION_ENV=sit
# Windows
set AUTOMATION_ENV=sit
`

## 模块结构
| 文件 | 说明 |
|------|------|
| __init__.py | 公共 API 导出（load_config / get_env / is_env / models） |
| enums.py | ConfigEnv 枚举（local / sit / uat） |
| models.py | Pydantic 配置模型（AutomationConfig / ApiConfig / DatabaseConfig ...） |
| loader.py | YAML 加载器（ConfigLoader — 按环境加载 profile） |
| settings.py | 配置入口（load_config — 单函数 API） |
| profiles/local.yaml | 本地开发环境配置 |
| profiles/sit.yaml | SIT 环境配置 |
| profiles/uat.yaml | UAT 环境配置 |
| 	ests/test_enums.py | ConfigEnv 单元测试 |
| 	ests/test_loader.py | ConfigLoader 单元测试 |
| 	ests/test_settings.py | 公共 API 单元测试 |

## 使用示例
`python
from automation.config import load_config, get_env, is_env, ConfigEnv

# 自动检测环境（根据 AUTOMATION_ENV 环境变量）
config = load_config()

# 显式指定环境
config = load_config(env='sit')

# 获取配置
api_url = config.api.base_url          # http://localhost:8000
db_host = config.database.host         # localhost
redis_port = config.redis.port         # 6379
qdrant_host = config.qdrant.host       # localhost

# 运行时环境检测
current = get_env()                    # 'local'
is_sit = is_env(ConfigEnv.SIT)         # False
`

## 设计原则
1. 不依赖任何业务代码（backend/ / ai/ / frontend/）
2. 后续 framework 所有模块都通过 config 获取配置
3. 配置加载采用懒加载 + 缓存（get_config() 只加载一次）
4. 所有配置模型通过 Pydantic 提供类型提示和校验
