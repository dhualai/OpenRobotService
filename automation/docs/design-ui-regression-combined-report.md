# UI 业务链路与单接口 Smoke 联合 Allure 报告设计

> 状态：已确认并实现
> 日期：2026-09-19
> 范围：本地自动化回归，真实测试环境
> 约束：本阶段只做设计，不修改测试代码

## 1. 目标

在同一份 Allure HTML 中同时展示：

1. 一条完整的真实 UI 业务链路：
   `U1 提单 -> U2 处理并解决 -> U1 关闭`
2. 一组与这条链路直接相关的真实测试环境单接口 Smoke。

报告要让不翻代码的人直接看到：

- 业务链路是否通过。
- 链路中的中文操作、角色、状态流转和实际接口。
- 登录、摇人问答、系统任务三个模块的基础接口是否正常。

## 2. 已确认约束

- UI 业务链路继续使用真实测试环境。
- 单接口 Smoke 也使用同一真实测试环境，不使用 Mock 结果冒充真实环境。
- Smoke 只允许只读或幂等接口，不创建、修改、删除业务数据。
- U1/U2 凭据继续从环境变量读取，不写入仓库。
- 接口顺序和状态来自实际请求，不手工编造。
- 第一版只生成样板报告，不改 CI 触发，不接工单驱动。

## 3. 现有能力与本阶段缺口

### 3.1 已有能力

- `automation/tests/ui/test_call_qa_to_ticket_close_regression.py`
  已能真实完成 UI 业务链路。
- `automation/tests/ui/conftest.py`
  已提供测试后端隧道、自动化 AI 隧道和本地 Gateway。
- Allure 已能区分：
  - `场景用例 / UI完整业务链路`
  - `单接口用例 / <模块>`

### 3.2 本阶段缺口

- 单接口侧还没有与 UI 场景绑定到同一真实环境的 Smoke 文件。
- 现有 `tests/real/test_real_backend_smoke.py` 只有 health、admin 登录、工单创建，
  与本链路所需的 U1/U2、摇人问答、工单查询不完全匹配。
- 现有 Mock 代码驱动用例很多，但不适合作为“真实环境 Smoke”直接混入本报告。
- 还没有一条固定的联合执行命令生成两类用例。

## 4. 方案选择

### 方案 A：复用现有 Mock API 用例

优点：

- 改代码最少。
- 速度快、状态完全可控。

缺点：

- 报告中混有 Mock 与真实环境，容易让查看者误解。
- 不能证明测试环境接口当前可用。

结论：不采用。

### 方案 B：新增真实环境只读 Smoke

优点：

- 与 UI 场景使用同一测试环境。
- 无业务写入，风险低。
- 报告中的两类用例环境语义一致。

缺点：

- 需要新增一个 Smoke 测试文件和对应环境客户端夹具。
- 深度状态流转无法单独 Smoke，必须继续由 UI 场景覆盖。

结论：采用。

## 5. 第一版 Smoke 集合

Smoke 仅覆盖登录、摇人问答、系统任务，共 6 条：

| 序号 | 中文用例 | 角色 | 接口 | 断言 | 数据安全 |
|---|---|---|---|---|---|
| API-S01 | 测试后端健康检查 | 无 | `GET /api/health` | `200`，`status=healthy` | 只读 |
| API-S02 | U1 登录成功 | U1 | `POST /api/auth/login` | `200`，返回 `access_token` | 只读 |
| API-S03 | U1 获取当前用户 | U1 | `GET /api/auth/me` | `200`，`username=u1_auto` | 只读 |
| API-S04 | U1 查询摇人会话列表 | U1 | `GET /api/call/conversations?scene_type=chat&limit=10` | `200`，返回数组 | 只读 |
| API-S05 | U1 查询可访问工单 | U1 | `POST /api/tasks/filter` | `200`，返回 `items/total` 结构 | 只读查询 |
| API-S06 | U1 查询可指派人员 | U1 | `GET /api/tasks/assignable-users?skip=0&limit=10` | `200`，返回数组 | 只读 |

### 5.1 不放进 Smoke 的接口

| 接口 | 原因 | 当前覆盖方式 |
|---|---|---|
| `POST /api/call/conversations` | 会产生测试会话 | UI 场景 S02 |
| `POST /api/ai/qa/ask/stream` | 依赖 AI 时序，运行时间长 | UI 场景 S03 |
| `POST /api/ai/qa/ticket/confirm` | 会创建真实工单 | UI 场景 S05 |
| `POST /api/tasks/{id}/respond` | 修改真实工单状态 | UI 场景 S10/S12/S14 |
| `POST /api/tasks/{id}/complete-step` | 修改真实阶段 | UI 场景 S11/S13 |
| `PATCH /api/tasks/{id}/status` | 修改真实主状态 | UI 场景 S15/S17 |
| `DELETE /api/tasks/{id}` | 当前接口返回 500，且属于清理能力 | 清理告警，不作为 Smoke 通过条件 |

## 6. 联合报告结构

Allure 顶层分类固定为：

```text
场景用例
└── UI完整业务链路
    └── 测试环境：U1提单，U2处理并已解决，U1关闭

单接口用例
└── 真实测试环境 Smoke
    ├── 登录
    ├── 摇人问答
    └── 系统任务
```

数量预期：

- 场景用例：1 条，内部 16 个业务步骤。
- 单接口用例：6 条。
- 合计：7 条测试用例、1 份 Allure HTML。

## 7. 运行架构

```text
pytest 一次执行
  ├── 复用 UI 回归 session fixture
  │     ├── SSH 隧道 -> 测试后端 9400
  │     ├── SSH 隧道 -> 自动化 AI 9411
  │     └── 本地 Gateway
  ├── 执行 1 条 UI 场景
  └── 执行 6 条真实环境只读 API Smoke
        └── 使用 Gateway 的 backend_url

Allure
  └── 汇总场景用例 + 单接口用例
```

Smoke 使用同步 `httpx.Client` 或现有 `ApiClient`，请求地址复用 UI runtime 的
`backend_url`，不单独维护第二套环境地址。

## 8. 用例步骤与附件

每条 Smoke 使用中文 Allure 步骤：

```text
接口：POST /api/auth/login -> HTTP 200
```

每条 Smoke 附加三类证据：

1. `接口结果`：

- 请求方法。
- 请求路径。
- HTTP 状态码。
- 耗时。

2. `响应摘要`：

- 只保留安全的响应字段。
- 列表类接口记录响应类型和数量。
- 敏感字段不进入附件。

3. `断言结果`：

- 断言名称。
- 期望值。
- 实际值。
- 通过或失败。

UI 场景每个 S 步骤增加 `Sxx-断言与响应摘要`：

- 业务步骤执行结果。
- 每个接口的方法、路径、HTTP 状态和通过状态。
- 原有 `Sxx-网络请求` 继续保留脱敏后的请求和响应明细。

以下内容仍禁止附加：

- `Authorization`。
- `access_token`。
- Cookie。
- 完整用户隐私数据。

## 9. 实现文件计划

| 文件 | 变更 |
|---|---|
| `automation/tests/ui/test_real_safe_api_smoke.py` | 新增 6 条真实环境只读 Smoke |
| `automation/tests/ui/conftest.py` | 复用/补充 runtime 客户端夹具 |
| `automation/tests/conftest.py` | 将 Smoke 文件归入“单接口用例 / 真实测试环境 Smoke” |
| `automation/docs/testing/scenarios/ui-regression-smoke.md` | 新增 Smoke 场景设计 |
| `automation/docs/worklog/task-48-combined-allure-report.md` | 完成后记录验证结果 |

如果实现只需要复用现有 runtime，不调整环境启动逻辑，则不拆新的环境 fixture。

## 10. 实现步骤

1. 新增真实环境只读 Smoke 测试文件。
2. 复用 UI 回归 runtime 的 backend URL 和 U1 凭据。
3. 为 Smoke 增加中文 Allure 分类和步骤。
4. 更新 `tests/conftest.py` 的分类规则。
5. 用一次 pytest 同时运行 UI 场景和 6 条 Smoke。
6. 生成联合 Allure HTML。
7. 检查顶层分类、环境标识和敏感信息脱敏。
8. 连续执行两次，确认结果稳定。
9. 更新 worklog。

## 11. 验证命令

```powershell
UI_REGRESSION_E2E=1 `
... `
pytest `
  automation/tests/ui/test_call_qa_to_ticket_close_regression.py `
  automation/tests/ui/test_real_safe_api_smoke.py `
  -m "e2e or smoke" `
  --alluredir=automation/output/allure-results-ui-regression-combined

allure generate `
  automation/output/allure-results-ui-regression-combined `
  -o automation/output/allure-report-ui-regression-combined `
  --clean
```

## 12. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| Smoke 误写成真实环境写接口 | P0 | 只允许表 5 中的 6 个只读接口 |
| 敏感响应或超大响应进入报告 | P0 | 成功时只附响应摘要；敏感字段不进入附件 |
| 报告混淆 Mock 与真实环境 | P1 | 单接口分类明确标为“真实测试环境 Smoke” |
| 会话列表或工单列表为空 | P1 | 只断言响应结构和状态码，不依赖固定数据数量 |
| 深层阶段/状态接口未单测 | P1 | 由现有 UI 场景覆盖，不在 Smoke 中伪造 |
| 登录日志泄漏 token | P1 | 复用已有 URL 脱敏和 httpx 日志降级 |
| 场景失败导致 Smoke 也不运行 | P2 | 两类用例独立收集，报告中分别展示结果 |

## 13. 待人工确认

1. 是否接受第一版 Smoke 全部采用真实测试环境只读接口。
2. 是否接受深层阶段/状态接口只由 UI 场景覆盖，不单独做 Smoke。
3. 是否接受报告分类名“真实测试环境 Smoke”。
4. 是否接受先做 6 条 Smoke，不扩展到登录异常、权限和参数校验。

确认后再进入实现阶段。
