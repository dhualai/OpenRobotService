# Task-04: 统一客户端模块实现

## 基本信息
| 字段 | 值 |
|------|-----|
| 任务编号 | TASK-04 |
| 任务名称 | 统一客户端模块实现 |
| 模块路径 | automation/framework/clients/ |
| 分支 | hxg |
| 创建日期 | 2026-07-26 |
| 状态 | 已完成 |

## 完成内容

### 模块文件
| 文件 | 说明 |
|------|------|
| exceptions.py | ClientError 异常层次（6 种异常） |
| base.py | BaseClient + RetryConfig + sync_retry/async_retry |
| api_client.py | ApiClient（httpx.AsyncClient，异步） |
| mysql_client.py | MySQLClient（pymysql，同步） |
| redis_client.py | RedisClient（redis-py，同步，软导入） |
| qdrant_client.py | QdrantClient（qdrant-client，同步，软导入） |
| __init__.py | 公共 API 导出 |

### 统一能力
1. 超时: 通过 ApiConfig/database/redis 配置的 timeout 字段控制
2. 重试: sync_retry / async_retry 装饰器，指数退避，可配置次数/延迟
3. 日志: BaseClient.log 使用 framework.logger 的统一日志
4. 异常: ClientError -> ConnectionError/TimeoutError/AuthError/QueryError/RetryExhaustedError
5. 生命周期: connect/close + 上下文管理器（with / async with）

### 测试结果
41 passed in 9.33s

| 测试文件 | 用例数 | 主要内容 |
|----------|--------|----------|
| test_base.py | 11 | RetryConfig、sync_retry、async_retry、BaseClient |
| test_api_client.py | 10 | 连接/关闭/请求/认证/超时/配置 |
| test_db_clients.py | 20 | MySQL/Redis/Qdrant 各客户端 |

## API 文档
`python
from automation.framework.clients import ApiClient, MySQLClient, RedisClient, QdrantClient
from automation.framework.clients import RetryConfig

# 所有客户端支持上下文管理器
async with ApiClient() as client:
    resp = await client.request('GET', '/endpoint')

with MySQLClient() as db:
    rows = db.fetch_all('SELECT * FROM table')

with RedisClient() as cache:
    cache.set('key', 'value', ex=3600)

with QdrantClient() as qd:
    results = qd.search('collection', [0.1, 0.2])
`

## 参考
- 设计文档: docs/testing/framework-design.md
- 前序任务: task-01-framework-init.md, task-02-config.md, task-03-logger.md
