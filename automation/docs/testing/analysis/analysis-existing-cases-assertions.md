# 分析：存量用例弱断言与硬编码凭据（第一批改善）

- **日期**：2026-11-16
- **任务**：P0-1 补齐弱断言 + P0-2 收敛硬编码凭据
- **范围**：`automation/tests/call|tasks|admin|auth/test_*_code.py` + `tests/business_chain/test_call_to_ticket_close.py`
- **方法**：静态逐行扫描 + 精读 Mock 后端返回结构，只分析不改码

---

## 1. 功能点

| 项 | 说明 | 优先级 |
|---|---|---|
| 成功路径字段断言 | 200 响应应校验关键业务字段，避免"通了就算过" | P0 |
| 凭据单一来源 | 测试账号从统一配置读取，不散落在用例文件 | P0 |
| 异常路径断言 | 404/422/401/400 的状态码本身即完整断言，不在本次范围 | — |

---

## 2. 现状与精确数据

### 2.1 弱断言分布（精确口径）

初版报告估算"31 处"，本次逐行精扫（识别多行调用、排除异常路径与 204 无内容响应）后，**成功路径（200）无字段断言共 29 处**：

| 文件 | 处数 | 典型端点 |
|---|---|---|
| `call/test_call_code.py` | 9 | 会话列表、QA 流式、发消息、我的任务列表/创建、催办、可指派用户 ×3 |
| `tasks/test_tasks_code.py` | 7 | 任务列表 ×3、筛选、统计、AI 派单、全链路首步 |
| `admin/test_admin_code.py` | 11 | 工单列表/统计 ×2、项目列表、风险列表、看板、用户列表 ×2、角色列表 ×2、资源列表 |
| `auth/test_auth_code.py` | 2 | 微信标签、微信回调 |

> 说明：`expected_status=404/422/401/403/400` 的用例（约 50 处）状态码即预期结果，属合理断言，不改；`204 No Content` 无响应体，不适用字段断言。

### 2.2 Mock 返回结构与可断言字段

精读 `src/mocks/backend_mock.py` 后，按响应类型分类：

| 响应类型 | 端点示例 | 返回结构 | 能否用 `expected_fields` |
|---|---|---|---|
| dict + 固定键 | `/api/tasks/stats/overview` | `{total, by_status, by_priority}` | ✅ 可直接断言 |
| dict + 固定键 | `/api/tasks/1/ai-assign` | `{task_id, assigned_to, method, confidence}` | ✅ |
| dict + 固定键 | `/api/tasks/cuiban-notification` | `{success, task_id, message}` | ✅ |
| dict + 固定键 | `/api/tasks/filter` | `{items, total}` | ✅ 可断言 `total` |
| 分页 dict | `/api/tasks`、`/api/call/my-tasks/` | `{items, total, page, size, pages}` | ✅ 可断言 `page`/`size` |
| **list（顶层数组）** | `/api/tasks/assignable-users` | `[{id,name,role}, ...]` | ❌ **不能**，`assert_dict_contains_subset` 只接受 dict |
| **list（顶层数组）** | `/api/admin/*` 部分列表（需逐个确认） | 数组 | ❌ 需另行处理 |
| SSE 流式 | `/api/call/qa/ask/stream` | `StreamingResponse` | ❌ 特殊响应，不宜强断字段 |

**关键约束**：`expected_fields` 只支持 dict 子集递归断言。对 list 响应若强行传入会在 helper 内 `assert_contains` 对 list 取 key 时失败。因此 list 类弱断言需评估是"补断言但换方式"还是"维持状态码断言 + 注释说明"。

### 2.3 硬编码凭据分布

| 文件 | 位置 | 内容 |
|---|---|---|
| `call/test_call_code.py` | L11 | `_ROLE_CREDENTIALS = {'customer': ('customer','cust123'), 'engineer': ('engineer','eng123')}` |
| `tasks/test_tasks_code.py` | L10 | 同上 |
| `admin/test_admin_code.py` | L11 | 同上 |
| `auth/test_auth_code.py` | L35/52/61/70 | `'testadmin'/'admin123'`、`'engineer'/'eng123'`、`'customer'/'cust123'` 内联 |
| `business_chain/test_call_to_ticket_close.py` | L14-15 | `U1=("u1_auto","123456")`、`U2=("u2_auto","123456")` |
| `tests/conftest.py` | L143 | `mock_auth_token` fixture 内联 `testadmin/admin123` |

现有配置机制：`config/{local,sit,uat}/config.yaml` + `load_config()`，但 **config 没有 credentials 段**，且这些账号是 Mock 后端的固定内置用户（`AGENTS.md` 默认用户表：testadmin/admin123、engineer/eng123、customer/cust123），conftest 的 Mock 也写死同一份。

**矛盾点**：凭据既在 Mock 后端内置写死，又在测试用例写死——单纯把测试侧改成环境变量，若环境变量改了值而 Mock 未同步，用例会挂。因此收敛方案必须与 Mock 用户表联动（详见设计文档）。

---

## 3. 业务流程 / 状态流转 / 权限 / 接口

本次为**存量用例质量改善**，不引入新业务场景，故业务 7 要素分析简化如下：

- **接口列表**：见 2.2 表格，均为已有端点的断言补强，不新增接口调用。
- **权限控制**：硬编码凭据涉及 customer/engineer/admin 三角色，收敛后须保持角色语义不变。
- **状态流转**：全链路用例 `test_full_flow_create_to_closed` 首步补字段断言，不改变后续步骤。
- **边界条件**：list 响应、SSE 流式、204 无内容三类不适用 `expected_fields`，需特殊处理。

---

## 4. 风险点

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| 补字段断言后 mock 返回字段不符导致用例失败 | 中 | 已逐一核对 mock 返回结构；改后本地跑 mock 全量验证 |
| list 响应误用 `expected_fields` 导致 helper 报错 | 高 | 设计中明确排除 list 类，改用 `expected_fields=None` + 显式注释或扩展 helper |
| 凭据收敛后与 Mock 内置用户不一致 | 高 | 环境变量默认值必须与 `AGENTS.md` 默认用户表一致；Mock 侧保持不动 |
| 改动文件超过 10 个触发规范停止条件 | 低 | 本批 6 个测试文件，未超阈值 |
| 真实后端（USE_MOCK=0）契约与 mock 返回字段差异 | 中 | 补的字段选各端点稳定契约字段；workflow_dispatch 预验证 |

---

## 5. 边界条件

- `assignable-users` 返回顶层 list：不补 `expected_fields`，保持状态码断言，加注释说明原因。
- SSE 流式 `/qa/ask/stream`：不补字段断言（流式体不能简单 json 解析）。
- 微信系接口：mock 返回结构需单独确认后再决定是否补字段。
- 空列表场景（如 `assignable_users_empty`）：mock 仍返回固定 2 人列表，需先确认用例断言逻辑是否与名称一致。

---

## 6. 建议方案与优先级

1. **P0-A**：对 dict 类 200 响应补 `expected_fields`（约 15-20 处），字段取自 mock 稳定契约。
2. **P0-B**：list/SSE/204 类保持状态码断言，加清晰注释（约 8-10 处），避免"看似遗漏"。
3. **P0-C**：凭据收敛到统一入口（环境变量 + 默认值），与 Mock 用户表对齐，改 6 个文件。
4. **后续**：list 响应若需强断言，另立任务扩展 `_api` helper 支持 `expected_list`，不混入本批。

**分析完成，停止，等待人工确认。**
