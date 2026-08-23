---
name: automation-testing
description: OpenRobotService 自动化测试平台工作流。Use when the user wants to add or update test cases (Excel), extend the Mock backend, run or fix automation tests, generate Allure reports, use the AI pipeline (ci_ai_gen) to generate tests from PRD/OpenAPI, or touch anything under automation/. Covers the data-driven Excel case spec, CLI tools, AI test-generation pipeline, and the mandatory analyze-design-confirm-implement-test-docs workflow.
---

# Automation Testing Platform (OpenRobotService)

本 skill 是 `automation/` 自动化测试平台(Excel 数据驱动 + Mock 后端 + AI 生成流水线)的入口。

**权威规范**:`automation/AGENTS.md`(完整 572 行规范,本 skill 是其摘要,冲突时以它为准)
**修改边界**:`.claude/SKILLS/代码修改约束/AI代码修改边界Skill.md`(禁止修改 `ai/` `backend/` `frontend/` 业务逻辑)

---

## 1. 强制工作流(每次任务必须)

```
① 阅读 automation/AGENTS.md → ② 阅读 automation/docs/ + 根 docs/ → ③ 阅读相关源码
→ ④ 输出分析文档到 automation/docs/ → ⑤ 等待人工确认(设计阶段)
→ ⑥ 实现(一次只改一个模块,≤10 文件) → ⑦ pytest 验证 → ⑧ Allure 报告 → ⑨ 更新 worklog
```

禁止:跳过分析直接写码、一次实现整个模块、不更新 `automation/docs/worklog/`。

## 2. 目录速查

```
automation/
├── src/            # 框架库:runner(数据驱动) clients(API/MySQL/Redis/Qdrant) fixtures assertions logger mocks(MockBackend) ai_metrics utils
├── config/         # 环境配置 local/sit/uat
├── tests/          # call(我要摇人) tasks(系统任务) admin(后台管理) auth(认证) ai(AI 评估)
├── ci_ai_gen/      # AI 测试生成流水线(prompts/ + run_pipeline.py + extract_api.py + gates.py)
├── testdata/cases/api-test-cases.xlsx   # ★ 用例唯一权威,数据驱动核心
├── scripts/        # cli-*.py 工具
├── docs/           # testing/analysis(7要素分析) testing/scenarios(8覆盖场景) worklog/ 任务记录
└── output/         # Allure 报告(gitignored)
```

## 3. 常用命令(在 automation/ 目录执行)

```powershell
# 全部测试
cd automation; pytest -v

# 指定模块
cd automation; pytest tests/call/ -v

# 框架库快速验证(不带 Allure)
cd automation; pytest src/ config/ -v

# API Mock 测试 + Allure 报告
cd automation; pytest tests/ -m api --alluredir=output/allure-results -v
cd automation; allure generate output/allure-results -o output/allure-report --clean

# CLI 工具(从 automation/ 目录)
python scripts/cli-import-cases.py <cases.yaml>        # YAML 用例 → Excel
python scripts/cli-generate-test-modules.py            # Excel sheet → pytest 模块
python scripts/cli-generate-report.py                  # 用例 Excel → 报表 xlsx

# AI 用例转正合并(AI 产物 → 正式 Excel,半自动闭环最后一环)
python scripts/cli-merge-ai-cases.py --run-id demo-008 --dry-run   # 预览合并计划
python scripts/cli-merge-ai-cases.py --run-id demo-008             # 合并(自动备份 .bak)
```

> 注意:`cli-init-cases.py` 无参数直接运行会重建/覆盖 Excel,仅用于初始化,勿在生产用例上执行。

## 4. Excel 用例规范(api-test-cases.xlsx)

每个模块一个 sheet。字段:`id`(模块前缀+序号) `module` `method` `path` `auth` `role` `payload` `expected_status` `expected_fields` `type` `note`。

8 种覆盖类型(写入 `note`):正常流程 / 异常流程 / 权限 / 状态流转 / 数据校验 / Redis / AI / 数据库(+ 全链路 `flow`)。

**steps 列(全链路用例)**:Excel 可选列,JSON 数组表达多步链路,每步 `{method, path, payload, expected_status, expected_fields}`;`{{stepN.body.<字段>}}` / `{{stepN.status}}` 引用前步响应(只允许引用已执行步骤);无 steps 走单请求路径(既有用例零改动)。例:`TASK-032` 建单→处理中→已解决→已关闭。

添加新用例流程:
1. 确认 `src/mocks/backend_mock.py` 已支持该接口(不支持先扩 Mock)
2. 场景设计输出到 `automation/docs/testing/scenarios/`
3. Excel 对应 sheet 新增一行
4. `pytest tests/{module}/ -v` 自动参数化执行(用例与代码分离)

默认 Mock 用户:testadmin/admin123(admin)、engineer/eng123(engineer)、customer/cust123(customer)。

## 5. AI 生成流水线(ci_ai_gen)

两种驱动模式,从仓库根执行:

```powershell
# PRD 驱动(当前初版):PRD → 功能点(REQ) → 用例(cases.json/xlsx) → pytest 脚本 → gate 门禁
python -m automation.ci_ai_gen.run_pipeline --spec-dir <openapi目录> --out-dir <输出目录> --run-id <id> --prd <prd路径>

# 接口驱动(后期目标):OpenAPI → 分析 → 用例 → 脚本
python -m automation.ci_ai_gen.run_pipeline --spec-dir <openapi目录> --out-dir <输出目录> --run-id <id>
```

- 四角色:`analyzer`(分析)→ `case-gen`(用例)→ `script-gen`(脚本)→ `gate`(门禁审阅,2 轮修复上限)
- 产物归档:`automation/references/generated-cases/{run_id}/`(含 cases.xlsx 已映射平台格式)
- **闭环规则**:AI 产物不直接入库;审阅确认后再将 `cases.xlsx` 内容合并进 `testdata/cases/api-test-cases.xlsx` 转正
- 设计文档:`automation/docs/ci-ai-test-pipeline.md`;CI 触发:`.github/workflows/ai-test.yml`

## 6. 文档要求

- 分析文档:7 要素(功能点/业务流程/状态流转/权限矩阵/接口列表/风险点/边界条件),参考 `automation/docs/testing/analysis/analysis-{module}.md`
- 场景设计:8 覆盖类型,输出到 `automation/docs/testing/scenarios/`
- 任务记录:每次任务更新 `automation/docs/worklog/task-NN-*.md`(目标/阅读/修改文件/测试结果/风险/下一步)

## 7. 禁止事项

- 修改 `ai/` `backend/` `frontend/` 业务逻辑(按边界 skill 流程申请)
- 在根 `docs/` 下创建自动化测试专属文档(只放全项目共用文档)
- 未分析直接实现 / 跳过测试 / 一次改 >10 文件 / 删除已有文档
- 在 `backend/tests/` `ai/tests/` 下创建自动化测试文件
