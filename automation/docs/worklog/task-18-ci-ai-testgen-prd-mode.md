# task-18-ci-ai-testgen-prd-mode.md

## 本次目标

在 P0 流水线基础上，实现**PRD 驱动的用例生成**初版：需求来源从"接口规格反推"改为"产品需求文档（PRD）功能点驱动"，后期框架稳定后再切回接口驱动。

## 阅读内容

- `automation/references/prd/摇人吧产品功能文档（知识库版）.md`（644 行：三大模块/状态机/权限矩阵/派单机制）
- `automation/ci_ai_gen/`（P0 代码）、`automation/docs/ci-ai-test-pipeline.md`

## 修改文件

| 文件 | 说明 |
|------|------|
| `automation/ci_ai_gen/prompts/analyzer.md` | PRD 模式：五标题结构（含功能点清单 REQ-xx 编号规范、状态流转、权限矩阵） |
| `automation/ci_ai_gen/prompts/case_gen.md` | 按功能点生成：req_id 关联、新增 flow/auth 用例类型 |
| `automation/ci_ai_gen/gates.py` | `check_analysis(prd_mode)` 五标题校验；`extract_req_ids`；`check_cases_req_coverage`（REQ×用例映射门禁） |
| `automation/ci_ai_gen/run_pipeline.py` | `prd_path` 配置；`_load_prd`（优先 spec_dir/prd.md）；analyze 五标题门禁 + REQ 缺失校验；cases 覆盖度门禁；`--prd` CLI |
| `automation/ci_ai_gen/extract_api.py` | `--prd` 参数：PRD 文档/目录 → spec_dir/prd.md |
| `automation/ci_ai_gen/tests/test_pipeline.py` | +9 条：REQ 提取/覆盖度门禁/PRD 模式 happy path/无编号失败/覆盖缺口失败 |
| `.github/workflows/ai-test.yml` | extract 步骤传 `--prd automation/references/prd` |
| `automation/docs/ci-ai-test-pipeline.md` | §13 补充 PRD/接口双模式对照表 |

未修改业务代码。

## 测试结果

```
24 passed (automation/ci_ai_gen/tests/，含 9 条新增)
276 passed, 28 skipped (全量回归)
```

## 设计要点

- 双模式架构：PRD 驱动只影响 analyze/cases 输入与门禁；script_gen/gate 完全复用（脚本仍需 OpenAPI，PRD 用例引用的接口须在规格内）
- REQ 编号契约：analyzer 必须输出 REQ-xx；case_gen 用例必须带 req_id；门禁校验双向（无编号→分析失败；无覆盖→用例失败）
- 切换成本：后期切接口驱动仅改 analyzer 输入与 check_analysis 模式参数

## 风险

- PRD 是功能文档（含前端），用例生成时依赖提示词约束"以可测试接口为准"，可能需要人工校准
- PRD 全文 45KB 直接注入 prompt，token 较大；如超限需加摘要层（后续优化）

## 下一步建议

1. 推 backend/ 变更实测 workflow，校验 PRD 模式真实生成质量
2. PRD 超长时增加分块/摘要预处理
3. 后期按计划切换接口驱动并对比生成质量
