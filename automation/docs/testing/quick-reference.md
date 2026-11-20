# 速查表

> 常用命令速查。

---

## 一、后端测试

```powershell
# 全部测试（跳过需要 DB 的 task 测试）
cd backend
pytest --ignore=tests/tasks

# 全部测试（含 DB 集成测试，需设置 TEST_DATABASE_URL）
cd backend
pytest

# 指定模块
pytest tests/integrations/

# 指定文件
pytest tests/integrations/test_mapper.py

# 指定测试类
pytest tests/integrations/test_mapper.py::TestZentaoMapper

# 指定测试函数
pytest tests/integrations/test_mapper.py::test_full_sample_mapping

# 指定标记
pytest -m "not slow"

# 详细输出
pytest -v

# 显示 print 输出
pytest -s

# 失败时停在第 1 个失败
pytest -x

# 只收集不执行
pytest --collect-only

# 最常用
pytest --ignore=tests/tasks --alluredir=./allure-results
```

### 依赖安装

```powershell
# 最小依赖
pip install pytest pytest-asyncio httpx

# 含报告
pip install pytest pytest-asyncio httpx allure-pytest

# 完整
pip install -r backend/requirements.txt -r backend/requirements-test.txt
```

---

## 二、前端测试

```powershell
cd frontend

# 运行一次
npm run test

# 监视模式
npm run test:watch

# 带覆盖率
npm run test:coverage

# 指定文件
npx vitest run src/stores/__tests__/auth.test.ts

# UI 模式（浏览器界面）
npx vitest --ui

# 更新快照
npx vitest run --update
```

---

## 三、AI 模块测试

```powershell
cd ai

# 正式测试
pytest tests/test_llm_api.py -v

# 交互脚本
python tests/agent_chat.py
python tests/llm_chat.py
```

---

## 四、Allure 报告

```powershell
cd backend

# 运行 + 收集结果
pytest --alluredir=./allure-results --ignore=tests/tasks

# 生成报告
allure generate ./allure-results -o ./allure-report --clean

# 打开报告
allure open ./allure-report

# 清空结果（重新开始）
Remove-Item -Recurse -Force ./allure-results -ErrorAction SilentlyContinue
```

---

## 五、pytest-html 备用报告

```powershell
pip install pytest-html
cd backend
pytest --html=report.html --self-contained-html --ignore=tests/tasks
```

---

## 六、覆盖率

```powershell
# 后端（需安装 pytest-cov）
pip install pytest-cov
cd backend
pytest --cov=app --cov-report=html --ignore=tests/tasks

# 前端
cd frontend
npm run test:coverage
```

---

## 七、Allure 装饰器速查

| 装饰器 | 用途 | 示例 |
|--------|------|------|
| `@allure.feature` | 功能模块 | `@allure.feature("禅道集成")` |
| `@allure.story` | 子功能 | `@allure.story("字段映射")` |
| `@allure.title` | 用例标题 | `@allure.title("状态映射: {input} → {expected}")` |
| `@allure.severity` | 严重级别 | `@allure.severity(allure.severity_level.CRITICAL)` |
| `@allure.description` | 详细描述 | `@allure.description("使用真实禅道数据验证")` |
| `@allure.step` | 步骤（函数内） | `with allure.step("登录禅道"):` |
| `@allure.tag` | 标签 | `@allure.tag("smoke", "regression")` |
| `@allure.link` | 关联链接 | `@allure.link("http://zentao/task/123")` |

### 严重级别

```python
allure.severity_level.BLOCKER   # 阻塞性
allure.severity_level.CRITICAL  # 严重
allure.severity_level.NORMAL    # 正常
allure.severity_level.MINOR     # 次要
allure.severity_level.TRIVIAL   # 轻微
```

---

## 八、Mock 辅助速查

### 后端

```python
from unittest.mock import patch, MagicMock, AsyncMock

# 函数级 Mock
@patch("app.services.user_service.get_user")
def test(mock_get_user):
    mock_get_user.return_value = {"id": 1}

# AsyncMock
mock_db = AsyncMock()
mock_db.execute.return_value = result

# MagicMock + spec
mock_adapter = MagicMock(spec=TaskSourceAdapter)
```

### 前端

```typescript
import { vi } from 'vitest';

// 函数 Mock
const mockFn = vi.fn().mockReturnValue('value');
const mockAsync = vi.fn().mockResolvedValue({ data: 'value' });

// 模块 Mock
vi.mock('@/api/client', () => ({
  createRequest: vi.fn(() => mockAsync),
}));

// Spy
vi.spyOn(console, 'error').mockImplementation(() => {});
```

---

## 九、目录结构速查

```
# 后端新增测试
backend/tests/{模块}/
├── __init__.py
├── test_{功能}.py
└── conftest.py（可选）

# 前端新增测试
frontend/src/{模块}/__tests__/{文件}.test.{ts,tsx}

# AI 新增测试
ai/tests/
├── __init__.py
├── conftest.py（可选）
├── test_{功能}.py
└── scripts/（交互脚本）
```

---

## 十、相关文档

| 文档 | 路径 |
|------|------|
| 测试总览 | `index.md` |
| 开发工作流 | `development-workflow.md` |
| 目录结构规范 | `directory-structure.md` |
| 命名规范 | `naming-conventions.md` |
| Allure 报告规范 | `allure-report.md` |
