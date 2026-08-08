# task-21-full-flow-scenarios

## 本次目标

打通全链路场景用例能力:Excel steps 列 + 执行器多步串联 + AI 生成侧 flow 用例规范。

## 阅读内容

- `automation/src/runner/executor.py`(单请求模型)→ 确认无多步能力
- `automation/src/mocks/backend_mock.py`(token 机制 `_make_token`、任务状态机 `_ALLOWED`)
- `automation/ci_ai_gen/export_xlsx.py` / `gates.py` / `prompts/case_gen.md`
- `automation/docs/design-full-flow-scenarios.md`(本任务设计文档)

## 修改文件

| 文件 | 动作 |
|------|------|
| `automation/src/runner/executor.py` | 改:多步执行 `_run_steps` + 占位符解析 `_resolve_placeholders` + 每步 Allure attach + 认证复用;错误统一 `pytest.fail` |
| `automation/src/runner/cases.py` | 改:steps 列 JSON 解析 |
| `automation/src/runner/tests/test_executor_multi_step.py` | 新增:9 条测试(链路/类型保持/认证复用/缺失引用/断言失败/兼容) |
| `automation/ci_ai_gen/export_xlsx.py` | 改:EXCEL_HEADERS 加 steps 列;`case_steps_for_execution` 请求级步骤映射 |
| `automation/ci_ai_gen/gates.py` | 改:`_check_case_steps` flow 用例门禁(≥2 步/请求级字段/占位符不越界) |
| `automation/ci_ai_gen/prompts/case_gen.md` | 改:flow 用例规范 + 可执行步骤示例 |
| `automation/ci_ai_gen/tests/test_export_xlsx.py` | 改:+4 条 flow 导出测试 |
| `automation/ci_ai_gen/tests/test_pipeline.py` | 改:+4 条 flow 门禁测试 |
| `automation/scripts/cli-import-cases.py` | 改:HEADERS 同步 steps 列 |
| `automation/testdata/cases/api-test-cases.xlsx` | 改:4 sheet 加 steps 列头;tasks 新增 TASK-032 全链路用例(建单→处理中→已解决→已关闭) |
| `automation/AGENTS.md` | 改:steps 列规范 + flow 覆盖类型 |
| `.agents/skills/automation-testing/SKILL.md` | 改:steps 列说明 |
| `automation/docs/design-full-flow-scenarios.md` | 新增:设计文档 |

## 测试结果

```
313 passed, 28 skipped in 10.79s   (全量回归,较此前 296 passed 净增 17)
81 passed(API 三模块 Allure 集)
```

新增用例验证:
- `TASK-032` 全链路用例在数据驱动下通过(Excel steps → load_cases 解析 → 多步执行)
- 占位符缺失/越界/字段缺失均明确报错(带用例 ID 与步骤号)

## Allure 报告

已生成:`automation/output/allure-report/index.html`(TASK-032 按步骤展示 Request/Response)

## 风险

| 风险 | 说明 |
|------|------|
| AI 生成占位符语法错误 | gate 拦截 + 执行期明确报错,人工修正 |
| 链路用例顺序敏感 | Allure 已按 step 分开展示,失败可定位到具体步骤 |
| Excel 误覆盖 | 合并/初始化前需备份(`.bak`),本任务已备份 |

## 下一步建议

1. 实现 `cli-merge-ai-cases.py`(AI 产物合并转正工具,设计见 `design-ai-cases-merge.md`)
2. demo-009 用更新后的 case_gen 提示词重跑,验证 flow 用例真实生成
3. 微信模块(wechat sheet + test_wechat.py)决策
