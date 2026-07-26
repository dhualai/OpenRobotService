# UI 模块测试计划

## 概述

UI 测试基于 Playwright + Page Object 模式，覆盖前端关键页面。
当前实现了登录页面的 Page Object 和测试用例。
后续可扩展至工单看板、微信 H5 等页面。

## 已实现架构

```
ui/
├── conftest.py           → Playwright browser + page fixtures
├── pages/
│   └── login_page.py     → LoginPage PO（navigate/login/is_logged_in）
├── tests/
│   └── test_login.py     → 3 个登录测试用例
├── utils/
│   ├── device.py         → 5 种视口预设（mobile/tablet/desktop）
│   └── screenshot.py     → 截图工具（存磁盘）
├── README.md
└── pages/ 待实现
    ├── admin_login.py    ⬜
    ├── task_board.py     ⬜
    └── wechat_h5.py      ⬜
```

## 已实现的 Page Object

### LoginPage

| 方法 | 说明 |
|------|------|
| `navigate()` | 跳转到 /login |
| `login(username, password)` | 填写表单并提交 |
| `is_logged_in` | 登录后 URL 不含 /login |
| `current_url` | 当前页面 URL |

## 已实现的测试用例

| # | 测试函数 | 覆盖点 |
|---|---------|--------|
| 1 | test_login_page_loads | 登录页加载，URL 包含 /login |
| 2 | test_login_empty_fields_stays_on_login | 空字段提交后仍在登录页 |
| 3 | test_login_invalid_credentials | 无效凭据后未跳转 |

## 待实现的 Page Object

| Page Object | 方法规划 | 优先级 |
|-------------|----------|--------|
| AdminLoginPage | navigate/login/verify | P1 |
| TaskBoardPage | view_tasks/filter_tasks/click_task | P1 |
| WeChatH5Page | 微信 H5 页面操作 | P2 |

## 已知问题

| 问题 | 影响 | 建议修复 |
|------|------|----------|
| 无 Playwright 可用性守卫 | 环境无 Playwright 时 pytest 崩溃 | 加 `pytest.importorskip` |
| 前端不可达时不 skip | CI 上超时失败 | 在 fixture 里做连通性检查 |
| 未接入 automation 框架 | 无统一配置/日志/断言 | 接入 config + logger |
| 截图不附到 Allure | 报告中看不到截图 | 加 `allure.attach()` |

## 运行方式

```bash
# 需要先启动前端 dev server（localhost:5173）
# 启动后端 dev server（localhost:8000）
cd frontend && npm run dev

# 运行 UI 测试
pytest automation/ui/tests/ -v

# 指定设备视口
pytest automation/ui/tests/ -v --device mobile
```

## 当前状态

**状态**：✅ 已实现并提交（5f5a0a7）- 登录流程
**测试数**：3 用例（1 个 Page Object）
**依赖**：Playwright + 浏览器 binary、前端 dev server（:5173）、后端（:8000）
**下一步**：
1. 修 P2 守卫问题（Playwright 可用性 + 前端可达性）
2. 实现 TaskBoardPage / AdminLoginPage
3. 接入 automation 框架（config/logger）
4. 补充移动端视口测试
