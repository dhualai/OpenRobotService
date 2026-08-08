# 设计:全链路场景用例支持(多步执行 + 变量传递)

> 状态:设计稿(待人工确认) | 作者:automation 测试架构 | 日期:2026-08-07
> 目标:让数据驱动用例能表达并执行"登录 → 建单 → 派单 → 处理 → 评论 → 关闭"类端到端链路

---

## 1. 背景

当前执行器 `automation/src/runner/executor.py::run_case()` 是**单请求模型**:一条用例 = 一个 HTTP 请求,无步骤循环、无变量传递。

后果:
- AI 生成的 373 条用例(demo-008)中 flow 类型仅 6 条,且无法真正执行
- 核心业务链路(转工单 → 派单 → 处理 → 关闭)没有一条端到端用例
- 全链路只能靠人工写独立 pytest 脚本,与数据驱动体系割裂

**关键可行性前提**:MockBackend 是同进程内存态,同一用例内的多步请求共享状态,串联可行,无需真实后端。

## 2. 方案:三层改造

### 2.1 格式层:Excel 新增 `steps` 列(可选)

一行仍是一条用例,但新增可选列 `steps`(JSON 数组),表达完整链路:

```json
[
  {"method": "POST", "path": "/api/tasks", "payload": {"title": "链路测试单", "type": "bug"}, "expected_status": 201},
  {"method": "PATCH", "path": "/api/tasks/{{step1.body.id}}/status", "payload": {"status": "in_progress"}, "expected_status": 200},
  {"method": "GET", "path": "/api/tasks/{{step1.body.id}}", "expected_status": 200, "expected_fields": {"status": "in_progress"}}
]
```

规则:
- 无 `steps` 列或列为空 → 走现有单请求逻辑,**既有 92 条用例零改动**
- 有 `steps` → 每步独立请求+断言;`method/path/payload/expected_status/expected_fields` 列在链路上作为"首步"冗余,仅链路上报头用
- **变量传递**:payload/path 支持 `{{stepN.body.<json路径>}}`、`{{stepN.status}}` 占位符,执行前用前一步响应替换
- 缺省:最后一步的 expected 作为整条用例结果(Allure 断言)

### 2.2 执行层:run_case 多步循环(核心改动)

```
run_case(case):
  steps = case.get("steps")
  if not steps: 走现有单请求路径(不动)
  else:
    ctx = {}
    for i, step in enumerate(steps, 1):
      method/path/payload = 变量替换({{stepN...}} → ctx)
      headers = 按 case.auth/role 登录(首次),后续步骤复用同一 token
      r = client.request(...); attach Allure(request/response, 标 step i/N)
      assert_status_code + expected_fields
      ctx[f"step{i}"] = {"status": r.status_code, "body": r.json()}
```

实现要点:
- 变量替换用正则 `\{\{\s*step(\d+)\.(status|body)\s*(\.?[A-Za-z0-9_\[\]\"'-]*)\s*\}\}`,按路径逐级取值,取不到 → 断言失败并附清晰错误
- 认证:首个需要 auth 的步骤执行 `_auth_for_role` 登录,后续步骤复用 header(避免每步重复登录)
- Allure:每个步骤独立 attach,标题标 `step i/N`

### 2.3 生成层:提示词 + 导出

- `automation/ci_ai_gen/prompts/case_gen.md`:新增"全链路流程"用例类型要求——每个模块至少 1 条核心链路用例(创建→流转→查询),steps 表达,后步引用前步响应字段(用 `{{step1.body.xxx}}` 语法),并给出 1 条完整示例
- `automation/ci_ai_gen/export_xlsx.py`:flow 类型用例导出时,把 steps 全量写入 `steps` 列(不再只取第一步),无 steps 的用例该列为空
- `automation/ci_ai_gen/gates.py`:结构门禁增加 steps 合法性校验(steps 为数组、每步 method/path 必填、占位符 stepN 不超界)

## 3. 涉及文件

| 文件 | 动作 | 说明 |
|------|------|------|
| `automation/src/runner/executor.py` | 改 | 多步循环 + 变量替换 + 认证复用 |
| `automation/src/runner/cases.py` | 改 | steps 列解析为 list |
| `automation/ci_ai_gen/export_xlsx.py` | 改 | flow 用例 steps 全量导出 |
| `automation/ci_ai_gen/prompts/case_gen.md` | 改 | flow 类型规范 + 示例 |
| `automation/ci_ai_gen/gates.py` | 改 | steps 结构门禁 |
| `automation/src/runner/tests/test_executor_multi_step.py` | 新增 | 多步执行/变量/认证复用/兼容 |
| `automation/ci_ai_gen/tests/test_pipeline.py` | 改 | 覆盖 flow 导出与门禁 |
| `automation/AGENTS.md` + skill | 改 | steps 列文档化 |

## 4. 实现顺序(一次一个模块)

1. 执行器多步 + 变量替换 + 单测(核心,独立可验证)
2. cases.py steps 解析 + 单测
3. export_xlsx + gates + 提示词(生成侧闭环)
4. 用 demo-008 flow 用例或手工造 1 条链路用例,全流程验证 pytest + Allure
5. 更新文档 + worklog

## 5. 风险

| 风险 | 对策 |
|------|------|
| 变量取不到导致误报 | 占位符替换失败 → 明确报错(步骤/占位符/实际值),不静默 |
| 多步用例因 mock 状态残留互扰 | Mock 每次测试重置(现有机制),同用例内共享状态是预期 |
| 与现有 92 条用例回归冲突 | 无 steps 走老路径,单测覆盖兼容性 |
| AI 生成的占位符语法错误 | gate 结构校验 + 执行期明确报错,人工修正 |
| 全链路用例偶发因步骤顺序敏感失败 | note 标注"链路顺序敏感,失败需按步骤定位"(Allure 已按步骤 attach) |

## 6. 验收标准

- [ ] 新增单测通过:多步执行 / 变量传递 / 认证复用 / 无 steps 兼容
- [ ] 手工 1 条链路用例(建单→改状态→查询)在 mock 下 pytest 全绿
- [ ] export_xlsx 对 flow 用例输出 steps 列,门禁能拦截非法 steps
- [ ] 全量回归 296 passed 不减
