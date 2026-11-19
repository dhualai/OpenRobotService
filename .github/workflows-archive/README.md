# workflows-archive — 已停用的 workflow 归档

GitHub Actions 只扫描 `.github/workflows/`，因此把文件移入本目录即为停用（不需要删除配置或改仓库设置）。
恢复方式：`git mv .github/workflows-archive/<file>.yml .github/workflows/` 后提交。

---

## ai-eval.yml（2026-11-19 归档）

**归档原因**：三重失效，从未产生任何运行记录。

1. **push 分支写的是 `[hxg, develop]`**，而开发主战场是 `test` 分支 → 推 test 不触发。
2. **依赖 `vars.AI_EVAL_ENABLED`**，仓库变量中不存在该变量 → gate 步骤恒为 false，
   后续所有 step 的 `if: steps.gate.outputs.enabled == 'true'` 全部跳过。
3. **依赖 `secrets.DEEPSEEK_API_KEY`**，仓库 secrets 中无此名称（实际为 `LLM_API_KEY` /
   `RELEASE_LLM_API_KEY`）→ 即使开启 gate，AI 服务也缺少密钥无法启动。

证据：GitHub 已注册 workflow 列表中不含本文件；`GET /actions/workflows/ai-eval.yml/runs` 返回 404，
说明自创建以来从未运行。

**AI 评估能力并未消失**：`automation/tests/ai/` 与 28 条 golden case（assigner 6 / diagnosis 10 /
rag_retrieval 8 / data_analysis 4）保留，可在本地运行：
`cd automation && pytest tests/ai/ -m ai -v`

**重新启用需要**（缺一不可）：
1. 修正 push 分支为 `test`（或仅保留 schedule / workflow_dispatch）
2. 配置仓库变量 `AI_EVAL_ENABLED=true`
3. 对齐 secret 名称（`DEEPSEEK_API_KEY` ↔ 现有的 `LLM_API_KEY`）
4. 验证 AI 服务可在 CI 内启动（端口 8401，`/api/ai/qa/health` 健康检查通过）

**相关设计文档**（均在 `automation/docs/archive/`）：
- `design-ai-eval-p0-veto-badcase.md`
- `design-ai-eval-p1-runs-ci.md`
- `ci-ai-test-pipeline.md`
