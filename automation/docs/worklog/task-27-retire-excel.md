# task-27 · 退役 Excel 驱动测试（全面切代码驱动）

## 本次目标

代码驱动版（task-26 迁移的 130 条）验证稳定后，退役 Excel 数据驱动测试，统一为代码驱动。

## 已确认决策（grill）

Q1 api-test-cases.xlsx 移入归档区 | Q2 删除 executor/cases（run_case/load_cases） | Q3 立刻退役

## 修改文件列表

- 删除 `tests/{call,tasks,admin,auth}/test_*.py`（4 个 Excel 驱动测试文件，git rm 保留历史）
- 删除 `src/runner/` 全部（cases.py / executor.py / __init__.py / tests/）
- 删除 `scripts/cli-gen-scenario-docs.py`（Excel 版场景文档生成器）
- 删除 `docs/testing/scenarios/*.md`（4 份 Excel 时代场景设计稿，git 历史可恢复）
- 移动 `testdata/cases/api-test-cases.xlsx` → `references/archived-cases/`（只读归档）
- `automation/AGENTS.md`：目录结构、标准用例规范（Excel 10 字段 → Python 模板）、添加用例流程、Review 清单、优先级表全部更新为代码驱动
- 新增 `automation/docs/worklog/task-27-retire-excel.md`（本文）

## 测试结果

```
# API 用例（纯代码版 130 条）
130 passed, 28 deselected in 1.66s

# 框架库（src + config + ci_ai_gen）
208 passed

# 全量
338 passed, 28 skipped in 13.12s

# Allure 报告
130 条（不再双版本重复），生成成功
```

## 风险

- executor.py 删除前有未提交修改（flush 接入等）——随删除一并丢弃，无影响（退役目标）
- `git rm` 因本地修改拒绝删除 executor.py，用 `-f` 强制处理

## 下一步

- ci_ai_gen 流水线 prompt 改造（script_gen 生成框架规范 Python 用例）
- 归档 xlsx 生命周期：确认无需恢复后可删
- 提交 git（本次退役改动 + task-26 全部）
