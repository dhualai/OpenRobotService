## db/ — 数据库校验模块

本目录校验数据存储的正确性（数据完整性、缓存一致性、向量检索精准度）。
- checkers/ 存放各存储的校验器（mysql / redis / qdrant）
- MySQL 校验通过 conn.execute + assertions 而非 clients/
- 边界：db/ 校验数据完整性；clients/ 提供操作接口
