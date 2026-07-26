# AI 自动化测试平台 - 框架设计 V2

> 本文档定义 utomation/ 目录结构、各模块职责、设计决策，以及已知风险的优化建议。
>
> 适用项目：OpenRobotService（企业级微信服务号）
> 技术栈：Vue3 · FastAPI · MySQL · Redis · Qdrant · AI Agent · DeepSeek
> 立场：不修改任何业务代码目录（i/、ackend/、rontend/、pp/），测试基础设施独立承载于 utomation/。
> 版本：V2 —— 在 V1 基础上扁平化结构、新增 mocks/docker 层、简化 schema 策略。
## 一、设计目标

1. **独立演进** — 测试平台与业务代码解耦，不受业务重构影响，可独立迭代。
2. **分层清晰** — 按测试类型分层（API/UI/AI/DB/E2E），每一层自包含 client、utils、tests，降低认知负担。
3. **共享复用** — 全局 Fixture、数据工厂、Mock 服务、工具函数集中管理，避免各层重复实现。
4. **可观测** — Allure 报告、日志、截图、覆盖率数据统一输出到 output/。
5. **CI/CD 就绪** — 测试编排、环境配置、Docker Compose 全部内置，减少交接成本。

---

## 二、与 V1 的核心差异

| 改进点 | V1 做法 | V2 做法 | 理由 |
|--------|---------|---------|------|
| 目录深度 | pi/client/endpoints/ 三层 | pi/clients/ 两层 | 减少认知负担 |
| Schema 策略 | 独立 pi/schemas/ 目录 | 直接 import backend schema | 避免维护两套模型 |
| UI 结构 | pages/ + components/ | 仅有 pages/ | 减少文件数量 |
| 数据存储校验 | db/mysql/tests/ 嵌套 | db/checkers/ 扁平组织 | 减少目录层级 |
| 报告目录 | 
eports/ | output/ (allure+logs+screenshots) | 更通用 |
| Mock 服务 | 无统一目录 | mocks/ 集中管理 | 微信/LLM mock 需共享 |
| Docker | 在 ci/ 下 | docker/ 独立 compose | 更显眼，便于本地启动 |
| 依赖管理 | 
equirements.txt | pyproject.toml 统一管理 | 现代 Python 实践 |
| CI 配置 | 三个 CI 平台文件 | 单 .github/workflows/ + scripts | 聚焦实际使用的 CI |

---
## 三、目录结构总览

`
automation/
|
+-- README.md                    # 平台概述、快速开始
+-- pyproject.toml               # 依赖声明 + pytest/ruff/mypy 配置
+-- .env.example                 # 环境变量模板
+-- .gitignore
|
+-- config/                      # 全局配置
|   +-- __init__.py
|   +-- settings.py              # 环境感知配置
|   +-- profiles/                # dev/staging/production
|
+-- api/                         # API 自动化
|   +-- __init__.py
|   +-- conftest.py
|   +-- clients/                 # httpx 客户端
|   |   +-- base.py
|   |   +-- wechat.py
|   |   +-- ai_agent.py
|   |   +-- task.py
|   +-- tests/
|   +-- utils/
|
+-- ui/                          # UI 自动化 (Playwright)
|   +-- __init__.py
|   +-- conftest.py
|   +-- pages/                   # Page Object
|   +-- tests/
|   +-- utils/
|
+-- ai/                          # AI 自动化测试
|   +-- __init__.py
|   +-- conftest.py
|   +-- evaluators/              # 质量评估器
|   +-- scenarios/               # 测试场景 YAML
|   +-- tests/
|   +-- utils/
|
+-- db/                          # 数据存储校验
|   +-- __init__.py
|   +-- conftest.py
|   +-- checkers/                # mysql/redis/qdrant checker
|   +-- tests/
|   +-- utils/
|
+-- e2e/                         # 端到端测试
|   +-- __init__.py
|   +-- conftest.py
|   +-- flows/                   # 业务流程步骤
|   +-- tests/
|
+-- mocks/                       # Mock 服务 (V2 新增)
|   +-- __init__.py
|   +-- conftest.py
|   +-- wechat_server.py
|   +-- llm_server.py
|   +-- qdrant_server.py
|
+-- fixtures/                    # 全局 Fixture 和数据
|   +-- __init__.py
|   +-- conftest.py
|   +-- data/                    # YAML/JSON 静态数据
|   +-- factories/               # 数据工厂
|
+-- output/                      # 构建产物 (gitignored)
|   +-- allure-results/
|   +-- allure-report/
|   +-- screenshots/
|   +-- logs/
|
+-- docker/                      # 测试环境容器 (V2 新增)
|   +-- docker-compose.test.yml
|   +-- Dockerfile.test (可选)
|
+-- ci/                          # CI/CD
|   +-- scripts/
|   +-- .github/workflows/
|
+-- utils/                       # 全局工具 (基础设施层)
|   +-- __init__.py
|   +-- logger.py
|   +-- retry.py
|   +-- timer.py
|   +-- helpers.py
|
+-- docs/                        # 测试文档
    +-- QUICKSTART.md
    +-- API_TESTING.md
    +-- AI_TESTING.md
    +-- UI_TESTING.md
`

---

## 四、核心模块职责（V2 更新）

### 4.1 config/ — 配置层
settings.py 读取 AUTOMATION_ENV，自动加载对应 profile。
无 test_matrix.yaml，改为 CI 中 pytest -m 筛选。

### 4.2 api/ — API 自动化
clients/base.py 封装 httpx.AsyncClient。Schema 优先通过 editable install 复用 backend Pydantic schema。
移除 api/schemas/：维护两套 schema 的同步成本大于解耦收益。

### 4.3 ui/ — UI 自动化
Playwright + Page Object。移除 components/：H5+管理面板界面差异大，组件复用场景有限。

### 4.4 ai/ — AI 自动化
评估器返回 0-1 分数。场景 YAML 由 PM 维护。双评估策略：DeepSeek（主）+ Embedding（辅）。

### 4.5 db/ — 数据存储校验
checkers/ 扁平化：mysql_checker.py / redis_checker.py / qdrant_checker.py。

### 4.6 mocks/（V2 新增）
微信回调 mock、LLM API mock（三种模式）、内存 Qdrant mock。

### 4.7 docker/（V2 新增）
docker-compose.test.yml 独立放置，不藏在 ci/ 下。

### 4.8 utils/ 边界
基础设施层（retry/timer/logger）vs 领域层（auth/assertions/screenshot/data_builder）。

---
## 五、pytest 标记约定

| Marker | 归属模块 | 说明 | CI 通道 |
|--------|---------|------|---------|
| api | api/ | API 测试 | Fast Lane |
| ui | ui/ | UI 测试 | Full Lane |
| ai | ai/ | AI 测试 | Full Lane |
| db | db/ | 数据库校验 | Fast Lane |
| e2e | e2e/ | E2E 测试 | Full Lane |
| slow | 任意 | >30s | 单独 Stage |
| smoke | 任意 | 冒烟 | Fast Lane |

Fast Lane: pytest -m smoke_or_api_or_db --alluredir=output/allure-results
Full Lane: pytest -m ui_or_ai_or_e2e --alluredir=output/allure-results

---
## 六、与项目结构的关系

关键边界：
  backend/tests/ = 白盒单元（后端维护）
  automation/api/tests/ = 黑盒集成（测试维护）
  docs/testing/ 现有文档属规范层，本文件属架构层

依赖：pip install -e ../backend 复用 Schema
配置：config/profiles/ 独立管理

---

## 七、风险与优化（V2 更新）

R1（AI 非确定）：评分阈值 + evaluator 单元测试；V2: >0.3 偏差自动归档
R2（数据污染）：SAVEPOINT / Redis 前缀 / temp collection
R3（时间过长）：Fast Lane <5min / Full Lane 日构建
R4（评估偏差）：双评估器；V2: Mock LLM 校验 evaluator
R5（上手成本）：先 Fast Lane 再 Full Lane
R6（Mock 偏差）：V2 新增 - PROBE.md + 健康检查 + Pre-Prod 替换
R7（AI 成本）：V2 新增 - P0 用真实 API / P1+ 用 Mock
R8（定位混淆）：V2 更新 - 白盒/黑盒/E2E 三层独立 Allure 报告

---

## 八、实施路线图

Phase 1 骨架：目录树 + pyproject.toml + config + utils + docker
Phase 2 API+DB：clients/ + checkers/ MVP
Phase 3 Mock：三个 Mock 服务
Phase 4 AI：evaluators/ + scenarios/
Phase 5 UI+E2E：pages/ + flows/
Phase 6 优化：全链路覆盖

---

*本文档随自动化平台迭代持续更新。*
