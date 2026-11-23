# Task 56：覆盖缺口补强第一批（A 类纯用例）

- **日期**：2026-11-17
- **状态**：完成（本地验证通过；待 CI 预验证）
- **前提**：用户 2026-11-16 21:01 解除"不新增用例"约束并确认第一批方案
- **设计依据**：`docs/testing/scenarios/design-gap-filling.md`

## 本次目标

补 G2 权限 / G3 mock 零覆盖接口 / G4 异常路径，**只新增用例、不改 mock、不动业务代码**。

## 修改文件（3 个新增测试文件）

| 文件 | 用例数 | 覆盖缺口 |
|---|---|---|
| `tests/tasks/test_tasks_documents_code.py`（新建） | 10 | G3：spec-doc（默认值/revision 递增/缺 content）、operation-logs、relations（创建/缺 target/列表+树）、respond、complete-step（未协商 400/正常） |
| `tests/tasks/test_tasks_auth_code.py`（新建） | 4 | G2：无 token 建单/筛选/催办/查可指派人员 → 401 |
| `tests/tasks/test_tasks_negative_code.py`（新建） | 4 | G4：更新/删除/指派不存在工单 → 404、创建缺 description → 422 |

合计 **+18 条用例**。

## 关键调研结论（影响设计）

1. **`POST /api/tasks` 有鉴权**（在 `_route_tasks` 的 `if not rest and method == "POST"` 分支内调 `_require_auth`），初始误判为无鉴权，读路由后修正。
2. **`PATCH /api/tasks/{id}/assign` 在 mock 无鉴权**（实测返回 200）→ 删除"无 token 转派"用例，避免假绿。
3. **call 模块全部无鉴权**（会话/消息/流式问答均未调 `_require_auth`）→ 不在 call 补权限用例。
4. **创建工单 mock 要求 `title` + `description` 双必填** → G3 用例的前置建单需带 description（首次 10 条全挂即此原因，已修正）。
5. mock 状态机 `_ALLOWED` 仅 6 状态（真实后端 11）→ G1 状态机补强需扩 mock，未纳入本批。

## 测试结果

```
tests/tasks/test_tasks_documents_code.py   10 passed
tests/tasks/test_tasks_auth_code.py         4 passed
tests/tasks/test_tasks_negative_code.py     4 passed

# 全量改动范围
144 passed, 12 skipped in 49.62s
（基线 126 + 新增 18 = 144，吻合）
```

12 skip 为本地无 MySQL 的 DB 用例，按设计跳过。

## 风险与说明

- **权限用例仅覆盖"无 token 被拒"**，mock 不做角色校验（除 admin/转派）；角色越权语义由真实后端场景车道验收，文件中已加 docstring 说明，避免误认为权限已全覆盖。
- 新增用例全部基于 mock 稳定返回做字段断言，未改任何 mock 逻辑。
- 未纳入本批：G1 状态机（需扩 mock）、G5 Redis/DB 深度、G6 UI E2E、G7 AI L2/L3（需真实环境/服务）。

## 下一步

1. 已提交 PR #466（`ci/gap-filling-batch1` → test，7 文件 +448/-9），待合入。
2. 第二批（G1 状态机）：用户 2026-11-19 决定**不授权扩 mock**，暂缓；状态机流转由真实后端场景用例验收。
3. 第三批（G5/G6/G7）依赖真实环境/AI 服务，待决策。
