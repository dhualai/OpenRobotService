# 测试代码 Review 清单

> 提交测试代码前，逐项检查此清单。

---

## 一、结构检查

- [ ] 测试文件放在正确的目录（`backend/tests/{模块}/` / `frontend/**/__tests__/` / `ai/tests/`）
- [ ] 测试子目录有 `__init__.py`（后端/AI）
- [ ] 不改变现有目录结构
- [ ] 测试文件命名符合 `test_{功能}.py` / `{组件}.test.tsx` 规范

---

## 二、测试内容检查

- [ ] 正常路径已覆盖（核心流程 "happy path"）
- [ ] 边界值已覆盖（None、空字符串、空列表、最大值、最小值）
- [ ] 异常路径已覆盖（网络错误、权限不足、参数非法、资源不存在）
- [ ] 每个测试验证单一行为（一个测试一个断言，或一组相关断言）
- [ ] 参数化测试覆盖多组输入输出

---

## 三、隔离性检查

- [ ] 不依赖外部服务（DB / Redis / MinIO / 微信 API / LLM API）
- [ ] 不依赖测试执行顺序（每个测试独立可运行）
- [ ] 不依赖共享状态（全局变量、环境变量残留）
- [ ] 后端测试使用 conftest.py 的模块替换机制阻止 DB 连接
- [ ] 前端测试在 `beforeEach` 中重置 Store 状态
- [ ] 集成测试有条件控制（`@pytest.mark.skipif` 保护）

---

## 四、Mock 检查

- [ ] Mock 的是外部依赖，不是被测函数自身
- [ ] `MagicMock` 指定了 `spec` 参数防止误用不存在的方法
- [ ] `AsyncMock` 用于异步函数
- [ ] Mock 设置有返回值（不返回 `MagicMock` 实例给调用方）
- [ ] 未 Mock 不必要的依赖（尽量少的 Mock 保持测试真实感）
- [ ] 前端 `vi.fn()` 而非 `jest.fn()`（Vitest 语法）

---

## 五、数据检查

- [ ] 测试数据基于真实业务场景，而非随意编写
- [ ] 不含敏感信息（密码、Token、API Key）
- [ ] 不包含多余字段（只放被测逻辑需要的最小数据集）
- [ ] Factory 函数有合理的默认值和 override 参数

---

## 六、断言检查

- [ ] 断言明确（不写 `assert True` / `assert result` 等模糊断言）
- [ ] 使用 pytest 原生断言（不写 `self.assertEqual` 等 unittest 风格）
- [ ] 前端使用 Testing Library 角色查询（`getByRole` / `findByText`）
- [ ] 异常断言精确（`pytest.raises(ValueError)` 而非 `pytest.raises(Exception)`）

---

## 七、Allure 装饰器检查（如适用）

- [ ] 测试类/函数标注了 `@allure.feature` 和 `@allure.story`
- [ ] 重要测试标注了 `@allure.severity`
- [ ] 参数化测试标注了 `@allure.title`（包含参数占位符）

---

## 八、可维护性检查

- [ ] 测试函数名称自描述（见 `naming-conventions.md`）
- [ ] 复杂测试有注释解释测试目的
- [ ] 跨文件复用的数据/工具放在正确位置（`data/` / `utils/`）
- [ ] 不是对源码的简单复制（测试有独立的价值）

---

## 九、运行检查

- [ ] `cd backend && pytest --ignore=tests/tasks` 全部通过
- [ ] `cd frontend && npm run test` 全部通过（如涉及前端）
- [ ] `cd ai && pytest tests/` 全部通过（如涉及 AI）
- [ ] 无 DeprecationWarning（或已确认可接受）
- [ ] 无 `pytest: warnings summary` 中的意外警告

---

## 十、提交前

- [ ] 只包含本次改动的文件（`git status` 确认）
- [ ] 提交信息符合 `test: 描述` 格式
- [ ] 增量测试时，不修改已有测试的逻辑（可用重构工具抽取公共部分）
- [ ] `docs/testing/` 下对应规范文件已同步（如新增测试模式）

---

## 十一、常见拒绝原因

| 原因 | 示例 |
|------|------|
| 测试依赖真实 DB | import 链触发 `Base.metadata.create_all()` |
| 测试未隔离 | 测试 A 创建的数据影响测试 B |
| Mock 不完整 | Mock 设置无返回值，返回 `MagicMock` 实例 |
| 断言模糊 | `assert result`、`assert response.status_code == 200`（无错误信息） |
| 测试数据随意 | `name="test"`, `title="abc"` 无业务含义 |

---

## 十二、相关文档

| 文档 | 路径 |
|------|------|
| 目录结构规范 | `directory-structure.md` |
| 命名规范 | `naming-conventions.md` |
| Fixture 与 Mock 规范 | `fixture-and-mock.md` |
| 测试数据规范 | `test-data.md` |
| Allure 报告规范 | `allure-report.md` |
| 开发工作流 | `development-workflow.md` |
