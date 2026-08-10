# OpenRobotService 自动化测试规范 · 总览

> 本文是测试规范的入口文档。所有新增自动化测试必须遵循本文引用的规范。
> 以此处定义的规范为准，统一后端/前端/AI 三模块的测试实践。

---

## 适用范围

| 模块 | 框架 | 目录 | 当前测试数 | 覆盖率 |
|------|------|------|-----------|--------|
| 后端 | pytest 9.1+ | `backend/tests/` | 58 用例 / 5 文件 | 待统计 |
| 前端 | Vitest 3.2+ | `frontend/src/**/__tests__/` + `frontend/src/test/` | 15 文件 | v8 provider |
| AI | pytest（独立脚本） | `ai/tests/` | 5 脚本（非正式测试） | 无 |

**核心原则**：不改变现有目录位置，只统一开发规范。

---

## 规范文档索引

| # | 文档 | 内容 |
|---|------|------|
| 1 | `directory-structure.md` | 三模块的 tests 目录结构标准 |
| 2 | `naming-conventions.md` | 测试文件、函数、类命名规范 |
| 3 | `fixture-and-mock.md` | Fixture 定义规范、Mock 策略 |
| 4 | `test-data.md` | 测试数据管理规范 |
| 5 | `utilities.md` | 公共测试工具函数集合 |
| 6 | `allure-report.md` | Allure 报告集成规范 |
| 7 | `review-checklist.md` | 测试代码 Review 清单 |
| 8 | `development-workflow.md` | 自动化开发工作流 + AGENT.md 引用 |
| 9 | `quick-reference.md` | 命令行速查表 |

---

## 技术栈速览

### 后端测试栈

| 组件 | 版本 | 用途 |
|------|------|------|
| pytest | ≥9.1 | 测试框架 |
| pytest-asyncio | ≥1.4 | 异步测试支持 |
| httpx | ≥0.27 | API 测试客户端 |
| allure-pytest | ≥2.13 | Allure 报告集成 |
| pytest-html | 可选 | HTML 报告备用 |
| unittest.mock | 内置 | Mock 工具 |

### 前端测试栈

| 组件 | 版本 | 用途 |
|------|------|------|
| vitest | ≥3.2 | 测试框架 |
| @testing-library/react | ≥16 | React 组件测试 |
| @testing-library/jest-dom | ≥6 | DOM 匹配器 |
| @testing-library/user-event | ≥14 | 用户交互模拟 |
| jsdom | ≥26 | 浏览器环境模拟 |
| @vitest/coverage-v8 | ≥3.2 | 覆盖率报告 |

---

## 快速导航

- **新增测试时** → 阅读 `directory-structure.md` + `naming-conventions.md`
- **需要 Mock 时** → 阅读 `fixture-and-mock.md`
- **需要测试数据时** → 阅读 `test-data.md`
- **需要生成报告时** → 阅读 `allure-report.md`
- **提交代码前** → 阅读 `review-checklist.md`
- **查看命令** → 阅读 `quick-reference.md`

---

## 相关外部文档

| 文档 | 路径 |
|------|------|
| 项目架构 | `docs/project_architecture.md` |
| 业务规则 | `docs/business_rules.md` |
| 后端代码总览 | `backend/CODEBASE_OVERVIEW.md` |
| 集成设计文档 | `backend/INTEGRATION_DESIGN.md` |
| 产品需求文档 | `docs/PRD.md` |
| AI Agent 工作流 | `AGENT.md` |
