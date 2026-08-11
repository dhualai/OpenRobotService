# task-20-ci-ai-testgen-real-run.md

## 本次目标

用真实 DeepSeek LLM + 本仓 backend（161 接口）+ 摇人吧 PRD 跑通完整流水线，验证 PRD 驱动用例生成，并修复真实运行暴露的工程问题。

## 过程与修复

| 问题 | 修复 | 验证 |
|------|------|------|
| `import app` 需连 MySQL（本地无库） | extract_api 增加 `AI_EXTRACT_SKIP_DB=1` 开关，导入前 stub `app.core.db`（不影响 CI 真库路径） | 本地提取 161 端点成功 |
| LLM 输出被 max_tokens=1024 截断（分析/用例/脚本均截断） | LLMJudgeClient.complete 支持 max_tokens；pipeline 默认 8192 | 分析完整、脚本可编译 |
| 用例 JSON 单批超长截断（88 REQ 一次生成 20K+ 字符） | 按 5 REQ/批分批生成 + 合并 + 全局 TC 重编号去重 | cases 阶段通过 |
| 脚本超长截断 | 按 25 用例/批生成多文件 test_gen_01~NN.py | 15 文件全部可编译可收集 |
| 摘要不含请求体字段（$ref 未展开）→ gate 误报"字段不存在" | endpoint_summary 展开 $ref/allOf/oneOf/array → 字段列表注入提示词 | gate 审阅字段级准确 |
| LLM 输出带 ``` 围栏 / JSONC 注释 | gates 增加 strip_code_fence / _strip_json_comments；gate 输出同处理 | 解析稳定 |
| gate 审阅输出也超长（15+ 条长 issue） | gate.md 提示词限 10 条 issue、每条 ≤100 字 | 待下轮验证 |
| 归档默认路径错（落到项目根） | parents[2]→parents[1]，归档到 automation/references/generated-cases | ✅ |
| gate 修复后脚本仍坏时提前终止 | 修复后不合格继续循环，轮次耗尽才失败 | 单测覆盖 |

## 真实运行结果（demo-008）

- analyze ✅ 88 REQ；cases ✅ **373 条用例**（166 positive / 95 negative / 67 auth / 39 edge / 6 flow），覆盖 72 REQ
- script ✅ 15 个 pytest 文件；gate ⚠️ 审阅有效（硬编码 ID/字段越界/状态码越界/顺序依赖/认证头缺失），未全部自修复
- 归档：`automation/references/generated-cases/demo-008/`（analysis.md / cases.json / **cases.xlsx 373 行** / test_gen_01~15.py / summary.md）

## 测试结果

```
36 passed (ci_ai_gen/tests/)
288 passed, 28 skipped (全量回归)
```

## 风险与待改进

1. REQ-73~88 个别批次生成失败（偶发截断）→ 批次级失败重试
2. 脚本硬编码 ID（task_001）→ 提示词加强动态资源创建；或执行阶段配合数据准备脚本
3. gate 修复循环成本高（15 文件串行 × 多轮 LLM）→ 并行化或采样审阅
4. 状态码/枚举断言依赖 OpenAPI 响应列表，PRD 期望与 spec 不一致时以 spec 为准（gate 已约束）

## 下一步建议

1. 把 gate 限制后的完整流水线再跑一轮验证收敛（demo-009）
2. CI 接入实测（push backend/ 触发）
3. 批次失败自动重试 + 生成质量抽样评估
