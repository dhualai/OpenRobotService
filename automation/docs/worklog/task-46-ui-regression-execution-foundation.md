# Task 46：UI 回归执行基础能力

> 日期：2026-09-19
> 状态：阶段 4A 实现完成

## 目标

为完整 Playwright 业务链路准备执行基础设施：

- 浏览器真实网络捕获。
- 敏感字段脱敏。
- 每步截图和 Allure 附件。
- 测试数据尽力清理。
- 页面高层动作封装。

## 修改文件

- `automation/src/ui_regression/__init__.py`
- `automation/src/ui_regression/capture.py`
- `automation/src/ui_regression/cleanup.py`
- `automation/src/ui_regression/page_actions.py`
- `automation/src/ui_regression/tests/test_capture.py`
- `automation/src/ui_regression/tests/test_cleanup.py`
- `automation/src/ui_regression/tests/test_page_actions.py`

## 实现内容

1. `NetworkCapture`：
   - 按业务步骤收集浏览器实际请求和响应。
   - 记录接口顺序、状态码、耗时、请求体和响应体。
   - 自动脱敏 password、token、Cookie、Authorization。
   - 响应体最大保留 20 KB。
   - 每步附加网络 JSON 和页面截图到 Allure。
2. `CleanupManager`：
   - 管理员账号清理测试工单。
   - U1 账号清理测试会话。
   - 清理失败只返回告警，不抛异常覆盖业务结果。
   - 清理结果附加到 Allure。
3. `UiPageActions`：
   - 登录。
   - 新建会话和发送问题。
   - 打开/确认工单草稿。
   - 进入系统任务、搜索和打开工单。
   - 确认接单、推进阶段、提交已解决和确认关闭。

## 验证结果

```text
12 passed in 20.46s
```

覆盖：

- 敏感字段和 Bearer Token 脱敏。
- 工单和会话清理。
- 清理失败告警。
- 缺少清理凭据时不抛异常。
- 阶段默认结束时间格式。

## Allure 报告

已生成：

- `automation/output/allure-report-ui-regression-4a/index.html`

## 风险

- `UiPageActions` 目前只完成单元级辅助函数验证，尚未在真实浏览器中执行完整点击链路。
- 页面定位器已在阶段 3 加入，实际可用性将在阶段 4B 验证。
- 当前清理使用产品 API，管理员硬删除失败时只记录告警。

## 下一步

1. 获取阶段 4A 提交使用的 ORS 工单号。
2. 提交并推送阶段 4A。
3. 进入阶段 4B，新增 U1/U2 fixtures 和完整业务链路场景。
