# task-54：knowledge 模块数据库集成测试（P1 层首批）

## 本次目标

为 P1 Database 层补第一批真实库集成测试，选定 knowledge 模块（知识生产闭环地基，直接服务阶段 3 护城河目标），验证 6 张表的约束、软删语义、JSON 往返与结算契约。

## 阅读内容

- `automation/AGENTS.md`（工作流与边界）
- `automation/docs/gap-analysis-db-integration.md`（本次缺口分析）
- `automation/docs/testing/analysis/analysis-knowledge-db.md`（模块七要素分析）
- `backend/app/models/knowledge.py`（表结构口径，只读）
- `backend/app/modules/admin/services/knowledge_service.py`、`knowledge_settlement.py`、`knowledge_like_quota.py`（业务规则，只读）
- `automation/src/clients/mysql_client.py`、`src/fixtures/client_fixtures.py`（客户端与 fixture 模式）

## 用户拍板的决策

1. 归属 `tests/admin/`（服务代码在 admin 模块，纠正设计稿的 tests/tasks 笔误）
2. 12 条用例（13 条砍连接冒烟 1 条；主键冲突用例升级为"冲突 + UPDATE 契约"双断言）
3. 独立测试库 `helpdesk_test`，环境变量 `AUTOMATION_DB_NAME` 切换，未设置回落 config.yaml
4. CI 本期加 `-m db` 独立 job，先非阻塞（continue-on-failure），稳定后升级 blocking

## 修改文件列表

- `automation/tests/conftest.py`：新增 `knowledge_db` fixture（独立库切换 / 缺表 skip / STRICT_TRANS_TABLES 自检 / AUTO_T_ 前缀跟踪清理）、`KnowledgeDbSession`、`KNOWLEDGE_DB_TABLES`
- `automation/tests/admin/test_knowledge_db.py`：新增 12 条 DB 集成用例（5 个 class）
- `automation/docs/gap-analysis-db-integration.md`：新增（缺口分析）
- `automation/docs/testing/analysis/analysis-knowledge-db.md`：新增（模块分析）
- `automation/docs/testing/scenarios/knowledge-db-design.md`：新增（设计 + 确认结果）
- `automation/docs/testing/case-inventory-admin.md`：刷新（45 条 API 用例口径不变；db 用例不入 API 清单，与 Allure 报告策略一致）

未改动 `src/`、mock、pyproject（`db` marker 已存在）、业务模块。

## 测试结果

```
pytest tests/admin/test_knowledge_db.py -m db -v   → 12 skipped（本机无 MySQL，skip 机制按设计生效）
pytest src/ config/ -q                             → 320 passed（框架库无回归）
pytest tests/admin/ -m api -q                      → 45 passed, 12 deselected（P0 层无回归）
```

**已知限制**：本机无 MySQL/Docker，sit/uat 库为内网域名不可达，12 条用例的真实执行验证依赖 CI 的 MySQL service container job（待办第 1 项）。SQL 已按 `backend/app/models/knowledge.py` 的 DDL 口径编写并经 py_compile + 双 lane 回归。

## 风险

- CI job 未建前，`-m db` 用例在现有流水线中不可见（不阻塞任何 lane）
- 独立测试库尚未创建（`CREATE DATABASE helpdesk_test` + `alembic upgrade head`），需在有 MySQL 的环境执行一次
- 结算 DDL 默认值（revision=1/status=reviewing）以 Alembic 实际迁移为准，若与模型 default 不一致将在 CI 首跑暴露

## 下一步计划

1. **CI job**：`.github/workflows/db-tests.yml`——mysql:8 service container + `alembic upgrade head` + `pytest -m db`，先 continue-on-failure（注意：workflow 在仓库根 `.github/`，本次未建，避免与 DB 库创建决策耦合）
2. tasks 模块 DB 测试（状态机落库 / 派单日志 / 代理关系约束），复用 `knowledge_db` 模式改名为通用 `db_session`
3. call 模块 DB 测试（conversation→message→ticket ID 链路）
4. Alembic schema 一致性检查纳入 CI
