# Task 48：UI 场景与真实环境 Smoke 联合 Allure 报告

> 日期：2026-09-19
> 状态：实现完成，联合链路连续两次通过

## 目标

在同一份 Allure HTML 中同时展示真实 UI 业务链路与真实测试环境只读 Smoke：

```text
场景用例
└── UI完整业务链路

单接口用例
└── 真实测试环境 Smoke
    ├── 环境
    ├── 登录
    ├── 摇人问答
    └── 系统任务
```

## 实现内容

1. 新增 6 条真实测试环境只读 Smoke：
   - `GET /api/health`
   - `POST /api/auth/login`
   - `GET /api/auth/me`
   - `GET /api/call/conversations?scene_type=chat&limit=10`
   - `POST /api/tasks/filter`
   - `GET /api/tasks/assignable-users?skip=0&limit=10`
2. Smoke 复用 UI 回归的 SSH 隧道、本地 Gateway 和 U1 凭据。
3. Smoke 只断言状态码和稳定字段，不创建、修改或删除业务数据。
4. 联合运行一次 pytest，生成包含 1 条场景用例和 6 条单接口用例的 Allure。
5. 修复 UI 场景 S05 的偶发点击未触发问题：
   - 监听真实 `ticket/confirm` 请求。
   - 2 秒内没有请求时只重试一次点击。
   - 一旦请求发出，只等待响应，不重复提交。
6. Smoke 每个接口增加三类证据：
   - `接口结果`
   - `响应摘要`
   - `断言结果`
7. UI 场景每个 S 步骤增加 `断言与响应摘要`：
   - 业务步骤执行结果。
   - 各接口方法、路径、HTTP 状态和结果。

## 修改文件

- `automation/tests/ui/test_real_safe_api_smoke.py`
- `automation/tests/ui/conftest.py`
- `automation/tests/conftest.py`
- `automation/src/ui_regression/page_actions.py`
- `automation/src/ui_regression/capture.py`
- `automation/src/ui_regression/tests/test_capture.py`
- `automation/docs/design-ui-regression-combined-report.md`
- `automation/docs/testing/scenarios/ui-regression-smoke.md`

## 验证结果

Smoke 单独运行：

```text
6 passed in 5.45s
```

UI 回归基础单测：

```text
14 passed in 10.08s
```

联合回归连续两次：

```text
7 passed in 25.61s
7 passed in 28.62s
```

报告最终分类：

```text
场景用例：1
单接口用例：6
```

## Allure 报告

已生成：

`automation/output/allure-report-ui-regression-combined/index.html`

核对结果：

- 场景用例位于 `场景用例 / UI完整业务链路`。
- Smoke 位于 `单接口用例 / 真实测试环境 Smoke`。
- Smoke story 分为环境、登录、摇人问答、系统任务。
- Smoke 每个接口包含接口结果、响应摘要和断言结果。
- UI 每个业务步骤包含断言与响应摘要、网络请求和页面截图。
- 最终结果和报告中检索不到 JWT 或原始 `token=` 值。

## 风险

- Smoke 当前复用 UI runtime，单独运行 Smoke 时也会启动 Playwright browser，存在少量额外开销。
- 管理员删除接口仍返回 500，调试运行产生的测试工单累计到 `812` 至 `836`，会话已清理。
- Smoke 暂不覆盖阶段、状态和写入接口，这些继续由 UI 场景覆盖。

## 下一步

1. 获取本次联合报告任务的 ORS 工单号。
2. 提交并推送当前功能分支。
3. 实现本地一键运行脚本和操作文档。
