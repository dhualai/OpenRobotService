# 生成测试用例的 Prompt 模板

将此 prompt 发给 AI，让 AI 按模板生成标准化的测试用例 YAML 文件。

## Prompt

```
请为 OpenRobotService 的 {module_name} 模块生成测试用例 YAML 文件。

## 背景

这是基于 FastAPI + 微信服务号 + AI Agent 的智能运维工单系统。
测试框架采用 Excel 数据驱动（openpyxl + pytest parametrize），
Mock 后端（httpx.MockTransport）提供所有端点模拟。

## 参考信息

当前模块已有场景设计文档：
automation/docs/testing/scenarios/scenarios-{module_name}.md

Mock 后端实现在：
automation/src/mocks/backend_mock.py

## 模板格式

严格按照 automation/testdata/templates/case-template.yaml 的字段定义。
每条用例必须包含完整 10 字段：id, module, method, path, auth, payload, expected_status, expected_fields, note。

## 覆盖要求

每个功能点至少覆盖以下 8 种类型（在 note 字段标注）：
1. 正常流程 - 核心 Happy Path
2. 异常流程 - 参数非法 / 资源不存在 / 重复提交
3. 权限 - 未认证 401 / 越权 403
4. 状态流转 - 合法流转 200 / 非法流转 400
5. 数据校验 - 必填缺失 422 / 字段类型 422 / 长度超限
6. Redis - 缓存相关场景（note 标注）
7. AI - 流式超时 / 降级（note 标注）
8. 数据库 - 约束冲突 / 事务（note 标注）

## 输出要求

输出一个 YAML 文件，内容为一个列表，每个元素是一条用例。
payload 和 expected_fields 使用 YAML 行内 JSON 格式。
```

## 用法示例

```
# 生成后保存到
output/generated-cases/new-call-cases.yaml

# 再执行
python scripts/cli-import-cases.py output/generated-cases/new-call-cases.yaml
```
