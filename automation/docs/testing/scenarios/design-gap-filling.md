# 设计：覆盖缺口补强方案（G1–G7）

- **日期**：2026-11-16
- **状态**：待确认，未写用例
- **依据**：`analysis/analysis-redundancy-and-gaps.md`（冗余与缺口复盘）
- **前提**：用户已解除"不新增用例"约束（2026-11-16 20:46）
- **边界**：仍只改 `automation/`；如需改 mock 或业务代码，单独标注并等待确认

---

## 一、缺口分类（按是否需要改 mock）

核查 `src/mocks/backend_mock.py` 后的关键结论：

| 类别 | 缺口 | mock 是否支持 | 能否纯补用例 |
|---|---|---|---|
| **A. 可直接补** | G3 mock 零覆盖接口（spec-doc/operation-logs/relations/respond/complete-step） | ✅ 实现完整 | ✅ 是 |
| **A. 可直接补** | G2 权限用例（tasks 模块 0 条） | ⚠️ 部分支持（见风险） | ✅ 是（但注意假绿） |
| **A. 可直接补** | G4 tasks 异常路径 | ✅ 支持 | ✅ 是 |
| **B. 需扩 mock** | G1 状态机 11 状态 | ❌ mock 仅 6 状态（`_ALLOWED`） | ❌ 需先扩 mock |
| **C. 需真实环境** | G5 Redis 深度/DB 深度 | ⚠️ 简单支持，深度场景需真实服务 | 部分 |
| **C. 需真实环境** | G6 UI 页面级 E2E | ❌ 需浏览器+测试环境 | 部分 |
| **C. 需 AI 服务** | G7 AI L2/L3 | ❌ 需真实 AI | 否（现有 skip） |

---

## 二、分批方案

### 第一批：A 类（纯补用例，不动 mock）—— ✅ 已完成（2026-11-17）

**实际交付 +18 条**（原估 23，因 mock 鉴权实测缺口下调）：

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `tests/tasks/test_tasks_documents_code.py` | 10 | G3 零覆盖接口全部补齐 |
| `tests/tasks/test_tasks_auth_code.py` | 4 | G2 无 token 拒绝（原估 6，assign 与 call 端点 mock 无鉴权故减 2） |
| `tests/tasks/test_tasks_negative_code.py` | 4 | G4 异常路径 |

**验证**：tasks 50 passed；全量 144 passed / 12 skipped（基线 126 + 18）。
**执行中规避的假绿**：`PATCH /api/tasks/{id}/assign` 与 call 全模块在 mock 内无 `_require_auth`，已排除，未写出必然假绿的用例。
详见 `docs/task-56-gap-filling-batch1.md`。

以下为原始设计记录（保留备查）。

目标：补回"mock 已实现却零覆盖"和"权限/异常空白"，全部在现有 mock 上可跑。

#### 1. G3 零覆盖接口（新增约 12 条）

放在 `tests/tasks/test_tasks_code.py` 新 class（按功能分组）：

| 功能 | 端点 | 用例（正常 + 异常/校验） |
|---|---|---|
| 规格文档 | GET/PUT `/api/tasks/{id}/spec-doc` | 获取默认文档、更新文档 revision 递增、更新缺 content→422 |
| 操作日志 | GET `/api/tasks/{id}/operation-logs` | 获取列表（空）、结构校验 |
| 工单关联 | GET/POST `/api/tasks/{id}/relations`、GET tree | 创建关联、缺 target→422、获取列表、关系树 |
| 响应工单 | POST `/api/tasks/{id}/respond` | new→in_progress、内容校验 |
| 完成步骤 | POST `/api/tasks/{id}/complete-step` | 未协商一致→400、已协商→成功 |

> 字段断言用 mock 稳定返回（revision/total/status/success）。

#### 2. G2 权限用例（新增约 6 条）

- `tests/tasks/test_tasks_code.py`：无 token 建单→401、customer 访问他人任务、engineer 越权删除→根据 mock 行为定
- `tests/call/test_call_code.py`：无 token 发消息/提问→401
- ⚠️ **假绿风险**：mock `_require_auth` 只验 token 存在、不验角色（除 admin 和 assign 转派）。
  **对策**：这批权限用例同时标注"仅 mock 车道的契约占位，角色语义由真实后端车道验收"，
  并在真实场景用例（test_ticket_scenarios）补 1-2 条角色越权，避免假绿。

#### 3. G4 tasks 异常路径（新增约 5 条）

- 更新不存在任务→404、删除不存在→404、指派不存在→404
- 创建非法字段类型→422、超长标题→422（若 mock 支持）

**第一批小计：约 23 条新增用例，全部 mock 可跑，本地 + CI quality-gate 验证。**

---

### 第二批：B 类（需先扩 mock）—— ⏸ 用户决定不扩 mock，暂缓（2026-11-19）

> **决策**：用户 2026-11-19 明确「G1 状态机先不要授权扩 mock」，故 G1 不补强，
> `_ALLOWED` 维持 6 状态现状。状态机流转由真实后端车道的场景用例
> （`tests/real/test_ticket_scenarios.py`：暂停/确认、无法解决/恢复、打回、撤回）
> 承担验收；mock 车道不再重复覆盖，也不写"mock 无法支持"的伪状态机用例。
> 后续若需 mock 侧状态机回归，需重新授权修改 `src/mocks/backend_mock.py`。

#### G1 状态机补强（原方案，未执行）

**问题**：真实后端 `TaskStatus` 有 11 个状态，mock `_ALLOWED` 只 6 个：

```
真实: new / in_progress / pending_requested / pending / unsolvable_requested
      / unsolvable / resolved / canceled / closed / archiving / archived
mock: new / pending / in_progress / resolved / closed / cancelled
```

**方案**（需确认后才做）：
1. 扩 `backend_mock.py` 的 `_ALLOWED`，补齐 5 个缺失状态及合法流转边
   （对齐 `backend/app/models/task.py` 的状态机 + 真实后端场景用例已验证的流转）
2. 新增约 10 条状态流转用例：每个终态不可流转、暂停请求/确认、无法解决/恢复、归档链路
3. 风险：mock 是"近似实现"，扩它可能引入与真实后端不一致的流转定义——**必须以真实后端代码为准，不臆造**

**这批超出"只改用例"，会动 `src/mocks/backend_mock.py`，需单独确认。**

---

### 第三批：C 类（需真实环境/服务）—— 视资源决定

| 缺口 | 可行方案 | 依赖 |
|---|---|---|
| G5 Redis 深度 | 在 db-tests 加 Redis 过期/一致性用例 | CI redis service（已有） |
| G5 DB 深度 | 加工单主表约束用例（唯一性/事务回滚） | CI mysql service（已有） |
| G6 UI E2E | 扩充 ui-regression 的页面级场景 | 测试环境 + SSH 隧道（重） |
| G7 AI L2/L3 | 已有分析（analysis-ai-unit-tests.md），属业务代码 | AI 团队排期 |

---

## 三、优先级建议

1. **先做第一批**（23 条，纯用例，风险低、收益明确）
2. 第一批次 CI 全绿后，确认第二批是否扩 mock
3. 第三批按资源和依赖决定，不强求

---

## 四、验证与验收

- 每批做完：`pytest tests/{module}/ -v` 逐模块验证
- 全量：本地 mock 全绿（当前基线 126 passed / 12 skipped，补后应 149 passed）
- CI：workflow_dispatch quality-gate 预验证
- Allure：新用例正确归入 feature/story，docstring 首行覆盖类型

---

## 五、待确认事项

1. 是否同意**先做第一批**（约 23 条纯用例）？
2. G2 权限用例的"假绿"对策是否接受（mock 占位 + 真实车道补 1-2 条）？
3. 第二批扩 mock 是否授权（会改 `src/mocks/backend_mock.py`）？
4. 第三批（需真实环境）是否纳入本次规划，还是另开任务？

确认后按第一批开始，一次一个模块，改完即测。
