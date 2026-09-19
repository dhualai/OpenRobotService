# UI 回归 GitHub Actions 接入设计

> 状态：只读分析完成，等待人工 review
> 日期：2026-09-19
> 范围：真实测试环境 UI 场景 + 真实环境 Smoke + Allure artifact
> 禁止：PR 接触 Secrets；任何环境连接生产环境

## 1. 目标

把当前本地一键回归迁移到 GitHub Actions：

```text
test 分支 push 或 workflow_dispatch
-> Ubuntu runner
-> 安装 Python、Node、Playwright、Allure
-> 使用 TEST_SSH_PRIVATE_KEY 建立测试环境连接
-> 构建前端
-> 执行 UI 完整业务链路 + 6 条 Smoke
-> API 500 时执行数据库补偿清理
-> 生成联合 Allure HTML
-> 上传 Actions artifact
-> 在 test 分支 commit 评论运行摘要和报告入口
```

## 2. 已只读检查

- `.github/workflows/real-access-check.yml`
- `.github/workflows/test.yml`
- `.github/workflows/ai-test.yml`
- `.github/workflows/ui-smoke.yml`
- `automation/ci/README.md`
- `automation/scripts/run-ui-regression.ps1`
- `automation/tests/ui/conftest.py`
- `automation/docs/UI_REGRESSION.md`
- `automation/docs/design-ui-regression-cleanup.md`

## 3. 现有 CI 能力

当前仓库已经具备：

- GitHub 托管 Ubuntu runner 通过 SSH 隧道访问测试环境。
- `TEST_SSH_PRIVATE_KEY` Secret 的注入方式。
- Python、Node、Playwright 安装模式。
- Allure results 和 HTML artifact 上传模式。
- `simple-elf/allure-report-action` 生成 history 报告。
- `test.yml` 当前会把 develop 分支报告发布到 GitHub Pages。

## 4. 接入边界

### 4.1 触发

第一版：

```yaml
on:
  workflow_dispatch:
  push:
    branches:
      - test
```

不接：

- PR。
- fork。
- `develop`、`hxg`。
- 生产环境。

原因：

- UI 回归会产生真实测试工单。
- 使用 SSH key、账号密码和数据库清理账号。
- 这些 Secret 不能暴露给 PR 或 fork。

### 4.2 并发

必须串行：

```yaml
concurrency:
  group: ui-regression-test
  cancel-in-progress: false
```

原因：

- 避免两位测试人员同时操作同一测试环境。
- 避免数据库清理和工单状态互相干扰。
- 第一版优先保证结果可解释，不优先抢运行速度。

## 5. Secrets 与 Variables

### 5.1 GitHub Secrets

```text
TEST_SSH_PRIVATE_KEY
UI_REGRESSION_U1_PASSWORD
UI_REGRESSION_U2_PASSWORD
UI_REGRESSION_CLEANUP_PASSWORD
UI_REGRESSION_DB_PASSWORD
```

### 5.2 Repository Variables

```text
UI_REGRESSION_SSH_HOST
UI_REGRESSION_SSH_USER
UI_REGRESSION_SSH_PORT
UI_REGRESSION_U1_USERNAME
UI_REGRESSION_U2_USERNAME
UI_REGRESSION_CLEANUP_USERNAME
UI_REGRESSION_DB_USER
UI_REGRESSION_DB_NAME
UI_REGRESSION_CLEANUP_PROJECT_ID
UI_REGRESSION_CLEANUP_TITLE_PREFIX
```

非敏感端口和超时可直接写在 workflow：

```text
UI_REGRESSION_BACKEND_REMOTE_PORT=9400
UI_REGRESSION_BACKEND_LOCAL_PORT=19400
UI_REGRESSION_AI_REMOTE_PORT=9411
UI_REGRESSION_AI_LOCAL_PORT=19411
UI_REGRESSION_DB_REMOTE_PORT=3306
UI_REGRESSION_DB_LOCAL_PORT=19402
UI_REGRESSION_TUNNEL_TIMEOUT=60
```

## 6. Workflow 结构

目标文件：

```text
.github/workflows/ui-regression.yml
```

建议 job 结构：

```text
jobs:
  ui-regression:
    runs-on: ubuntu-latest
    timeout-minutes: 30
```

步骤：

1. Checkout。
2. Setup Python 3.11。
3. Setup Node 22。
4. `pip install -e automation/`。
5. `npm ci --prefix frontend`。
6. `python -m playwright install --with-deps chromium`。
7. 写入 `$HOME/.ssh/ci_key`。
8. 设置 UI 回归环境变量和 Secrets。
9. 构建前端。
10. 运行：
    - `automation/tests/ui/test_call_qa_to_ticket_close_regression.py`
    - `automation/tests/ui/test_real_safe_api_smoke.py`
11. 生成联合 Allure results。
12. `always()` 生成 HTML 并上传 artifact。
13. 对 `test` push 的 commit 创建评论。

## 7. SSH 隧道策略

复用当前 `ui_regression_runtime`：

- backend：`19400 -> 9400`
- automation AI：`19411 -> 9411`
- MySQL：`19402 -> 3306`

workflow 只负责写 SSH 私钥并设置：

```text
UI_REGRESSION_SSH_KEY=$HOME/.ssh/ci_key
```

pytest fixture 负责启动和回收三条隧道。

优点：

- 复用本地已经验证过的隧道逻辑。
- 不在 workflow 中重复实现 SSH 生命周期。
- 测试结束或异常退出时由 fixture finally 回收。

## 8. 数据库清理

CI 默认开启：

```text
UI_REGRESSION_DB_CLEANUP_ENABLED=1
```

清理流程：

```text
API 删除优先
-> HTTP 500
-> 数据库补偿
-> Allure 中展示 API 和数据库清理结果
```

安全边界：

- 只连接 `helpdesk_test`。
- 使用最小权限账号 `automation_cleanup`。
- Secret 不进入日志。
- `tasks` 删除必须匹配本次 ticket_id、project_id 和标题前缀。

## 9. Allure 报告与链接

### 9.1 报告生成

推荐：

```text
simple-elf/allure-report-action@v1.7
```

输出：

```text
automation/output/allure-history/
```

### 9.2 artifact

```yaml
- uses: actions/upload-artifact@v4
  with:
    name: allure-report-ui-regression-${{ github.run_number }}
    path: automation/output/allure-history
```

### 9.3 是否发布 GitHub Pages

第一版不发布公开 Pages。

原因：

- 当前仓库是开源仓库。
- 报告包含测试账号、内部项目名、工单和接口信息。
- GitHub Pages 通常是公开可访问的。

第一版只提供：

- Actions run 链接。
- 本次 run 的 Allure artifact 名称。
- 测试通过/失败数量和失败用例名称。

后续如果必须提供直接 HTML 链接，应使用具备登录鉴权的独立静态站点。

## 10. Commit 评论

push 到 `test` 时，使用 `actions/github-script@v7` 创建 commit comment：

```text
UI 回归完成

结果：7 passed / 0 failed
场景用例：1
单接口 Smoke：6
清理：成功 / 告警数量

报告：下载 artifact allure-report-ui-regression-<run_number>
运行记录：<run_url>
```

评论不包含：

- 密码。
- token。
- 完整响应体。
- 工单敏感内容。

## 11. 失败分类

报告需要区分：

1. 环境失败：
   - SSH 不可达。
   - 隧道启动失败。
   - 测试后端或 AI 健康检查失败。
   - DB 补偿隧道不可用。
2. 业务失败：
   - 页面步骤断言失败。
   - 接口状态码或字段断言失败。
3. 清理告警：
   - API 删除 500。
   - DB 补偿失败或无匹配。

第一版策略：

- 环境失败和业务失败让 job 失败。
- 清理告警在 Allure 展示，但不覆盖业务结论。

## 12. 文件计划

| 文件 | 变更 |
|---|---|
| `.github/workflows/ui-regression.yml` | 新增 CI workflow |
| `automation/ci/scripts/run-ui-regression-ci.sh` | 新增 Linux 运行入口 |
| `automation/docs/UI_REGRESSION_CI.md` | 新增 CI 使用和排障文档 |
| `automation/docs/worklog/task-51-ui-regression-ci.md` | 完成后记录结果 |

如果 shell runner 在 workflow 中足够简单，可不新增脚本；优先保持 workflow 可读。

## 13. 验证计划

1. 先通过 `workflow_dispatch` 人工触发。
2. 确认 SSH、backend、AI、DB 三条隧道建立成功。
3. 确认前端构建成功。
4. 确认 1 条 UI 场景和 6 条 Smoke 全部通过。
5. 确认 API 500 时数据库补偿自动生效。
6. 确认残留 dry-run 为 0。
7. 确认 Allure artifact 可下载并打开。
8. 确认 test 分支 push 能自动触发。
9. 确认 commit 评论包含摘要和 run 链接。
10. 连续运行两次，结果稳定。

## 14. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| Secret 在 PR 泄漏 | P0 | 禁止 PR/fork 触发 |
| 并发运行污染环境 | P0 | 固定 concurrency，禁止并行 |
| 公开报告泄漏内部数据 | P0 | 第一版不发布公开 Pages |
| SSH 隧道不稳定 | P1 | 60 秒超时，报告明确环境失败 |
| DB 清理误删 | P0 | 最小权限账号、精确 ticket_id、project/title 过滤 |
| 报告 artifact 需要登录 | P2 | 评论 run 链接并提供 artifact 名称 |

## 15. 待确认事项

1. 是否接受仅 `test` push 和 `workflow_dispatch` 触发。
2. 是否接受第一版不发布公开 GitHub Pages。
3. 是否接受 commit 评论只给 run 链接和 artifact 名称。
4. 是否接受 CI 默认开启数据库补偿清理。
5. 是否为非敏感配置创建 Repository Variables。

确认后进入实现阶段。
