# Task 37：测试分支触发规则调整

> 日期：2026-09-11
> 状态：已更新

## 目标

根据新的发布流程调整自动化测试触发分支：

```text
my_func_br -> test -> dev
```

- `test` 分支部署测试环境。
- 合入 `test` 后运行真实环境自动化。
- `test` 验证通过后再合并到 `dev`。
- `dev` 部署生产环境，不运行会产生测试数据的真实业务链路。

## 修改

- `.github/workflows/test.yml`
  - push 分支增加 `test`。
  - pull_request 目标分支增加 `test`。
- 自动化设计文档中的真实环境触发规则从 `push develop` 改为 `push test`。

## 安全边界

- PR 只跑 Mock/无 secret 测试。
- 真实后端链路只允许 `push test` 和 `workflow_dispatch`。
- 不在 `dev`/生产环境执行会产生业务数据的完整链路。

## 下一步

1. 将真实工单生命周期接入 `test` 分支的 workflow。
2. 完善真实测试数据清理。
3. 完整 AI 链路等待可控回复能力。
