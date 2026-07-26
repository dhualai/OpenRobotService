# config/ — 全局配置层

## 职责
- settings.py 读取 AUTOMATION_ENV 环境变量，自动加载对应 profile。
- profiles/ 按环境（dev/staging/production）存放配置文件，包含 API 地址、数据库连接串、Qdrant 端点等。
- 敏感信息通过 .env 注入，不提交明文到版本控制。

## 边界
- 与业务代码（backend/）的配置完全独立，不从 backend 继承任何配置。
- 测试用例不直接引用本目录配置，应通过 Fixture 获取。

## 文件说明
| 文件 | 说明 |
|------|------|
| settings.py | 配置加载入口，根据 AUTOMATION_ENV 加载对应 profile |
| profiles/dev.yaml | 开发环境配置 |
| profiles/staging.yaml | 预发布环境配置 |
| profiles/production.yaml | 生产环境配置 |
