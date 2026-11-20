# 设计：AI 评测 P1 — 运行版本化 + 持续评测触发

> 日期：2026-08-12 | 状态：设计稿（待人工确认）
> 依据：`automation/docs/gap-analysis-ai-eval-skill.md` 第 4 节 P1 项
> 范围：`automation/` 内 10 个文件以内 + 仓库根 `.github/workflows/ai-eval.yml`，不触碰 `ai/` `backend/` `frontend/`

---

## 1. 目标

1. **评测运行版本化**：每次 `pytest tests/ai/` 自动生成独立运行记录 `output/ai-eval-runs/{run-id}/`（run.json 元数据 + results.csv 长表），支持同一 golden 集的跨版本对比（prompt/知识库/检索阈值变更前后各跑一次 → 量化回归）
2. **版本对比工具**：`cli-compare-ai-runs.py` 输出用例级状态迁移 + 指标级回归清单，可作门禁（发现回归 → 退出码 1）
3. **持续评测触发**：新增 `.github/workflows/ai-eval.yml`（定时 + 手动 + 相关路径变更触发），服务可用才真跑，否则优雅 skip 并在报告注明；默认由仓库变量门控

## 2. 涉及文件清单

| # | 文件 | 动作 | 说明 |
|---|------|------|------|
| 1 | `automation/src/ai_metrics/run_recorder.py` | 新增 | 运行记录器（纯 Python，Fast Lane 可测）：registry + dump run.json/results.csv |
| 2 | `automation/src/ai_metrics/tests/test_run_recorder.py` | 新增 | 自测 ≈8 条 |
| 3 | `automation/src/ai_metrics/__init__.py` | 修改 | 导出 RunRecorder |
| 4 | `automation/tests/ai/runner.py` | 修改 | 4 个 run_*_case 记录结果入 recorder（含 suite、skipped_all） |
| 5 | `automation/tests/ai/conftest.py` | 修改 | `pytest_sessionfinish` 钩子：会话结束 dump 运行记录（无记录/`AI_EVAL_NO_RUN=1` 时跳过） |
| 6 | `automation/scripts/cli-compare-ai-runs.py` | 新增 | 版本对比：--base/--new/--latest-two/--fail-on-regression |
| 7 | `.github/workflows/ai-eval.yml` | 新增 | 持续评测 workflow |
| 8 | `automation/docs/AI_TESTING.md` | 修改 | 增补第 12 章：运行版本化与持续评测 |
| 9 | `automation/docs/worklog/task-22-ai-eval-p1-runs-ci.md` | 新增 | 任务记录 |

**不改**：`tests/ai/test_*.py` 用例文件、`ai_metrics` 各指标模块、Mock 后端、既有 workflow。

## 3. 模块职责

### 3.1 `run_recorder.py`

```
class RunRecorder:
    start()                       # run_id = env AI_EVAL_RUN_ID 或 auto run-YYYYmmdd-HHMMSS
    record(case_id, suite, passed, skipped_all, veto_pending, checks, responses_summary)
    dump(run_root) -> run_dir    # 写 run.json + results.csv
```

- `results.csv` 长表：`run_id, case_id, suite, passed, veto_pending, n_checks, n_failed, failed_metrics, skipped_all, ts`（case×run 一行，语义对齐 skill 长表）
- `run.json`：run_id / started_at / finished_at / counts(total|passed|failed|skipped) / env(AI_EVAL_BASE_URL, judge 可用性) / golden_fingerprint(suite → 用例数, 检测 golden 变更)
- run_id 来源：`AI_EVAL_RUN_ID` 环境变量（版本对比时显式命名，如 `before-prompt-v2`）或自动时间戳
- 纯函数式 dump，可单测（临时目录）

### 3.2 runner 集成

- `run_ai_case(client, case, suite)` 增加 suite 参数；`run_rag_case(case, suite)` / `run_assigner_case(case, suite)` / `run_analysis_case(client, case, suite)` 同理
- 各函数结果写入 recorder（module 级单例，与 `_judge_cache` 同风格）
- `skipped_all=True`（RAG/派单依赖缺失）→ 记录为 skipped 行，不占失败
- 测试文件调用处补 suite 参数：diagnosis / rag / assigner / data_analysis

### 3.3 conftest 钩子

```python
@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    recorder.dump(RUNS_ROOT)   # RUNS_ROOT = output/ai-eval-runs
```

- 无记录（未执行 AI 用例）→ 不写目录；`AI_EVAL_NO_RUN=1` → 跳过
- 目录写失败仅警告，不改变退出码

### 3.4 `cli-compare-ai-runs.py`

```
usage: cli-compare-ai-runs.py --base <run-id|path> --new <run-id|path> [--fail-on-regression]
       cli-compare-ai-runs.py --latest-two [--fail-on-regression]
```

- 输出：每用例状态迁移表（PASS→FAIL = 回归 / FAIL→PASS = 修复 / 新增失败 / 消失失败）+ 指标级回归清单（case_id + metric）
- golden_fingerprint 不一致 → 警告"golden 已变更，对比仅作参考"
- `--fail-on-regression`：存在回归 → 退出码 1（CI 门禁用）

### 3.5 `ai-eval.yml`（持续评测）

```yaml
on:
  schedule: [{cron: "0 20 * * 5"}]        # 每周五 20:00 UTC
  workflow_dispatch:
  push: {paths: [ai/core/**, ai/agents/**, ai/ingestion/**, ai/docs/**, automation/testdata/fixtures/ai/**, automation/src/ai_metrics/**]}
```

- 服务：redis + qdrant（对齐 ai-test.yml 风格；mysql 非必需）
- 步骤：checkout → setup-python 3.11 → `pip install -r ai/requirements.txt; pip install -e automation/` → 门控检查（`AI_EVAL_ENABLED` 仓库变量 ≠ true → skip 并注明）→ 启动 AI 服务（`uvicorn ai.app.main:app` 或项目实际入口，后台运行）→ 健康检查 → `pytest tests/ai/ --alluredir` → 上传 Allure + run 记录 artifact
- 失败处理：AI 服务未起 → 用例优雅 skip，不红 CI；有真实失败 → 红 CI + 日志
- 与 ai-test.yml（AI 生成用例）互不干扰，独立命名

## 4. 测试用例变更

### 4.1 `test_run_recorder.py`（≈8 条）

| # | 场景 | 断言 |
|---|------|------|
| 1 | record + dump 生成 run.json | 元数据字段齐全，counts 正确 |
| 2 | dump 生成 results.csv | 表头正确，case×run 行数正确 |
| 3 | 显式 run_id（env） | run 目录名与 run.json.run_id 一致 |
| 4 | skipped_all 记录 | 计入 skipped，不计 failed |
| 5 | veto_pending 记录 | 字段透传 |
| 6 | golden_fingerprint | suite→用例数正确 |
| 7 | 无记录 dump | 返回 None，不写文件 |
| 8 | failed_metrics 摘要 | 失败 metrics 以分号连接 |

### 4.2 `cli-compare-ai-runs.py` 自测（并入 4.1 或独立 4 条）

- 构造 base/new 两个 run 目录 → 迁移表分类正确；fingerprint 不一致警告；--fail-on-regression 退出码

### 4.3 本地验证

```powershell
cd automation; pytest src/ai_metrics/tests/ -q                          # Fast Lane
cd automation; $env:AI_EVAL_RUN_ID='demo-1'; pytest tests/ai/ -q        # 生成 output/ai-eval-runs/demo-1/
cd automation; python scripts/cli-compare-ai-runs.py --latest-two        # 对比
```

## 5. 实现步骤

| 步骤 | 内容 | 验收 |
|------|------|------|
| 1 | run_recorder.py + test_run_recorder.py + 导出 | Fast Lane 全绿 |
| 2 | runner 4 函数集成 + test_*.py 补 suite + conftest 钩子 | `pytest tests/ai/ -q` 后 output/ai-eval-runs/ 出现记录 |
| 3 | cli-compare-ai-runs.py + 自测 | 迁移表/门禁正确 |
| 4 | ai-eval.yml | 语法检查（actionlint 不可用则人工 review + 文档说明） |
| 5 | AI_TESTING.md 增补 + worklog + 全量回归 | 全部通过 |

## 6. 风险分析

| 风险 | 等级 | 缓解 |
|------|------|------|
| pytest_sessionfinish 钩子与全局 conftest 冲突 | 中 | trylast + 无记录跳过 + 异常不改变退出码 |
| 每次测试写磁盘 | 低 | output/ 已 gitignored；单次 <100KB |
| CI 无 AI 服务 | 中 | 优雅 skip + 变量门控；先本地真跑校准 |
| golden 变更导致对比失真 | 中 | fingerprint 警告 + 文档说明 |
| compare 门禁误伤 | 低 | --fail-on-regression 显式开启才红 CI |

## 7. 待确认决策

| # | 问题 | 建议 |
|---|------|------|
| 1 | ai-eval.yml 是否本期启用（变量默认值） | 默认 false（CI 无 AI 服务环境），先本地跑通，等基础设施就绪再开 |
| 2 | compare 门禁是否接 CI | 本期不接（同上），CLI 本地/手动使用 |
