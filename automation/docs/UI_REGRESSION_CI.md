# UI 回归 GitHub Actions 使用说明

> **已废弃（2026-09-22）：本文描述的「GitHub 托管 runner + SSH 隧道」链路已停用。**
>
> 现行方案改为 **self-hosted runner 直连**：
>
> - runner 部署在测试服务器本机，标签 `self-hosted, linux, x64, ors-test`
> - 直接访问 `http://127.0.0.1:9400`（后端）与 `http://127.0.0.1:9411`（可控 AI），不再需要端口转发
> - `TEST_SSH_PRIVATE_KEY` Secret 已删除；服务器 `authorized_keys` 中对应的
>   `openrobot-ci-actions` 公钥也已移除
>
> 本文仅作历史记录保留，现行口径见 `automation/docs/SELF_HOSTED_RUNNER.md`。

## 1. 触发条件

```text
push test
workflow_dispatch
```

PR 和 fork 不触发，不接触 Secrets。

## 2. 必需 Secrets

```text
TEST_SSH_PRIVATE_KEY
REAL_U1_PASSWORD
REAL_U2_PASSWORD
UI_REGRESSION_CLEANUP_PASSWORD
UI_REGRESSION_DB_PASSWORD
```

## 3. Repository Variables

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

非敏感端口、项目 ID 和标题前缀有默认值。

## 4. SSH 白名单（已废弃）

> 本节随 `TEST_SSH_PRIVATE_KEY` 一并废弃，仅作历史记录保留。

`TEST_SSH_PRIVATE_KEY` 对应的公钥当时需要允许：

```text
permitopen="127.0.0.1:9400"
permitopen="127.0.0.1:9411"
permitopen="127.0.0.1:3306"
```

不允许执行远程 shell 命令，只允许端口转发。

## 5. SSH 会话模型

backend、automation AI 和 MySQL 不分别建立三条 SSH 会话，而是在一次 SSH 连接中注册全部
`-L` 转发：

```text
19400 -> 127.0.0.1:9400
19411 -> 127.0.0.1:9411
19402 -> 127.0.0.1:3306
```

这样避免同一 runner 在短时间内重复认证和建立第二条、第三条会话。

## 6. Workflow 步骤

```text
校验 Secrets/Variables
-> 安装 Python、Node、Playwright
-> 写入 SSH private key
-> 输出公钥指纹并预检全部 SSH 转发
-> 检查 backend、AI health 和数据库端口（单阶段最多重试 3 次）
-> 构建前端
-> pytest 启动一次 SSH 多端口转发
-> 执行 UI 场景 + Smoke
-> API 500 时数据库补偿
-> 生成 Allure HTML
-> 上传 artifact
-> test commit 评论摘要
```

### 6.1 预检重试

健康检查通过本地 SSH 转发执行，单阶段配置：

```text
HTTP timeout: 30 秒
attempts: 3
retry delay: 2 秒
trust_env: false
```

每次尝试都会打印阶段和次数：

```text
Backend health: attempt 1/3
Automation AI health: attempt 1/3
Database forward: attempt 1/3
```

这样可以吸收 GitHub runner 到测试环境之间的瞬时抖动，同时保留最后一次错误。

## 7. 报告

artifact 名称：

```text
allure-report-ui-regression-<run_number>
```

第一版不发布公开 GitHub Pages。

原因：

- 仓库是开源的。
- 报告包含内部项目、接口和测试数据。
- Actions artifact 需要具备仓库访问权限的账号查看。

## 8. 失败分类

- SSH、后端、AI、数据库连接失败：环境失败。
- 页面步骤或接口断言失败：业务失败。
- API 删除 500、DB 补偿失败：清理告警。

环境失败和业务失败会让 job 失败；清理告警不会覆盖业务结论。

## 9. 排障

1. 先查看 `Preflight real environment SSH forwards` 步骤：
   - 公钥指纹失败：检查 `TEST_SSH_PRIVATE_KEY`。
   - SSH 进程提前退出：查看该步骤输出的 SSH stderr。
   - backend health 失败：检查测试后端 `9400`。
   - AI health 失败：检查自动化 AI `9411`。
   - 数据库端口失败：检查 MySQL `3306` 和 `permitopen`。
2. 检查 SSH 公钥是否包含三个 `permitopen`。
3. 检查 `automation_cleanup` 是否能连接 `helpdesk_test`。
4. 下载 Allure artifact 查看步骤截图、接口状态和清理结果。
