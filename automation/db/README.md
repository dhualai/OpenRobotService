# db/ — 数据存储校验模块

## 职责
- 覆盖 MySQL、Redis、Qdrant 三种数据存储的正确性校验。
- 每种存储一个 checker 类，提供领域断言方法（如 assert_row_count、assert_key_exists）。

## 结构
| 目录/文件 | 说明 |
|-----------|------|
| conftest.py | 数据库连接 Fixture（事务级隔离） |
| checkers/ | 各存储校验器：mysql_checker / redis_checker / qdrant_checker |
| 	ests/ | 测试用例：数据完整性 / 缓存一致性 / 向量检索精准度 |
| utils/ | 测试数据准备与清理 |

## 隔离策略
- MySQL：SAVEPOINT 回滚
- Redis：	est: 前缀 + teardown 清理
- Qdrant：临时 collection + teardown 删除
