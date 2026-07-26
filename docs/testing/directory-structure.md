# 目录结构规范

> 本文定义三模块的 tests 目录结构标准。
> **原则**：不改变现有目录位置，所有新增测试按这里定义的规则放入对应目录。

---

## 一、后端 `backend/tests/`

### 1.1 目录层次

```
backend/tests/
├── __init__.py                      # 空文件，标记为包
├── conftest.py                      # 全局 fixture（模块替换 Mock）
├── {子模块}/
│   ├── __init__.py                  # 空文件
│   ├── conftest.py                  # 子模块专用 fixture（可选）
│   ├── test_{功能}.py               # 按功能划分的测试
│   └── data/
│       ├── sample_{数据说明}.json    # 测试数据文件（可选）
│       └── sample_{数据说明}.py     # Python 测试数据常量（可选）
└── utils/
    └── {工具函数}.py                # 测试用工具函数（可选）
```

### 1.2 当前实际结构

```
backend/tests/
├── __init__.py
├── conftest.py                      # ★ 全局 Mock（阻止 MySQL 连接）
├── test_init.py                     # 占位测试（可删除）
├── integrations/
│   ├── __init__.py
│   └── test_adapter.py              # 禅道适配器
│       test_engine.py               # 同步引擎
│       test_mapper.py               # 字段映射
└── tasks/
    ├── __init__.py
    ├── test_standard_task_creation_api.py   # API 集成测试
    └── test_standard_task_creation_db.py    # DB 集成测试（需 TEST_DATABASE_URL）
```

### 1.3 新增规则

| 测试类型 | 放入目录 | 示例 |
|----------|----------|------|
| 单元测试 | `tests/{子模块}/` | `tests/call/test_conversation_service.py` |
| API 集成测试 | `tests/{子模块}/` | `tests/call/test_conversation_api.py` |
| DB 集成测试 | `tests/{子模块}/` | `tests/admin/test_project_db.py` |
| 跨模块测试 | `tests/` 根目录 | `tests/test_workflow_integration.py` |

---

## 二、前端 `frontend/src/__tests__/`

### 2.1 目录层次

```
frontend/src/
├── test/
│   └── setup.ts                     # ★ 全局配置（已存在，勿移）
├── {模块}/
│   ├── {组件/文件}.{ts,tsx}        # 源码
│   └── __tests__/
│       ├── {组件/文件}.test.tsx      # 组件测试
│       └── {纯逻辑文件}.test.ts      # 纯逻辑测试
```

### 2.2 当前实际结构

```
frontend/src/
├── test/
│   └── setup.ts                     # vitest 全局 mock（localStorage/matchMedia）
├── api/__tests__/
│   └── client.test.ts
├── config/__tests__/
│   └── api.test.ts
├── pages/__tests__/
│   ├── AdminView.test.tsx
│   ├── Login.test.tsx
│   └── NoPermission.test.tsx
├── router/__tests__/
│   └── router.test.tsx
├── shared/components/__tests__/
│   ├── AdminLayout.test.tsx
│   ├── MainLayout.test.tsx
│   ├── Pagination.test.tsx
│   └── SafeHtml.test.tsx
├── shared/constants/__tests__/
│   └── ticket.test.ts
├── shared/utils/__tests__/
│   ├── authGuard.test.tsx
│   └── url.test.ts
└── stores/__tests__/
    ├── auth.test.ts
    └── workbench.test.ts
```

### 2.3 新增规则

| 测试类型 | 放入目录 | 示例 |
|----------|----------|------|
| 组件测试 | `{模块}/__tests__/` | `pages/call/__tests__/CallView.test.tsx` |
| Store 测试 | `stores/__tests__/` | `stores/__tests__/workbench.test.ts` |
| Utils 测试 | `shared/utils/__tests__/` | `shared/utils/__tests__/list.test.ts` |
| API 客户端测试 | `api/__tests__/` | `api/__tests__/ai.test.ts` |

---

## 三、AI 模块 `ai/tests/`

### 3.1 当前结构

```
ai/tests/
├── test_llm_api.py                  # LLM API 连通性测试（正式测试）
├── agent_chat.py                    # Agent 交互诊断脚本（非正式）
├── llm_chat.py                      # LLM 流式调用脚本（非正式）
├── streamlit_app.py                 # Streamlit 测试前端（非正式）
└── ttft_benchmark.py                # 首 Token 时间基准测试（非正式）
```

### 3.2 目标结构

```
ai/tests/
├── __init__.py                      # 空文件
├── conftest.py                      # AI 模块 fixture（Mock LLM API）
├── test_llm_api.py                  # LLM API 连通性测试（保留）
├── test_{功能}.py                   # 新增 pytest 测试
├── {子模块}/
│   ├── __init__.py
│   └── test_{功能}.py
└── scripts/                         # 非正式的交互/基准脚本
    ├── agent_chat.py                （保留，不移除）
    ├── llm_chat.py
    ├── streamlit_app.py
    └── ttft_benchmark.py
```

### 3.3 新增规则

| 测试类型 | 放入目录 | 示例 |
|----------|----------|------|
| 单元测试 | `tests/` 或 `tests/{子模块}/` | `tests/test_embed.py` |
| 交互脚本 | `tests/scripts/` | `tests/scripts/agent_chat.py` |
| 基准测试 | `tests/scripts/` | `tests/scripts/ttft_benchmark.py` |

---

## 四、约定

1. **`__init__.py`**：每个测试子目录必须有空 `__init__.py`，确保 pytest 可发现
2. **`conftest.py`**：全局 fixture 放 `tests/conftest.py`，子模块专用 fixture 放子模块内
3. **`data/` 目录**：仅在测试数据量大或需要文件输入时创建，优先使用 inline 数据
4. **`utils/` 目录**：仅在工具函数跨文件复用时创建，优先使用 `conftest.py` 的 fixture
