# api/ — API 自动化测试模块

## 职责
- 基于 httpx.AsyncClient 封装 HTTP 客户端，对后端 FastAPI 接口进行黑盒/集成测试。
- 通过 clients/ 按业务模块组织 endpoint 调用，测试用例不直接处理 HTTP 细节。

## 结构
| 目录/文件 | 说明 |
|-----------|------|
| conftest.py | API 测试 Fixture（client 注入、鉴权 Token） |
| clients/ | HTTP 客户端封装，每文件一个业务领域（wechat/ai_agent/task） |
| 	ests/ | 测试用例，按后端模块划分子目录 |
| utils/ | API 测试工具（JWT 生成、自定义断言） |

## Schema 策略
优先通过 pip install -e ../backend 导入 backend 的 Pydantic schema。
仅当 backend schema 不满足测试需求时，在测试文件内定义轻量 dataclass。
