# UI 测试说明

使用 Playwright Python + Chromium 做 Web UI Smoke。

## 当前范围

第一阶段只验证：

- Vite 前端可访问
- `/login` 页面加载
- 账号、密码输入框和登录按钮可见

用例位置：`automation/tests/ui/test_login_smoke.py`

## 本地运行

```powershell
cd D:\WorkCode\OpenRobotService
npm ci --prefix frontend
npm run dev --prefix frontend -- --host 127.0.0.1 --port 5173

# 另一个终端
$env:PLAYWRIGHT_BASE_URL = "http://127.0.0.1:5173"
$env:PLAYWRIGHT_HEADLESS = "1"
pytest automation/tests/ui -m ui -v
```

首次运行需要安装浏览器：

```powershell
python -m playwright install chromium
```

## 定位策略

- 第一版临时使用 placeholder 和文本定位。
- 后续按 `automation/docs/design-ui-automation-selectors.md` 补 `data-testid`。
- 坐标不作为 Web UI 主定位方式。