# Task 35：业务链路与单接口合并 Allure 报告

> 日期：2026-09-11
> 状态：已完成并验证

## 目标

把“场景用例”和“单接口用例”放到同一份 Allure 报告中，并在 Suites 视图中分成两个顶层分类。

## 实现

1. 在 `automation/tests/conftest.py` 增加 Allure 自动分类：
   - `automation/tests/business_chain/` -> `场景用例 / 完整业务链路`
   - 其他 API 用例 -> `单接口用例 / 原模块名`
2. 一次性执行全部 API 用例，共用同一个 `allure-results` 目录。
3. 生成合并 Allure HTML 报告。

## 修改文件

- `automation/tests/conftest.py`
- `automation/docs/worklog/task-35-combined-allure-report.md`

## 验证结果

```text
256 passed, 3 skipped, 32 deselected
```

Allure 分类统计：

```text
场景用例：1
单接口用例：258
```

## 报告

- 本地地址：`http://127.0.0.1:8084`
- 报告文件：`automation/output/allure-report-combined/index.html`

## 风险

- 当前仍为 Mock 环境报告。
- 3 条真实后端 smoke 用例按配置跳过。

## 下一步

1. 在 Allure 的 Suites 视图确认两个顶层分类。
2. 真实环境可用后，将业务链路场景切换到真实后端执行。
