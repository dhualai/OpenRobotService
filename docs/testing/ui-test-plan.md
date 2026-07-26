# UI 模块测试计划

## 概述

UI 测试基于 Playwright + Page Object 模式，用于验证前端页面行为。
当前模块目录 `automation/ui/` 已规划骨架结构，尚未实现。

## 规划架构

```
ui/
├── conftest.py        → Playwright browser/page fixture
├── pages/             → Page Object 类
│   ├── admin_login.py → 后台登录页
│   ├── task_board.py  → 工单看板页
│   └── wechat_h5.py   → 微信 H5 页面
├── utils/
│   ├── device.py      → 设备/视口配置
│   └── screenshot.py  → 截图工具
└── tests/
    ├── test_login/     → 登录流程测试
    └── test_task_flow/ → 工单操作测试
```

## 待实现功能

| 功能 | 优先级 | 前置依赖 |
|------|--------|----------|
| Playwright 集成（conftest + browser fixture）| P0 | — |
| 登录 Page Object | P0 | `automation/clients/` |
| 工单看板 Page Object | P1 | login PO |
| 截图比对比工具 | P1 | — |
| 移动端视口配置 | P2 | — |
| 微信 H5 页面测试 | P2 | 微信后端环境 |

## 当前状态

**状态**：⬜ 骨架就绪，待实现
**测试数**：0
**依赖**：Playwright 浏览器、后端运行中
**建议开始条件**：API 测试全部通过 + CI 就绪
