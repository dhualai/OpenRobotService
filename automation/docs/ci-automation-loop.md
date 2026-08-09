# CI 自动化测试闭环

> 本文描述 OpenRobotService 自动化测试的 CI 闭环：**代码上传 → 用例更新 → 测试执行 → 报告发布**。

## 一、闭环全景

```
push 代码到 GitHub（hxg / develop 分支）
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  .github/workflows/（GitHub Actions 自动触发）            │
│                                                         │
│  ┌─────────────┐      ┌──────────────────────────────┐  │
│  │  test.yml   │      │  ai-test.yml                 │  │
│  │  （执行）    │      │  （用例生成）                  │  │
│  └─────────────┘      └──────────────────────────────┘  │
│        │                       │                        │
│        ▼                       ▼                        │
│  test-infra：框架库测试      AI 分析接口变更             │
│  （MySQL/Redis/Qdrant 容器）   → 生成新用例（候选）       │
│  test-api：249 条 API 用例    → gate 审查 + 修复循环      │
│  test-auth：认证用例          → 落 test-gen/ 候选区      │
│        │                       │                        │
│        ▼                       ▼                        │
│  Allure 报告生成 ◄─────── 人工确认后合入 tests/ ───────┐  │
│  （allure-report-action）                              │  │
│        │                                              │  │
│        ▼                                              │  │
│  GitHub Pages 部署（在线查看，保留 20 次历史趋势）      │  │
└─────────────────────────────────────────────────────────┘
```

## 二、Workflow 职责

| Workflow | 触发 | 内容 |
|----------|------|------|
| `test.yml` | push（hxg/develop）/ PR → develop | 三层 job：test-infra（框架库 + 容器服务）→ test-api（API 用例）→ test-auth → report（Allure 生成 + GitHub Pages 部署） |
| `ai-test.yml` | push（hxg/develop）/ PR → develop，后端路径变更 | AI 用例生成流水线（ci_ai_gen）+ 执行 |

## 三、AI 用例生成流水线（ci_ai_gen）

后端接口变更时，`ai-test.yml` 运行四阶段流水线：

```
① analyze  接口规格/PRD → LLM 产出需求分析（analysis.md）
② cases    分析 → LLM 产出用例 JSON（cases.json，分批防超长）
③ script   用例 → LLM 产出框架规范 Python 用例（test_gen.py）
           （httpx + _api + @allure 装饰器 + 断言参数，与框架规范一致）
④ gate     LLM 审查 + 修复循环（最多 2 轮），运行时验证（pytest --collect-only）
```

**门禁原则**：任一阶段失败只记录不阻断（degrade-and-report）；生成脚本通过 `py_compile` + `pytest --collect-only` 双重校验。

## 四、人工确认环节（设计决策）

AI 生成的用例**不自动合入**正式用例库：

```
test-gen/{run_id}/test_gen.py（候选）→ 人工审阅 → 合入 tests/{module}/
```

**原因**：AI 生成代码存在质量波动风险，直接进库可能污染 CI。合入后即被 `test.yml` 自动执行。

可选演进：若对 gate 审查质量有信心，可将"gate 通过 → 自动合入"改为全自动（风险自担）。

## 五、报告发布

- **本地**：`pytest --alluredir=output/allure-results` 跑完自动生成 + 打开浏览器
- **CI**：`allure-report-action` 生成报告 → GitHub Pages（`https://<owner>.github.io/<repo>/`），保留 20 次历史（趋势图）

## 六、相关文件

| 文件 | 用途 |
|------|------|
| `.github/workflows/test.yml` | 测试执行 + 报告发布 |
| `.github/workflows/ai-test.yml` | AI 用例生成 + 执行 |
| `automation/ci_ai_gen/run_pipeline.py` | 生成流水线编排 |
| `automation/ci_ai_gen/prompts/script_gen.md` | 框架规范生成提示词 |
| `automation/ci_ai_gen/gates.py` | 结构门禁校验 |
| `automation/ci/scripts/*.bat` | 本地模拟 CI 通道 |
