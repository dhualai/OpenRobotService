# 测试用例模板 - 使用指南

## 文件说明

| 文件 | 用途 |
|------|------|
| `case-template.yaml` | 字段定义 + 编写规则（**AI 生成新用例时以此为准**） |
| `cases-reference.yaml` | 从现有 Excel 导出的完整参考用例库（按模块分类） |

## 字段速查

| Excel 列 | YAML 字段 | 类型 | 说明 |
|----------|-----------|------|------|
| id | id | string | `{MODULE}-{3位序号}`，留空则自动填充 |
| module | module | string | call / tasks / admin / auth |
| method | method | string | GET / POST / PUT / PATCH / DELETE |
| path | path | string | API 路径 |
| auth | auth | string | `"Y"` 或 `"N"` |
| payload | payload | object/null | 请求体 JSON；GET/DELETE 为 `null` |
| expected_status | expected_status | int | 期望 HTTP 状态码 |
| expected_fields | expected_fields | object/null | 期望响应字段 |
| note | note | string | 覆盖类型 + 功能点描述 |

## 使用流程

```bash
# 1. AI 按 case-template.yaml 生成 YAML 用例文件
# 2. 写入 Excel
python scripts/cli-import-cases.py path/to/new-cases.yaml

# 3. 自动执行
pytest tests/ -v
```
