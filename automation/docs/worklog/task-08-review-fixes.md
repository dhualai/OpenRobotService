# Task-08: 复审问题修复

## 基本信息
| 字段 | 值 |
|------|-----|
| 任务编号 | TASK-08 |
| 任务名称 | 复审问题修复 |
| 来源 | hi 窗口复审 |
| 分支 | hxg |
| 日期 | 2026-07-27 |
| 状态 | 已完成 |

## P1 — 已修复

### AllureLogHandler flush 问题
**问题**：AllureLogHandler.emit() 把日志追加到线程本地列表，但 flush() 从未被调用，日志丢失。
**修复**：在 logging_fixtures.py 添加 autouse fixture，每次测试结束后遍历 root logger handlers 调 flush()。
**验证**：23 个相关测试全部通过。

## P2 — 已处理

### 骨架模块 README
为 api/ / ui/ / ai/ / db/ / mocks/ 添加了 README 说明用途和边界。

### assert_records_match 性能
已评估：当前 O(n*m*k) 在小数据集上可接受，留待数据量大时优化。b/acklog。

## P3 — 未处理（backlog）
1. Retry sync/async 分支代码重复（20 行）
2. log_config fixture 命名误导
3. 断言不支持异步函数

