# task-21-ai-eval-p0-veto-badcase.md

## 本次目标

P0 落地 ai-evaluation-skill 方法论两项：**一票否决规则**(平均分不覆盖否决) + **Bad Case 台账闭环**。设计稿：`automation/docs/design-ai-eval-p0-veto-badcase.md`。

## 阅读内容

- `automation/AGENTS.md`、`automation/docs/AI_TESTING.md`、`automation/docs/gap-analysis-ai-eval-skill.md`(本次新增)
- `automation/src/ai_metrics/llm_judge.py` / `faithfulness.py` / `__init__.py`(judge 注入模式参照)
- `automation/tests/ai/runner.py`(_eval_l1/l2/l3 编排)、`testdata/fixtures/ai/diagnosis.json`(golden 结构)
- `automation/scripts/cli-merge-ai-cases.py`(CLI 惯例：--dry-run / .bak 备份)
- YLTsing/ai-evaluation-skill README + SKILL.md(方法论来源)

## 修改文件

| 文件 | 说明 |
|------|------|
| `automation/src/ai_metrics/veto.py` | 新增：否决规则判定（judge 注入；不可解析 → uncertain fail-safe） |
| `automation/src/ai_metrics/tests/test_veto.py` | 新增：9 条自测（FakeJudge） |
| `automation/src/ai_metrics/__init__.py` | 导出 build_veto_prompt / check_veto_rules |
| `automation/tests/ai/runner.py` | EvalResult.veto_pending；_eval_l3 后执行 _eval_veto；失败时 Allure 附 bad-case-suggestion |
| `automation/testdata/fixtures/ai/bad_case_log.json` | 新增：台账空表 |
| `automation/scripts/cli-update-bad-cases.py` | 新增：失败记录合并台账（去重/计数/.bak/--dry-run） |
| `automation/testdata/fixtures/ai/diagnosis.json` | DIAG-001/002 增加 expect.l3.veto_rules（示范） |
| `automation/docs/AI_TESTING.md` | 增补第 11 章：否决规则与 Bad Case 台账 |
| `automation/docs/gap-analysis-ai-eval-skill.md` | 新增：方法论对照分析 |
| `automation/docs/design-ai-eval-p0-veto-badcase.md` | 新增：P0 设计稿 |

未修改：`ai/` `backend/` `frontend/` 任何业务代码。

## 测试结果

```
51 passed (src/ai_metrics/tests/，含新增 9 条 veto 自测，Fast Lane)
377 passed, 28 skipped (全量回归 tests/ + src/ + config/；skip 均为既有优雅跳过：服务不可达/AI 运行时依赖缺失)
cli-update-bad-cases.py --dry-run/实际合并 均验证通过（去重计数、.bak 备份），测试数据已清理恢复空台账
```

## Allure 报告

未单独生成（本轮无 -m api 变更，全量回归通过为准；AI 评估用例在无服务环境按设计 skip）。

## 风险

- veto 每用例多 1 次 judge 调用：仅含 veto_rules 的用例（当前 2 条示范），L3 本就走 `-m judge` 可选路径
- judge 输出不可解析：uncertain fail-safe 标记待复核，detail 含原始输出，不计入具体规则
- 台账仅 CLI 写入：pytest 运行期 testdata 只读，无并发写风险
- 期间踩坑：diagnosis.json 编辑时 oldString 吞掉 expect 闭合行导致 JSON 损坏，已 git checkout 恢复后按准确结构重编，JSON 校验通过

## 下一步建议

1. P1：评测运行版本化（run-id + results.csv）与持续评测触发（需真实 AI 服务）
2. P2：人工复核门禁轻量版（否决候选清单输出 human_review.csv）
3. AI 服务启动后真跑 tests/ai/ 校准 veto 阈值（当前基于契约）
