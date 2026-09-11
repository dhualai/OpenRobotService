# Task 36：真实后端工单生命周期场景

> 日期：2026-09-11
> 状态：真实后端链路已跑通，清理机制待完善

## 目标

在不依赖 AI 可控回复的前提下，用真实测试环境验证“U1 建单并指派 U2 -> U2 接单 -> 推进 problem 阶段 -> 提交已解决 -> U1 关闭”的工单生命周期。

## 实现内容

1. 新增真实后端场景测试：
   - `automation/tests/real/test_ticket_lifecycle_real.py`
2. 场景步骤：
   - U1/U2 登录
   - U1 创建 problem 工单，项目固定 `Leo_test`
   - U1 模拟派单给 U2
   - U2 读取阶段模板并接单
   - U2 推进阶段，U1 确认
   - U2 提交 `resolved`
   - U1 确认 `closed`
   - 测试结束尝试清理测试工单
3. Allure 分类：
   - `场景用例 / 真实后端工单生命周期`
4. 合并进统一 Allure 报告：
   - `场景用例`：2 条（Mock 全链路 + 真实工单生命周期）
   - `单接口用例`：258 条

## 修改文件

- `automation/tests/real/test_ticket_lifecycle_real.py`
- `automation/tests/conftest.py`
- `automation/docs/worklog/task-36-real-ticket-lifecycle.md`

## 验证结果

```text
1 passed in 4.39s
```

真实链路验证通过。

## 风险

- 普通用户调用删除接口只会把工单软关闭，不会物理删除。
- 管理员硬删除接口在测试环境返回 500，开发过程中产生的两条测试工单已通过数据库精确清理。
- 当前测试清理仍需管理员或数据库清理脚本兜底；在清理方案固化前，不应把真实生命周期用例直接接入无人值守 CI。

## 临时清理方案

新增脚本：

- `automation/scripts/cli-cleanup-real-test-data.py`

安全规则：

- 默认只预览，必须显式传 `--execute` 才删除。
- 只接受 `AUTO-*` 前缀，默认清理 `AUTO-LIFECYCLE-%`。
- 只删除匹配的 `tasks.id` 及其已知子表记录。

本地用法：

```powershell
ssh -N -L 19402:127.0.0.1:3306 usp-a@125.122.97.107 -p 8802
$env:REAL_DB_PORT = "19402"
$env:REAL_DB_PASSWORD = "<db-password>"
python automation/scripts/cli-cleanup-real-test-data.py --dry-run
python automation/scripts/cli-cleanup-real-test-data.py --execute
```

已验证：`--dry-run` 能正常连接并输出匹配数量，无数据时不执行删除。

## 下一步

1. 推动后端修复管理员硬删除 500；修复后优先改用产品接口清理。
2. 后端修复前，真实环境测试结束后执行临时清理脚本。
3. 若要让 CI 自动清理，需要为 CI 提供受限 MySQL 清理账号和隧道，或新增测试专用清理接口。
4. 清理方案确认后，再将真实工单生命周期接入 CI。
