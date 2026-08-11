# task-19-ci-ai-testgen-archive.md

## 本次目标

为 AI 测试生成流水线增加**用例归档**能力：生成产物归档到 `automation/references/generated-cases/{run_id}/`，并把 AI 用例映射为平台 Excel 正式用例格式，打通"AI 生成 → 人工确认 → 正式用例库"闭环。

## 阅读内容

- `automation/ci_ai_gen/`（P0 + PRD 模式代码）
- `automation/tests/common/test_runner.py`（Excel 列格式：id/module/method/path/auth/role/payload/expected_status/expected_fields/type/note）

## 修改文件

| 文件 | 说明 |
|------|------|
| `automation/ci_ai_gen/export_xlsx.py` | 新增：cases.json → 平台 Excel 格式（openpyxl），状态码提取/JSON payload/req_id 入 note |
| `automation/ci_ai_gen/run_pipeline.py` | `--archive-dir` 参数（默认 references/generated-cases）；run() 末尾 `_archive()` 归档 5 件产物 |
| `automation/ci_ai_gen/tests/test_export_xlsx.py` | 新增 9 条：行格式/状态码提取（含中文前缀 403）/payload/导出 roundtrip |
| `automation/ci_ai_gen/tests/test_pipeline.py` | +2 条：归档产物完整性、无 archive_dir 时跳过 |
| `.gitignore` | 增加 `automation/references/generated-cases/` |
| `automation/docs/ci-ai-test-pipeline.md` | §13 补归档说明 |

未修改业务代码。

## 测试结果

```
34 passed (automation/ci_ai_gen/tests/，新增 11 条)
286 passed, 28 skipped (全量回归)
```

## 设计要点

- Excel 列与正式用例库完全一致，`expected_status` 用 `(?<!\d)([4-5]\d{2})(?!\d)` 从预期结果提取（中文+数字场景），payload 取自首个步骤 testData
- 归档目录 gitignore：CI 产物不入库；人工确认后手动合并 xlsx 到 `testdata/cases/api-test-cases.xlsx` 转正
- 修复：openpyxl 不能写 dict → expected_fields 输出 JSON 字符串 "{}"

## 风险

- 状态码提取是启发式（4xx/5xx），200 默认；异常用例若预期结果是文字描述可能提取不准，人工确认时注意
- payload 仅取第一个步骤，多步骤用例可能不完整

## 下一步建议

1. 实测 workflow 后校验归档 xlsx 质量
2. 人工确认流程固化：可加一个"转正"脚本把 xlsx 行合并进正式用例库（待评估）
