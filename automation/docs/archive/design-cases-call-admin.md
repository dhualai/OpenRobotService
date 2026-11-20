# 设计：call + admin 模块 CRUD/场景用例补齐 + 路径对齐（task-24）

> 关联盘点：`automation/docs/gap-analysis-framework-confirmation.md`（框架差异）
> 盘点数据：Excel 93 用例 / mock 路由 / 真实接口矩阵（会话内子代理盘点，2026-08-07）
> 状态：设计稿，待人工确认后才进入实现

---

## 一、目标（经 grill 三轮确认）

1. **call 模块**：补齐 conversations（PUT/DELETE）、messages（GET 列表/单条/PUT/DELETE）、my-tasks 单条详情的用例
2. **admin 模块**：补齐 users（POST/PUT）、roles（POST/PUT）、projects（PUT/DELETE）、risks（POST/PUT/DELETE）用例
3. **路径对齐**：修正「已有 Excel 用例」的路径与真实后端契约不一致（auth 补 `/api`、call 补 `/call` 段、admin 导出/资源路径对齐）
4. **覆盖深度**：正常流程 + 异常 + 数据校验 + 权限（核心覆盖）
5. **全链路**：call 补 1 条 steps 用例（提问→转工单→确认）
6. **文档**：scenarios-call.md / scenarios-admin.md 同步校正为实际状态

已确认不做：wechat/integrations 路径与用例、task-user-mappings、Redis/AI/DB 类覆盖、现有已验证用例重排。

---

## 二、涉及文件清单

| 文件 | 改动类型 |
|------|----------|
| `automation/src/mocks/backend_mock.py` | 修改（路径修正 + messages/conversations/projects/risks 路由扩展） |
| `automation/src/runner/executor.py` | 修改（`_auth_for_role` 登录路径 `/auth/login` → `/api/auth/login`） |
| `automation/tests/conftest.py` | 修改（`mock_auth_token` 登录路径同上） |
| `automation/testdata/cases/api-test-cases.xlsx` | 修改（路径修正 + 新用例追加，openpyxl 脚本处理） |
| `automation/docs/testing/scenarios/scenarios-call.md` | 修改（校正为新用例清单） |
| `automation/docs/testing/scenarios/scenarios-admin.md` | 修改（校正 + 补齐 users/roles/projects/risks 场景） |
| 新增 `automation/scripts/cli-update-cases-xlsx.py` | 新增（Excel 批量路径修正脚本，可复用） |
| 新增 `automation/docs/design-cases-call-admin.md` | 本文 |
| 新增 `automation/docs/worklog/task-24-call-admin-cases.md` | 新增（worklog） |

## 三、路径修正清单（mock + Excel 同步）

| # | 现状（mock/Excel） | 真实后端 | 影响用例 |
|---|--------------------|----------|----------|
| P1 | `/auth/login`、`/auth/me` | `/api/auth/login`、`/api/auth/me` | Excel auth 12 条 + `conftest.py:27` + `executor.py:81` + mock 路由 |
| P2 | `/api/conversations*` | `/api/call/conversations*` | Excel call ~6 条 + mock 路由 |
| P3 | `/api/qa/ask`、`/api/qa/ask/stream` | `/api/call/qa/ask*` | Excel call ~4 条 + mock 路由 |
| P4 | `/api/messages` | `/api/call/messages` | Excel call ~2 条 + mock 路由 |
| P5 | `/api/my-tasks/` | `/api/call/my-tasks/` | Excel call ~3 条 + mock 路由 |
| P6 | `/api/admin/export` | `/api/admin/export/project/{code}` | Excel admin 2 条 + mock 路由 |
| P7 | `/api/admin/resources*` | `/api/admin/resource-manager/resources*` | Excel admin 4 条 + mock 路由 |

> 执行方式：脚本读 xlsx → 按映射替换 path 列 → 写回；mock 路由注册同步改。回归验证现有 93 用例全绿即证明路径修正无损。

## 四、Mock 路由扩展清单

| 功能域 | 新增路由 | 语义 |
|--------|----------|------|
| call | PUT `/api/call/conversations/{id}` | 更新会话（404 不存在） |
| call | DELETE `/api/call/conversations/{id}` | 删除会话（404 不存在） |
| call | GET `/api/call/messages?conversation_id=` | 消息列表（无参数 422） |
| call | GET `/api/call/messages/{id}` | 消息详情（404 不存在） |
| call | PUT `/api/call/messages/{id}` | 更新消息（404 不存在） |
| call | DELETE `/api/call/messages/{id}` | 删除消息（404 不存在） |
| admin | PUT `/api/admin/projects/{id}` | 更新项目（404 不存在） |
| admin | DELETE `/api/admin/projects/{id}` | 删除项目（404 不存在） |
| admin | POST `/api/admin/projects/risks` | 创建风险（缺字段 422） |
| admin | PUT `/api/admin/projects/risks/{risk_code}` | 更新风险（404 不存在） |
| admin | DELETE `/api/admin/projects/risks/{risk_code}` | 删除风险（404 不存在） |
| admin | POST `/api/admin/users` | 创建用户（重名 409/400、缺字段 422） |
| admin | PUT `/api/admin/users/{username}` | 更新用户（404 不存在） |
| admin | POST `/api/admin/roles` | 创建角色（重名 409/400、缺字段 422） |
| admin | PUT `/api/admin/roles/{id}` | 更新角色（404 不存在） |

> users/roles 的 POST/PUT 盘点确认 mock 已支持（PUT/DELETE `/api/admin/users/{id}` 存在），实现时核对路由匹配；若已支持则仅补 Excel。

## 五、新用例设计（Excel 追加）

### 5.1 call 模块（新增 ~15 条，ID 从 CALL-026 起）

| 用例 | 接口 | 场景 |
|------|------|------|
| CALL-026/027/028 | PUT `/api/call/conversations/{id}` | 正常更新 / 404 / 缺字段 422 |
| CALL-029/030 | DELETE `/api/call/conversations/{id}` | 正常 / 404 |
| CALL-031/032 | GET `/api/call/messages?conversation_id=` | 正常列表 / 缺参数 422 |
| CALL-033/034 | GET `/api/call/messages/{id}` | 正常 / 404 |
| CALL-035/036 | PUT `/api/call/messages/{id}` | 正常 / 404 |
| CALL-037/038 | DELETE `/api/call/messages/{id}` | 正常 / 404 |
| CALL-039 | GET `/api/call/my-tasks/{task_id}` | 正常详情 |
| CALL-040 | POST `/api/ai/qa/submit` 权限 | 未认证 401 |
| CALL-041 | **steps 全链路**：qa/ask → submit → ack 转工单闭环 | 多步串联 |

### 5.2 admin 模块（新增 ~20 条，ID 从 ADMIN-025 起）

| 用例 | 接口 | 场景 |
|------|------|------|
| ADMIN-025/026/027 | POST `/api/admin/users` | 正常创建 / 缺字段 422 / 重名冲突 |
| ADMIN-028/029 | PUT `/api/admin/users/{username}` | 正常更新 / 404 |
| ADMIN-030/031/032 | POST `/api/admin/roles` | 正常创建 / 缺字段 422 / 重名冲突 |
| ADMIN-033/034 | PUT `/api/admin/roles/{id}` | 正常更新 / 404 |
| ADMIN-035/036 | PUT `/api/admin/projects/{id}` | 正常更新 / 404 |
| ADMIN-037/038 | DELETE `/api/admin/projects/{id}` | 正常 / 404 |
| ADMIN-039/040 | POST `/api/admin/projects/risks` | 正常创建 / 缺字段 422 |
| ADMIN-041/042 | PUT `/api/admin/projects/risks/{risk_code}` | 正常更新 / 404 |
| ADMIN-043/044 | DELETE `/api/admin/projects/risks/{risk_code}` | 正常 / 404 |
| ADMIN-045 | 权限：无 token 访问 users 创建 | 401 |

> 用例字段按既有 Excel 规范（id/module/function/title/前置条件/method/path/payload/expected_status/expected_fields/优先级/是否自动化/note/可选 steps）。

## 六、实现步骤（顺序执行，一次一个模块）

1. **路径修正**：脚本（`scripts/cli-update-cases-xlsx.py`）批量改 Excel path 列 + mock 路由 + `executor.py`/`conftest.py` 登录路径
   - 验证：`pytest tests/ -m api` 现有 93 用例全绿
2. **call 模块**：mock 扩展（conversations/messages/my-tasks 路由）→ Excel 追加 CALL-026~041 → 验证 call 全过
3. **admin 模块**：mock 扩展（projects/risks，核对 users/roles 已有）→ Excel 追加 ADMIN-025~045 → 验证 admin 全过
4. **场景文档校正**：scenarios-call.md / scenarios-admin.md 对齐新用例
5. **收尾**：全量回归 + Allure 报告 + worklog

## 七、风险分析

| 风险 | 等级 | 缓解 |
|------|------|------|
| 路径修正影响现有 93 用例（Excel 约 33 条改 path） | 中 | 第 1 步独立回归；脚本原子替换后全量验证 |
| mock 路由匹配遗漏导致误伤既有用例 | 中 | 路由改动与回归同轮完成，失败即定位 |
| users/roles 的 POST/PUT mock 实际未支持（盘点为核对结论） | 低 | 实现时先核对 `backend_mock.py`，缺则补 |
| Excel 二进制写入损坏 | 低 | openpyxl 脚本 + 改后立即跑用例验证可读性 |
| 新用例 expected_fields 与 mock 响应结构不一致 | 中 | 按 mock 实际响应结构设计断言，先跑 mock 对齐 |

## 八、验收标准

- [ ] 现有 93 用例在路径修正后全绿（行为不变）
- [ ] call 新增 16 条、admin 新增 21 条用例全部通过
- [ ] mock 新增路由有对应用例覆盖
- [ ] `USE_MOCK=0` 时 auth/call/admin 路径与真实后端契约一致（路径核对无偏差）
- [ ] 场景文档与 Excel 一致
- [ ] Allure 报告生成成功
