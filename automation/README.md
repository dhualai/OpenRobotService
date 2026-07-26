# OpenRobotService — AI 自动化测试平台

## 概述

本平台是 OpenRobotService（企业级微信服务号）的 AI 自动化测试基础设施。
独立承载于 utomation/ 目录，不修改任何业务代码（i/、ackend/、rontend/、pp/）。

## 目录结构

`
automation/
+-- config/       # 全局配置（环境感知 settings + profiles）
+-- api/          # API 自动化（httpx 客户端 + 黑盒测试）
+-- ui/           # UI 自动化（Playwright + Page Object）
+-- ai/           # AI 自动化（evaluator + scenario 分离）
+-- db/           # 数据存储校验（MySQL / Redis / Qdrant）
+-- e2e/          # 端到端测试（跨模块关键路径）
+-- mocks/        # Mock 服务（微信 / LLM / Qdrant）
+-- fixtures/     # 全局 Fixture 和数据工厂
+-- output/       # 构建产物（allure + screenshots + logs）
+-- docker/       # 测试环境容器编排
+-- ci/           # CI/CD 配置和脚本
+-- utils/        # 全局工具（retry / logger / timer）
+-- docs/         # 测试文档
`

## 测试分层

| 层级 | 测试类型 | CI 通道 | 执行频率 |
|------|---------|---------|---------|
| Fast Lane | API + DB | 每次提交 | < 5 分钟 |
| Full Lane | UI + AI + E2E | 日构建 | < 30 分钟 |

## 快速开始

`ash
# 1. 安装依赖
cd automation
pip install -e ../backend  # 复用 backend Schema
pip install -e .           # 安装测试框架依赖

# 2. 启动测试环境
docker compose -f docker/docker-compose.test.yml up -d

# 3. 运行测试
pytest -m smoke_or_api_or_db --alluredir=output/allure-results
`

## 设计文档

详见 [docs/testing/framework-design.md](../docs/testing/framework-design.md)
