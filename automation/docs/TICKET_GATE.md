# Ticket Gate（test 分支门禁）

`ticket-gate.yml` 在代码合并到 `test` 分支时执行：

1. 收集本次推送范围内的全部 commit message（不是只有最后一个 commit）。
2. 从中提取工单号。
3. 校验工单状态（`--require-resolved` 时）：只有 `resolved` / `closed` 的工单才跑功能用例。
4. 查找已 promotion 的工单用例。
5. **功能用例与回归用例分成两组分别执行**，跑不同的后端。
6. 功能用例失败 → **只通知不阻断**；回归用例失败 → 门禁失败（阻断）。
7. 上传 JUnit 和 Allure 结果。

完整闭环（扫描 → 生成 → 人工 review → 门禁）见 [TICKET_LOOP.md](./TICKET_LOOP.md)。

## 两组用例，两种后端

| 组 | 目标 | 后端 | 凭据 | 失败后果 |
|---|---|---|---|---|
| 功能用例 | 验证**本次需求**的接口在真实后端上可用 | 真实后端（`USE_MOCK=0`） | `TEST_USERNAME` / `TEST_PASSWORD` | 只通知，不阻断 |
| 回归用例 | 验证**存量功能**没被改坏 | Mock 后端（`USE_MOCK=1`） | `tests/conftest.py` 内的 mock 账号 | 阻断 |

> 回归用例（`tests/{module}/test_*_code.py`）本身是按 MockBackend 写的，
> 强行打到真实后端会因为账号/数据不存在而全红，所以回归固定跑 mock。
> 功能用例只验证「新接口能调通」，字段值由 LLM 推断，做**软断言**（见下）。

## 本地运行

```powershell
python automation/scripts/cli-ticket-gate.py `
  --message-file commit-message.txt `
  --regression tests/business_chain `
  --regression tests/tasks `
  --regression tests/call `
  --regression tests/admin `
  --regression tests/auth `
  --env local
```

校验工单状态（需要生产库只读权限）：

```powershell
python automation/scripts/cli-ticket-gate.py `
  --text "feature（ORS-837）：xxx" `
  --require-resolved
```

让功能用例也阻断分支（默认不阻断）：

```powershell
python automation/scripts/cli-ticket-gate.py --text "..." --block-on-functional-failure
```

## 工单号格式

支持：

- `ORS-837`（本项目标准格式）
- `工单:839`
- `ticket_id: 840`

**不再识别裸 `#838`**：那是 GitHub issue 引用，不是工单。
早期实现把它当工单号，会让无关提交触发工单查询，甚至跑错工单的功能用例。

## 门禁规则

- 没有工单号时只跑回归用例。
- 有工单号但未 promotion 时，不自动伪造工单用例。
- 开启 `--require-resolved` 后，工单状态不是 `resolved` / `closed` 时只跑回归，不跑功能用例。
- **状态查不到时 fail-closed**：不跑功能用例，只跑回归，并打 `::warning::`。
  （早期实现是 fail-open——查不到状态反而无条件跑功能用例，与需求正好相反。）
- **功能用例失败不阻断分支**（只通知不阻断），结果写入
  `automation/output/ticket-gate-notification.json`，并以 `::warning::` 形式输出。
  配置 `TICKET_COMMENT_API_BASE_URL` + `AUTOMATION_BOT_USERNAME/PASSWORD` 后，
  还会把结论回写成工单评论（`POST /api/tasks/{id}/comments`）。
- **回归用例失败即门禁失败**，阻断 test 分支。
- 功能用例通过 `TEST_BASE_URL` / `TEST_USERNAME` / `TEST_PASSWORD` 指向真实后端；
  真实后端缺凭据时脚本报错退出，而不是拿 mock 账号去登录。

## 生成脚本的断言策略

| 断言 | 强度 | 原因 |
|---|---|---|
| `expected_status` | 硬断言 | 来自 OpenAPI 规格，可靠 |
| `expected_fields` | **软断言**（默认只告警） | 由 LLM 从规格推断，字段值/集合总数不可靠 |

需要强断言时设置 `TICKET_CASE_STRICT_FIELDS=1`。

用例通过 `POST` 创建的资源会被记录 id，测试结束后自动 `DELETE` 清理，
清理失败不影响用例结果。

## CI 前置条件

`ticket-gate.yml` 在 CI 里会：

1. 起一次性 MySQL / Redis / Qdrant。
2. `alembic upgrade head` 建表。
3. 执行 `python backend/scripts/seed_automation_env.py` 写入种子数据：
   `Leo_test` 项目 + `automation_bot` 账号（`admin` 权限）。
   **没有种子数据时，生成用例里的登录步骤必然失败**（CI 库是空的）。
4. 启动真实后端（`uvicorn app:app`，端口 8400）。

种子账号与密码通过 `SEED_BOT_USERNAME` / `SEED_BOT_PASSWORD` 注入，
并同时传给门禁的 `TEST_USERNAME` / `TEST_PASSWORD`。
