# 项目架构说明

> 本文是对 OpenRobotService 项目**整体架构**的描述，覆盖后端、前端、AI 三个子系统的结构、技术栈与测试布局。
> 产品形态见 `docs/PRODUCT/PRODUCT.md`，技术设计蓝图见 `docs/PRODUCT/ARCHITECTURE.md`，后端代码现状见 `backend/CODEBASE_OVERVIEW.md`，集成设计见 `backend/INTEGRATION_DESIGN.md`。
> 本文聚焦**测试视角**的架构理解。

---

## 一、项目概览

OpenRobotService（公共实例 **「摇人吧」**）是一个面向工业移动机器人（AGV/AMR）行业的微信服务号平台，覆盖报障、工单流转、项目交付管理全流程，AI 沿"需求/供给/管理"三视角深度参与。

### 核心业务流程

```
用户(任意角色) ──微信服务号/H5──> 咨询 / 提交工单
                                     │
                                     ▼
                             工单进入系统，自动/手动派单
                                     │
            ┌────────────────────────┼────────────────────────┐
            ▼                        ▼                        ▼
      转发给处理人员           工单内多方讨论            上报上级领导
      (微信模板消息通知)        (评论时间线留痕)         (升级 escalation)
            │                        │                        │
            └────────────────────────┴────────────────────────┘
                                     ▼
                              工单关闭 / 交付完成
```

---

## 二、子系统结构

### 2.1 后端（`backend/`）— Python FastAPI

| 目录 | 职责 | 测试文件 |
|------|------|----------|
| `app/core/` | 配置、数据库、安全、认证、中间件 | — |
| `app/models/` | SQLAlchemy ORM 模型（统一单一 Base） | — |
| `app/schemas/` | Pydantic 请求/响应模型 | — |
| `app/services/` | 跨模块共享服务（用户/权限/日志） | — |
| `app/modules/call/` | 「我要摇人」模块（AI 问答/会话/工单） | — |
| `app/modules/tasks/` | 「系统任务」模块（工单 CRUD/状态机/派单） | `tests/tasks/` |
| `app/modules/admin/` | 「后台管理」模块（项目/风险/日报/权限） | — |
| `app/integrations/` | 外部任务源集成（禅道适配器插件化） | `tests/integrations/` |
| `app/wechat/` | 微信服务号外壳（OAuth/菜单/消息/通知） | — |
| `app/utils/` | 工具类（MinIO/MQTT/图片处理） | — |
| `tests/` | pytest 测试套件 | — |

### 2.2 前端（`frontend/`）— React + TypeScript + Vite

| 目录 | 职责 | 测试文件 |
|------|------|----------|
| `src/api/` | API 客户端封装 | `__tests__/client.test.ts` |
| `src/config/` | 配置（API 地址/微信参数） | `__tests__/api.test.ts` |
| `src/pages/` | 页面组件（call/tasks/admin） | `__tests__/` |
| `src/router/` | 路由配置 | `__tests__/router.test.tsx` |
| `src/shared/` | 共享组件/常量/工具 | `**/__tests__/` |
| `src/stores/` | Zustand 状态管理 | `__tests__/` |
| `src/test/` | Vitest 全局配置 | `setup.ts` |

### 2.3 AI 模块（`ai/`）— Python 独立服务

| 目录 | 职责 | 测试文件 |
|------|------|----------|
| `agents/` | 三视角 Agent（诊断/数据分析/派单） | — |
| `core/` | LLM/Embedding/Retrieval/Memory | — |
| `kb/` | Qdrant 向量知识库 | — |
| `ingestion/` | 知识摄取流水线 | — |
| `api/` | AI 服务 API 路由 | — |
| `tests/` | 测试脚本 | `test_llm_api.py` 等 |

---

## 三、技术栈

| 层次 | 技术 | 测试框架 |
|------|------|----------|
| 后端框架 | FastAPI 0.111+ / Uvicorn | pytest 9.1+ |
| 后端 ORM | SQLAlchemy 2.0 + Alembic | — |
| 后端数据库 | MySQL 8.0+（PyMySQL/asyncmy） | — |
| 后端缓存/队列 | Redis + Celery 5.6 | — |
| 后端对象存储 | MinIO + 阿里云 OSS | — |
| 前端框架 | React 19 + TypeScript 5.9 | Vitest 3.2+ |
| 前端测试库 | @testing-library/react 16 + jsdom 26 | Vitest |
| 前端 UI | TDesign Mobile React 0.23 | — |
| 前端状态管理 | Zustand 5 | — |
| AI 向量库 | Qdrant | — |
| AI 嵌入模型 | BAAI/bge-small-zh-v1.5 | — |
| AI 大模型 | DeepSeek / Qwen / GLM（OpenAI 兼容接口） | — |
| 测试报告 | Allure 2.33+ / pytest-html | allure-pytest 2.13+ |

---

## 四、测试目录布局

```
OpenRobotService/
├── backend/
│   ├── tests/                          # pytest 测试根目录
│   │   ├── conftest.py                 # 全局夹具（mock DB 模块）
│   │   ├── test_init.py                # 占位测试
│   │   ├── integrations/               # 外部集成测试
│   │   │   ├── test_adapter.py         # 禅道适配器测试
│   │   │   ├── test_engine.py          # 同步引擎测试
│   │   │   └── test_mapper.py          # 字段映射测试
│   │   └── tasks/                      # 系统任务测试
│   │       ├── test_standard_task_creation_api.py
│   │       └── test_standard_task_creation_db.py
│   └── pytest.ini                      # pytest 配置（可选）
├── frontend/
│   └── src/
│       ├── test/
│       │   └── setup.ts                # Vitest 全局配置
│       ├── api/__tests__/
│       ├── config/__tests__/
│       ├── pages/__tests__/
│       ├── router/__tests__/
│       ├── shared/components/__tests__/
│       ├── shared/constants/__tests__/
│       ├── shared/utils/__tests__/
│       └── stores/__tests__/
└── ai/
    └── tests/                          # AI 模块测试脚本
        ├── test_llm_api.py
        ├── agent_chat.py
        ├── llm_chat.py
        ├── streamlit_app.py
        └── ttft_benchmark.py
```

---

## 五、关键架构决策（测试相关）

### 5.1 后端 DB Mock 策略

`backend/tests/conftest.py` 使用占位模块对象替代真实的 `app`、`app.models`、`app.core` 包，阻止 import 时触发 MySQL 建表（`Base.metadata.create_all`）：

```python
# conftest.py（核心逻辑）
for _name, _sub in (("app", None), ("app.models", "models"), ("app.core", "core")):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        _m.__path__ = [os.path.join(_APP, _sub)] if _sub else [_APP]
        sys.modules[_name] = _m
```

同时用存根 `get_async_db` 替代真实数据库依赖。

### 5.2 前端测试环境

Vitest 使用 jsdom 模拟浏览器环境，无需真实 DOM 或浏览器：

- `globals: true` — 全局 `describe`/`it`/`expect`
- `environment: 'jsdom'` — 浏览器环境模拟
- `setupFiles: ['./src/test/setup.ts']` — 自动加载 `localStorage`/`matchMedia` mock

### 5.3 测试隔离

- 后端单元测试不依赖外部服务（DB/MinIO/微信）
- 集成测试通过 `skipif` 条件执行（`TEST_DATABASE_URL` 未设置时跳过）
- AI 模块测试需要真实 LLM API key 和网络连接

---

## 六、相关文档

| 文档 | 路径 |
|------|------|
| 后端代码结构总览 | `backend/CODEBASE_OVERVIEW.md` |
| 外部任务源集成设计 | `backend/INTEGRATION_DESIGN.md` |
| 产品需求文档 | `docs/PRD.md` |
| 技术架构蓝图 | `docs/PRODUCT/ARCHITECTURE.md` |
| 产品形态设计 | `docs/PRODUCT/PRODUCT.md` |
| 本地部署指南 | `docs/PRODUCT/SETUP.md` |
| 微信服务号配置 | `docs/PRODUCT/WECHAT.md` |
| 测试开发规范 | `automation/docs/testing/testing_guidelines.md` |
| 自动化测试方案 | `automation/docs/automation_strategy.md` |
