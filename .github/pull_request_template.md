## 改动说明

<!-- 一句话说清这个 PR 做了什么、为什么 -->

## 变更类型

- [ ] feat 新功能
- [ ] fix 修复
- [ ] chore / refactor / docs / test

## CI 与部署（涉及 .github/ 或 deploy/ 时才勾）

- [ ] 本 PR **未**改动 `.github/workflows/`、`deploy/`
- [ ] 本 PR 改动了 CI / 部署文件 → 下面已说明改了哪些、为什么，并已找 `@dhualai` 复核

改动 CI 时请附上：

```bash
git diff --stat origin/test...HEAD -- .github/ deploy/
```

> `ci-guard.yml` 会断言部署工作流仍保留 9 项关键能力（结构门禁、40x5s 健康窗口、gate outcome），
> 缺失任一项 PR 会变红。

## 分支纪律

- [ ] 本分支**没有**执行 `git merge main`

> 仓库的 `main` 是发版注册壳，其 workflow 永久停在 10-09 旧版（ruleset 的 update 规则冻死了 main）。
> 把 main 合进功能分支 = 把旧版 CI 带回 test。2026-10-20 的 PR #338 就是这样删掉了 test 的结构门禁，
> 导致次日部署报 `1054 Unknown column`。
>
> 需要同步基线请用 `git rebase origin/test`，不要 merge main。

## 测试

- [ ] 后端 `pytest` 通过
- [ ] 前端 `vitest` 通过
- [ ] 涉及 H5 时，微信内置浏览器实测通过（见 CONTRIBUTING.md）

## 影响面

- 关联需求 / issue：
- 部署后需执行的动作（数据库变更、配置项、手工步骤）：
