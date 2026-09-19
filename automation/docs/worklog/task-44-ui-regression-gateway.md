# Task 44：本地 UI 回归 Gateway 与双 SSH 隧道

> 日期：2026-09-19
> 状态：阶段 2 实现完成

## 目标

实现本地 UI 回归运行入口：

- 本地提供前端静态页面。
- `/api/ai/*` 转发到自动化 AI `9411`。
- 其他 `/api/*` 转发到测试后端 `9400`。
- 统一管理测试后端和自动化 AI 两条 SSH 隧道。

## 修改文件

- `automation/src/ui_regression/__init__.py`
- `automation/src/ui_regression/config.py`
- `automation/src/ui_regression/gateway.py`
- `automation/src/ui_regression/tunnels.py`
- `automation/src/ui_regression/tests/__init__.py`
- `automation/src/ui_regression/tests/test_gateway.py`
- `automation/src/ui_regression/tests/test_tunnels.py`

## 实现内容

1. 新增环境配置 `UiRegressionConfig`。
2. 新增本地 Gateway：
   - SPA 静态文件服务。
   - React Router 路径回退到 `index.html`。
   - `/api/ai/*` 分流到 automation AI。
   - 其他 `/api/*` 分流到测试后端。
   - 保留方法、查询参数、请求体和 Authorization。
   - 支持 SSE 流式响应。
3. 新增 `UiTunnelManager`：
   - 复用现有 `SSHTunnel`。
   - 同时启动 `19400 -> 9400` 和 `19411 -> 9411`。
   - 启动失败时自动回收已启动隧道。
4. 新增 Gateway 和隧道单测。

## 验证结果

```text
7 passed in 21.58s
```

真实 SSH 隧道验证：

```text
backend 200
ai {"status":"ok","service":"controlled-ai-api","mode":"api-only","background_workers":0}
```

真实 Gateway 验证：

```text
page 200 <html>gateway-ok</html>
backend 200 {"status":"healthy",...}
```

## Allure 报告

已生成：

- `automation/output/allure-report-ui-regression/index.html`

## 风险

- 当前 Gateway 未实现 WebSocket 代理。UI 场景允许返回列表重新打开或刷新页面同步状态。
- Gateway 启动时会检查测试后端和自动化 AI 健康接口，任一不可用时拒绝启动。
- 真实 UI 场景尚未执行，本阶段只验证运行链路和代理行为。

## 下一步

1. 获取本次提交使用的 ORS 工单号。
2. 提交阶段 2 代码。
3. 进入阶段 3，补充前端关键控件 `data-testid`。
