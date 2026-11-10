# 设计：knowledge 模块数据库集成测试（task-54）

> **任务类型**：第三阶段设计（AGENTS.md：设计阶段禁止写代码，须确认后实现）
> **前置**：`automation/docs/testing/analysis/analysis-knowledge-db.md`（已确认）
> **状态**：已确认并实现（2026-11-10）

## 0. 确认结果（2026-11-10，用户拍板）

1. **归属**：改放 `tests/admin/test_knowledge_db.py`（knowledge 服务的读写代码在 `backend/app/modules/admin/services/`，按业务模块归属后台管理；设计稿原写 tests/tasks/ 属于笔误，已纠正）
2. **用例数**：13 → **12 条**（砍掉连接冒烟一条，客户端能力已被 `src/clients/tests/test_db_clients.py` 覆盖；`test_settlement_month_pk_conflict_update_semantics` 同时验证冲突 + UPDATE revision+1 契约）
3. **测试库**：独立测试库（约定 `helpdesk_test`），环境变量 `AUTOMATION_DB_NAME` 切换；未设置时回落 config.yaml。sit/uat 域名本机不可达，真跑验证由 CI MySQL service container 承担
4. **CI**：本期新增 `-m db` 独立 job，先 `continue-on-failure` 非阻塞，观察 1-2 周转绿后升级 blocking

---

## 1. 涉及文件列表

| 文件 | 操作 | 说明 |
|---|---|---|
| `automation/tests/conftest.py` | 修改 | 新增 `db_session` fixture（MySQLClient，按用例清理） |
| `automation/pyproject.toml` | 修改 | 新增 `db` marker |
| `automation/tests/tasks/test_knowledge_db.py` | 新增 | 用例主体（knowledge 表归属任务模块：`task_id` 主键，闭环源头在 tasks） |
| `automation/docs/task-54-knowledge-db-tests.md` | 新增 | worklog |
| `automation/docs/testing/scenarios/knowledge-db-scenarios.md` | 新增 | 场景清单（由清单生成器刷新） |

不改 `src/`、不改 mock（集成测试直连真库）、不改业务模块。

## 2. 模块职责划分

- **fixture（conftest.py）**：读取 `AUTOMATION_ENV` 对应 `config.yaml` 的 `database:` 段构造 `MySQLClient`；提供 `db_cleanup` 夹具——每条用例结束后按 `AUTO_T_` 前缀 + 表清单删除测试数据（倒序删除事件表→业务表）
- **用例（test_knowledge_db.py）**：只做数据层断言（落库值/约束/事务），不调后端 HTTP 接口
- **跳过机制**：无 `TEST_DATABASE_URL`/配置缺失时 `pytest.mark.skip(reason="...")`，保证本地 Fast Lane 不受影响

## 3. 测试用例变更（新增测试函数清单，13 条）

覆盖类型：全部为 `数据库：` 前缀（8 类覆盖中数据库类的本模块落地）。

```python
class TestKnowledgePublicationDb:
    test_publication_insert_ok                    # 正常流程：发布落库，9 个 NOT NULL 字段齐全
    test_publication_missing_required_column      # 数据校验：缺 report_id → IntegrityError
    test_publication_longtext_content_roundtrip   # 边界：>64KB 内容 LONGTEXT 完整存取

class TestArchiveReviewDecisionDb:
    test_review_decision_unique_version_ok        # 状态流转：report1-v1 → report1-v2 成功
    test_review_decision_duplicate_version        # 数据库：同 report_id 重复 version → IntegrityError(uq_archive_review_version)

class TestKnowledgeLikeDb:
    test_like_composite_pk_upsert                 # 正常：同 (task,user) 切 active 复用行
    test_like_soft_delete_keeps_row               # 状态流转：取消赞 → active=false 行保留 + events 追加
    test_like_event_append_order                  # 数据库：事件表按 created_at 可排序、行数递增

class TestKnowledgeSettlementDb:
    test_settlement_create_reviewing_defaults     # 正常：默认 status=reviewing, revision=1
    test_settlement_json_roundtrip                # 数据库：rankings/audit JSON 写读深度相等
    test_settlement_month_pk_conflict             # 数据库：同 month 二次插入 → 主键冲突（应走 UPDATE 语义）

class TestKnowledgeDiscussionDb:
    test_discussion_append_only                   # 正常：追加写入，task_id/author_id 落库

class TestDbConnectivity:
    test_mysql_client_connect_and_cleanup         # 全链路：连接→写入→清理→断言无残留
```

## 4. Mock 变更

无。集成测试不经过 MockBackend。

## 5. 实现步骤（按顺序）

1. `pyproject.toml` 加 `db` marker
2. `tests/conftest.py` 加 `db_session`/`db_cleanup` fixture（含无配置时 skip 逻辑）
3. 新增 `tests/tasks/test_knowledge_db.py`（13 条用例，一次一个 class，逐步验证）
4. `pytest tests/tasks/test_knowledge_db.py -m db -v` 全绿
5. `pytest src/ config/` 回归确认不影响框架库
6. `pytest -m api` 抽样回归确认不影响 P0 层
7. 生成 Allure 报告验证 `db` 用例展示正常
8. `python scripts/cli-gen-case-inventory.py --module tasks` 刷新清单
9. 更新 worklog `task-54-knowledge-db-tests.md` + 本设计文档状态改"已实现"

## 6. 风险分析

| 风险 | 缓解 |
|---|---|
| local 环境库 `helpdesk_724` 若含真实数据，`AUTO_T_` 前缀防误删；清理语句限定 `task_id` 范围 | 高 |
| 复合主键/唯一约束依赖 MySQL 严格模式，测试库 sql_mode 须确认 | 用例前置连接自检 |
| `db` 用例拖慢 Fast Lane | marker 隔离，默认 lane 不含 `-m db`，CI 单独 job |
| 驱动差异（MySQLClient 为同步 pymysql，业务层为 asyncmy） | 只断言数据终态，不复刻业务层事务时序 |

## 7. 确认点（需要你拍板）

1. 用例归属 `tests/tasks/`（闭环源头在任务模块）还是新建 `tests/knowledge/`？设计按前者。
2. 13 条用例清单是否认可/增删？
3. 测试库指向：local 用 `helpdesk_724`（现配置）还是提供独立 `helpdesk_test`？若用现库，确认其中无不可清理的生产副本数据。
4. CI 是否本期加 `-m db` 独立 job（可与用例实现解耦，后补）。

*确认后进入第四阶段实现。*
