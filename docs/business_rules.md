# 业务规则

> 本文记录 OpenRobotService 项目的核心业务规则、约束和决策，供测试开发和代码编写时参考。
> 与代码相关的业务逻辑见 `backend/CODEBASE_OVERVIEW.md`，产品需求见 `docs/PRD.md`。

---

## 一、角色与权限

### 1.1 角色定义

| 角色 | 标识 | 说明 |
|------|------|------|
| 客户 | `customer` | 现场人员，可提交工单、咨询、查看自己提交的工单 |
| 工程师 | `engineer` | 实施工程师，可接单处理、转派、讨论、上报 |
| 项目经理 | `manager` | 可派单、处理、讨论、查看本项目工单 |
| 上级领导 | `leader` | 可接收上报、决策、查看全局工单 |
| 系统管理员 | `admin` | 全部权限、用户与项目管理 |

### 1.2 权限规则

- 提交工单、参与讨论是所有角色的通用能力
- 权限码格式：`backend:{resource}:{scope}:{action}`
- `require_permission(perm, project_id)` 支持 admin 直通、`resource:*` 通配
- 开发环境可用 `/api/auth/dev-login` 跳过微信 OAuth 获取 JWT
- `frontend/.env.local` 的 `VITE_DISABLE_AUTH_GUARD=true` 可跳过前端守卫

---

## 二、任务状态机

### 2.1 状态流转

```
待派单(pending_assignment) → 已派单(assigned) → 处理中(in_progress)
                                                        │
              ┌───────────────────┬──────────────────────┼──────────────────────┐
              ▼                   ▼                      ▼                      ▼
         待讨论(discussing)   已上报(escalated)      已解决(resolved)       已关闭(closed)
                                                                              │
                                                                              ▼
                                                                        处理中(in_progress) ← 重开
```

### 2.2 状态合并规则（外部任务源同步）

当禅道等外部源同步任务时，状态取较后：

| 本平台状态 | 序号 | 外部来源 |
|------------|------|----------|
| `new` | 0 | `wait` |
| `in_progress` | 1 | `doing` |
| `pending` | 1（同级） | `pause` |
| `resolved` | 2 | `done` |
| `closed` | 3 | `cancel` / `closed` |

合并规则定义在 `app/integrations/engine.py` 的 `merge_status()`：
- incoming 序号 ≤ local 序号 → 不动（返回 None）
- incoming 序号 > local 序号 → 推进（返回 incoming）

### 2.3 工单任务类型映射（禅道 → 核心）

| 禅道 type | 核心 TaskType |
|-----------|---------------|
| `devel` | `feature` |
| `test` | `support` |
| `design` / `research` / `misc` | `other` |
| 空 / `None` | `other` |

### 2.4 任务优先级映射

| 禅道 pri | 核心 Priority |
|----------|---------------|
| 1 | `urgent` |
| 2 | `high` |
| 3 | `medium` |
| 4 | `low` |
| 空 / 非数字 | `medium` |

---

## 三、外部任务源集成

### 3.1 插件化设计

- 每个任务源是一个独立包，位于 `app/integrations/sources/` 下
- 通过 `TASK_SOURCES_ENABLED` 配置启用（如 `["zentao"]`）
- 插件实现 `TaskSourceAdapter` 接口，仅负责"拉取 + 翻译成 ExternalTask"
- 状态合并、账号映射、入库由 `SyncEngine` 统一处理
- 移除一个源：删除对应目录 + 从配置中去掉 → 核心零改动

### 3.2 同步规则

- 方向：单向只读（外部 → 本平台），不回写
- 触发：Airflow DAG 定时触发 `POST /api/tasks/sources/{name}/sync`
- 鉴权：独立 API Key（`X-API-Key`），与用户 JWT 分离
- 幂等：按 `(source, external_id)` upsert，可安全重跑
- 容错：单条失败不中断整批

---

## 四、测试隔离规则

### 4.1 数据库隔离

- **单元测试**：conftest.py 使用占位模块阻止真实 DB 连接，不需要 MySQL
- **集成测试**（`test_standard_task_creation_db.py`）：通过 `TEST_DATABASE_URL` 环境变量连接独立 MySQL，无此变量时自动 skip
- 生产 DB 连接串由 `settings.DB_CONFIG` 注入，测试环境不读取

### 4.2 外部服务隔离

- 微信功能测试：可用 `/api/auth/dev-login` 降级模式，无需真实微信环境
- MinIO 测试：不依赖真实对象存储
- AI 模块测试：需要真实 LLM API key 和网络连接（`ai/tests/test_llm_api.py`）

### 4.3 外部接口契约

- 后端 API 路由以 `/api/` 为前缀
- AI 服务路由以 `/api/ai/` 为前缀
- 微信回调路由以 `/api/wechat/` 为前缀
- 前端 `/api/*` 经 vite 代理或 nginx 分发：`/api/ai/*` → AI 服务，其余 → 业务后端

---

## 五、配置规则

### 5.1 环境变量

| 变量 | 用途 | 测试影响 |
|------|------|----------|
| `DATABASE_URL` | MySQL 连接串 | 不设置时单元测试也可运行 |
| `TEST_DATABASE_URL` | 测试用 MySQL 连接串 | 设置后集成测试才会执行 |
| `TASK_SOURCES_ENABLED` | 启用的任务源 | 影响集成注册行为 |
| `WECHAT_*` | 微信配置 | 缺失时功能降级为日志 |
| `MEILI_ENABLED` | Meilisearch 全文检索开关 | 可降级为 ilike |

### 5.2 前端构建配置

| 构建命令 | base | API 前缀 | 用途 |
|----------|------|----------|------|
| `npm run build` | `/` | `/api` | 默认 |
| `npm run build:test` | `/t/app/` | `/t/api` | 测试环境 |
| `npm run build:prod` | `/p/app/` | `/p/api` | 生产环境 |

---

## 六、相关文档

| 文档 | 路径 |
|------|------|
| 产品需求文档（PRD） | `docs/PRD.md` |
| 架构设计蓝图 | `docs/PRODUCT/ARCHITECTURE.md` |
| 外部任务源集成设计 | `backend/INTEGRATION_DESIGN.md` |
| 后端代码结构总览 | `backend/CODEBASE_OVERVIEW.md` |
| 测试开发规范 | `docs/testing_guidelines.md` |
| 自动化测试方案 | `docs/automation_strategy.md` |
