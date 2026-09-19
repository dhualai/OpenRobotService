# UI 回归 GitHub Actions 使用说明

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

## 4. SSH 白名单

`TEST_SSH_PRIVATE_KEY` 对应的公钥必须允许：

```text
permitopen="127.0.0.1:9400"
permitopen="127.0.0.1:9411"
permitopen="127.0.0.1:3306"
```

不允许执行远程 shell 命令，只允许端口转发。

## 5. Workflow 步骤

```text
校验 Secrets/Variables
-> 安装 Python、Node、Playwright
-> 写入 SSH private key
-> 构建前端
-> pytest 启动三条 SSH 隧道
-> 执行 UI 场景 + Smoke
-> API 500 时数据库补偿
-> 生成 Allure HTML
-> 上传 artifact
-> test commit 评论摘要
```

## 6. 报告

artifact 名称：

```text
allure-report-ui-regression-<run_number>
```

第一版不发布公开 GitHub Pages。

原因：

- 仓库是开源的。
- 报告包含内部项目、接口和测试数据。
- Actions artifact 需要具备仓库访问权限的账号查看。

## 7. 失败分类

- SSH、后端、AI、数据库连接失败：环境失败。
- 页面步骤或接口断言失败：业务失败。
- API 删除 500、DB 补偿失败：清理告警。

环境失败和业务失败会让 job 失败；清理告警不会覆盖业务结论。

## 8. 排障

1. 检查 `TEST_SSH_PRIVATE_KEY` 是否配置。
2. 检查 SSH 公钥是否包含三个 `permitopen`。
3. 检查测试后端 `9400`、自动化 AI `9411` 是否健康。
4. 检查 `automation_cleanup` 是否能连接 `helpdesk_test`。
5. 下载 Allure artifact 查看步骤截图、接口状态和清理结果。
