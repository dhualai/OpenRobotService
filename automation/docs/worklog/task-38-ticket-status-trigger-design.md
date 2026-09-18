# Task 38：工单状态触发自动化测试设计

> 日期：2026-09-14
> 状态：设计稿，待确认

## 目标

将自动化测试触发从 GitHub 提交切换为工单状态触发：

```text
工单新建 -> in_progress -> resolved -> 触发自动化测试 -> 回写报告
```

## 设计方案

- 一期使用测试环境工单 API 轮询 `resolved` 状态。
- 触发工单必须具备 `metadata_info.automation.enabled=true`。
- 自动化自己创建的测试工单不得触发测试，防止递归。
- 触发器按 `ticket_id + resolved_at + suite` 去重。
- 运行入口首版选测试环境内部 runner，后续支持 GitHub repository_dispatch。
- 运行完成后把 Allure 链接和摘要回写工单。
- 失败只评论告警，不自动改工单状态。
- 支持生产工单只读扫描变体：
  - 生产触发项目固定为 `001` / `摇人吧服务号`。
  - 测试执行项目固定为 `Leo_test` / `摇人吧服务号-测试`。
  - 新建时登记候选并捕获项目/模块/版本/commit。
  - resolved 时触发测试环境执行。
  - 生产工单不回写状态，只评论报告或写内部看板。

## 产出

- `automation/docs/design-ticket-status-trigger.md`

## 依据

- 后端当前没有通用的工单 resolved 自动化事件。
- 后端已有 `ws_broadcast_task_updated` WebSocket 广播，但这不是外部触发器。
- 后端已有 resolved/closed 后调用 AI 清理日志缓存的先例，可作为二期事件钩子参考。

## 待确认

1. `resolved` 的业务语义。
2. 触发单和自动化被测工单如何区分。
3. 测试版本来源。
4. 运行入口选内部服务还是 GitHub Actions。
5. 失败是否阻止 `test -> dev`。
6. 生产工单到测试套件/版本的映射规则。
7. 生产工单回写权限和隐私合规边界。

## 下一步

1. 人工评审设计。
2. 确认触发单字段和 resolved 语义。
3. 确认后再进入轮询触发器实现。
