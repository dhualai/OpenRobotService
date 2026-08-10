# testdata/ - 测试数据

| 目录 | 内容 |
|------|------|
| `cases/` | Excel 测试用例（`api-test-cases.xlsx`，数据驱动核心，每个模块一个 sheet） |
| `fixtures/` | 静态测试数据：`ai/*.json`（AI 评估输入）、`data/*.yaml`（用户/工单/知识库） |
| `templates/` | 用例模板（`case-template.yaml`、`cases-reference.yaml`） |

## 用例 Excel 字段

| 字段 | 说明 | 示例 |
|------|------|------|
| `id` | 用例ID，`{MODULE}-{3位序号}` | `CALL-001` |
| `module` | 对应 sheet 名 | call / tasks / admin / auth |
| `method` | HTTP 方法 | GET / POST / PUT / PATCH / DELETE |
| `path` | API 路径 | `/api/tasks` |
| `auth` | 是否需要认证 | Y / N |
| `role` | 角色（默认 admin） | admin / engineer / customer |
| `payload` | JSON 请求体 | `{"title":"x"}` |
| `expected_status` | 预期状态码 | 200 / 400 / 422 |
| `expected_fields` | 预期返回字段断言（JSON） | `{"status":"pending"}` |
| `type` | 保留字段 | |
| `note` | 覆盖类型 + 说明 | 正常流程 / 权限 / 状态流转… |

## 添加用例

1. 确认 `src/mocks/backend_mock.py` 已支持该接口
2. 在对应 sheet 新增一行（或写 YAML 后用 `python scripts/cli-import-cases.py cases.yaml` 导入）
3. 运行 `pytest tests/{module}/ -v` 验证
