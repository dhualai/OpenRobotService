# Task 52：修复 UI Regression CI Allure 生成

> 日期：2026-09-20
> 状态：实现完成，待 GitHub Actions 验证

## 问题

PR #105 合并到 `test` 后，UI Regression workflow 自动触发两次，均在测试执行前失败。

日志根因：

```text
simple-elf/allure-report-action@v1.7
-> openjdk:8-jre-alpine
-> Docker Hub: not found
```

旧 Action 依赖已下架的基础镜像，导致所有后续测试步骤被跳过。

## 修复

1. 删除 `simple-elf/allure-report-action@v1.7`。
2. 在 Ubuntu runner 直接安装：
   - `default-jre-headless`
   - `allure-commandline`
3. 测试结束后直接运行：

```bash
allure generate \
  automation/output/allure-results-ui-regression-combined \
  -o automation/output/allure-report-ui-regression \
  --clean
```

4. 上传 `automation/output/allure-report-ui-regression` 为 artifact。

## 修改文件

- `.github/workflows/ui-regression.yml`
- `automation/docs/worklog/task-52-ui-regression-allure-cli-ci-fix.md`

## 验证计划

1. 创建修复 PR 到 `test`。
2. 合并后让 `push test` 再次触发。
3. 确认 Java、Allure CLI、SSH、前端构建和测试步骤实际执行。
4. 确认 Allure HTML artifact 可下载。

## 风险

- `npm install -g allure-commandline` 依赖 npm registry 可用。
- 首次运行仍需验证 Ubuntu runner 上的其他环境差异。
