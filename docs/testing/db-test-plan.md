# DB 模块测试计划

## 概述

DB 模块基于 `automation/clients/` 已有的 MySQLClient / RedisClient / QdrantClient，
提供数据校验（Checker）和数据准备（DataBuilder）两类工具，
覆盖数据库层的一致性和完整性验证。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                   DB 测试模块                            │
│                                                         │
│  Checker（断言层）       DataBuilder（数据准备层）         │
│  ┌─────────────┐        ┌──────────────┐               │
│  │MySQLChecker │        │MySQLDataBuilder│              │
│  │RedisChecker │        │RedisDataBuilder│              │
│  │QdrantChecker│        │QdrantDataBuilder│             │
│  └──────┬──────┘        └──────┬───────┘               │
│         │                      │                        │
│         └──────────┬───────────┘                        │
│                    ▼                                    │
│         Client 层（已有实现）                             │
│  ┌────────────────────────────────┐                     │
│  │ MySQLClient / RedisClient      │                     │
│  │ QdrantClient（含 retry/日志）   │                    │
│  └────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────┘
```

## MySQL Checker（11 用例）

### assert_row_exists
- 验证某行存在，返回行数据
- 正向：预期行存在 → 返回 dict
- 负向：行不存在 → AssertionError

### assert_row_not_exists
- 验证某行不存在
- 正向：行确实不存在 → 静默通过
- 负向：行意外存在 → AssertionError

### assert_row_count
- 验证行数约束（exact / min / max）
- 支持精确值、最小值、最大值三种模式

### assert_matches
- 验证行字段子集匹配
- 自动跳过 id/created_at 等技术字段

### assert_column_values
- 验证列只包含预期枚举值
- 检出不在白名单的值

## Redis Checker（8 用例）

### assert_key_exists / assert_key_not_exists
- 验证 Redis key 存在或不存在
- 依赖 RedisClient.get() / exists()

### assert_value_equals / assert_value_contains
- 验证值精确匹配或包含子串
- 先隐式 assert_key_exists 再比较值

## Qdrant Checker（7 用例）

### assert_collection_exists / assert_collection_not_exists
- 验证向量集合存在或不存在

### assert_search_returns
- 验证搜索结果包含指定 point ID
- 参数化支持部分命中检查

### assert_point_count
- 验证集合中点数量与预期一致
- 通过 search + 大 limit 估算

## 测试数据准备

| Builder | 方法 | 用途 |
|---------|------|------|
| MySQLDataBuilder | insert(table, data) | 插入单行 |
| MySQLDataBuilder | insert_batch(table, rows) | 批量插入 |
| RedisDataBuilder | set_key(key, value) | 设置缓存 |
| RedisDataBuilder | delete_key(key) | 清理缓存 |
| QdrantDataBuilder | insert_points(collection, points) | 插入向量 |

## 运行方式

```bash
# 全部 DB 测试
pytest automation/db/tests/ -v

# 单文件
pytest automation/db/tests/test_mysql.py -v

# 指定 checker
pytest automation/db/tests/ -k "Qdrant" -v
```

## 当前状态

**状态**：✅ 已实现并提交（ba595b7）
**测试数**：26 用例
**依赖**：无外部服务（基于 mock client）
**下一步**：接入真实数据库后验证 checker 行为一致性
