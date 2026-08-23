# Task-02: 配置模块实现

## 基本信息

| 字段 | 值 |
|------|-----|
| 任务编号 | TASK-02 |
| 任务名称 | 配置模块实现 |
| 模块路径 | automation/config/ |
| 设计文档 | docs/testing/framework-design.md |
| 分支 | hxg |
| 创建日期 | 2026-07-26 |
| 状态 | 已完成 |

## 完成内容

### 配置文件
| 文件 | 说明 |
|------|------|
| profiles/local.yaml | 本地开发环境配置（localhost:8000） |
| profiles/sit.yaml | SIT 环境配置（sit.openrobot.local） |
| profiles/uat.yaml | UAT 环境配置（uat.openrobot.local） |

每个 profile 包含：api / database / redis / qdrant / deepseek / wechat / playwright 共 7 个配置域。

### 核心模块
| 文件 | 说明 |
|------|------|
| enums.py | ConfigEnv 枚举（local / sit / uat），含 from_str 解析 |
| models.py | 7 个 Pydantic SubConfig + AutomationConfig 顶层模型 |
| loader.py | ConfigLoader — 按环境加载 YAML profile 并解析为模型 |
| settings.py | 统一配置入口（load_config / get_env / is_env） |
| __init__.py | 公共 API 导出 |

### 单元测试
| 文件 | 用例数 | 说明 |
|------|--------|------|
| 	ests/test_enums.py | 5 | ConfigEnv 枚举行为 |
| 	ests/test_loader.py | 10 | YAML 加载、缓存、env 检测、缺省值 |
| 	ests/test_settings.py | 10 | load_config / get_env / is_env 公共 API |

### 接口说明
`python
# 公共 API（通过 __init__.py 导出）
load_config(env=None, profiles_dir=None) -> AutomationConfig
get_env() -> str
is_env(env: ConfigEnv) -> bool

# ConfigEnv 枚举
ConfigEnv.LOCAL / .SIT / .UAT
ConfigEnv.from_str('sit') -> ConfigEnv.SIT

# AutomationConfig 模型
config.api.base_url       # str
config.database.host      # str
config.redis.port         # int
config.qdrant.host        # str
config.deepseek.model     # str
config.wechat.app_id      # str
config.playwright.browser # str
`

### 更新内容
- 旧 profiles（dev/staging/production）替换为（local/sit/uat）
- pyproject.toml 添加 pydantic-settings 依赖
- pyproject.toml testpaths 添加 config/
- config/README.md 全面更新

## 实现原则
1. 不依赖任何业务代码（backend/ / ai/ / frontend/）
2. 所有后续模块通过 config 获取配置
3. 懒加载 + 缓存（单次加载）
4. Pydantic 提供类型提示和校验

## 待办事项
- [ ] 运行测试验证：pytest -v automation/config/tests/
- [ ] 安装依赖：pip install -e automation/

## 参考
- 设计文档：docs/testing/framework-design.md
- type:feat
- 相关模块：automation/config/ 提供给后续 api/ / ai/ / db/ 等模块使用
