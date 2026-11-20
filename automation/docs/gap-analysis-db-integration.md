# 缺口分析：P1 数据库集成测试（2026-11-10）

> **任务类型**：第二阶段分析（AGENTS.md 工作流）
> **作者**：自动化测试专家 Agent
> **状态**：待人工确认
> **范围**：`automation/` 下新增真实数据库集成测试，不改 `ai/` `backend/` `frontend/` 业务代码

---

## 1. 当前实现与现状

### 1.1 已有资产

| 资产 | 位置 | 说明 |
|---|---|---|
| MySQL/Redis/Qdrant 客户端 | `automation/src/clients/{mysql,redis,qdrant}_client.py` | 封装完整，支持重试/上下文管理 |
| 客户端单测 | `automation/src/clients/tests/test_db_clients.py` | 只测客户端自身，不测业务表 |
| 环境隔离配置 | `automation/config/{local,sit,uat}/config.yaml` | 含 `database:` 段（local 指向 `helpdesk_724`） |
| 开发者侧 DB 测试 | `backend/tests/tasks/test_standard_task_*_db.py` | 业务模块自有口径，`TEST_DATABASE_URL` 驱动，**不属于本平台范围**，但可参考其 fixture 模式 |
| API 层 142 条用例 | `automation/tests/{call,tasks,admin,auth,ai}/` | P0 层，mock 驱动，已完成 |

### 1.2 缺口

1. **`automation/tests/` 无一条真实库集成用例**——P1 层（Database）实际覆盖为 0
2. Mock 后端是"本地近似实现"（AGENTS.md 已声明边界：真契约以真实后端为准），数据层正确性（约束/事务/迁移）完全无验证
3. **知识生产闭环**（阶段 3 护城河）涉及 6 张表 + 状态机 + 唯一约束，无任何数据层测试
4. 旧 `tickets` 与新 `tasks` 双模型并存（PRD 14.2 技术债），一致性无回归保障
5. Alembic 迁移与模型同步曾出 P0（`docs/automation/docs/bug-p0-alembic-empty-db.md`），未纳入 CI 门禁

## 2. 与目标的差距（AGENTS.md 优先级）

| 优先级 | 目标 | 现状 | 差距 |
|---|---|---|---|
| P1 | Database：MySQL/Redis/Qdrant 集成测试 | 0 条 | 全量缺口，本次任务 |
| P2 | AI Agent 接口测试 | 4 条 | 后续任务，本次不动 |
| P3 | UI/E2E | 10 条冒烟 | 后续任务，本次不动 |

## 3. 覆盖范围（本次 P1 规划）

### T0（本次先做，见 analysis-knowledge-db.md）
- **knowledge 模块**（6 张表）：知识生产闭环地基，表少、语义清晰、直接服务阶段 3
- **tasks 模块核心表**（第二批）：状态机落库、派单日志、代理关系约束
- **call 模块核心表**（第三批）：会话→消息→转工单 ID 链路

### T1（后续任务）
- identity/organization（RBAC 落库）、notification/wechat_activity（埋点）
- Redis 场景（命中/未命中/过期/雪崩/点赞限流）
- Qdrant（入库→检索命中→删除不命中）
- Alembic schema 一致性检查纳入 CI

## 4. 执行约束

- 数据库连接只走 `automation/config/` 环境配置，**绝不连接生产库**
- 用例幂等：fixture 负责"建→验→清"，测试数据带 `AUTO_T_` 前缀便于清理
- 覆盖类型 docstring 按规范（`数据库：` 前缀）
- 用例位置：`automation/tests/{module}/test_{module}_db.py`，走 `MySQLClient`
- 新增 marker `db` 于 `automation/pyproject.toml`（CI 可独立门禁）

## 5. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| 测试库不可用导致 CI 挂 | 中 | `db` marker 独立，CI 中可按需跳过/单独 job |
| 测试数据污染共享库 | 高 | 强制 fixture 清理 + 前缀约定 + 事务回滚兜底 |
| 业务表结构变更导致用例过期 | 低 | 用例断言以 `backend/app/models/` 为准并注明来源版本 |

## 6. 建议方案与优先级

**本次任务范围（待确认）**：仅 knowledge 模块 DB 集成测试，产出
1. `automation/tests/admin/test_knowledge_db.py`（或按模块归属放 `tests/tasks/`，见设计文档）
2. 新增 `db` marker + fixture（`automation/tests/conftest.py`）
3. Allure 报告纳入现有体系
4. worklog：`automation/docs/task-54-knowledge-db-tests.md`

tasks/call 两批在 knowledge 验证模式跑通后按同一模板复制推进。
