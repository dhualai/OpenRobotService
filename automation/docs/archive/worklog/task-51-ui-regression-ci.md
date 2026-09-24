# Task 51：UI 回归 GitHub Actions 接入

> **后续变更（2026-09-22）**：本文描述的 CI 接入方式已整体废弃。当前改为
> self-hosted runner 直连 `127.0.0.1:9400` / `127.0.0.1:9411`，不再使用 SSH 密钥、
> `authorized_keys` 白名单或 `permitopen`。相关 Secret 与公钥均已删除。
> 本文属于历史归档，勿作为现行口径。

> 日期：2026-09-19
> 状态：实现完成，等待 GitHub workflow 实测

## 目标

支持 `test` 分支 push 和 `workflow_dispatch` 执行完整 UI 回归：

```text
UI 完整业务链路
+ 6 条真实环境 Smoke
+ 数据库补偿清理
+ 联合 Allure artifact
+ test commit 评论
```

## 修改文件

- `.github/workflows/ui-regression.yml`
- `automation/docs/UI_REGRESSION_CI.md`
- `automation/docs/worklog/task-51-ui-regression-ci.md`

## 实现内容

1. 新增 UI regression workflow：
   - `workflow_dispatch`
   - `push test`
   - 固定 concurrency，防止并行污染。
2. PR 和 fork 不触发，不接触 Secrets。
3. 安装 Python、Node、Playwright Chromium。
4. 构建前端正式产物。
5. pytest fixture 建立三条 SSH 隧道：
   - 后端 9400
   - 自动化 AI 9411
   - MySQL 3306
6. 默认启用数据库补偿清理。
7. 生成 Allure history，并上传 artifact。
8. test push 成功后/失败后评论 commit：
   - 用例数
   - 失败数
   - 错误数
   - 清理结果
   - run 链接
   - artifact 名称
9. 不发布公开 GitHub Pages。

## 服务器准备

已扩展 `openrobot-ci-actions` SSH key：

```text
permitopen=127.0.0.1:9400
permitopen=127.0.0.1:9411
permitopen=127.0.0.1:3306
```

原 `authorized_keys` 已保留备份。

## 验证计划

1. 配置 GitHub Secrets 和 Variables。已完成：
   - 复用 `REAL_U1_PASSWORD`、`REAL_U2_PASSWORD`。
   - 新增 `UI_REGRESSION_CLEANUP_PASSWORD`、`UI_REGRESSION_DB_PASSWORD`。
   - 10 个 `UI_REGRESSION_*` Repository Variables 已创建。
2. 合并 workflow 到 test 分支。
3. 通过 `workflow_dispatch` 人工触发。
4. 确认 job 成功、artifact 可下载。
5. 确认 commit 评论生成。
6. 连续运行两次确认稳定。
7. 检查数据库 dry-run 为 0。

## 风险

- workflow 尚未在 GitHub runner 实测。
- `workflow_dispatch` 需要 workflow 已存在于默认分支。
- 首次运行需要确认 GitHub Variables 与 Secrets 配置完整。
- artifact 需要 GitHub 登录后下载。

## 下一步

1. 配置 Secrets 和 Variables。
2. 推送当前功能分支。
3. 合并到 test 后人工触发 workflow。
4. 根据首次运行结果修正 CI 环境差异。
