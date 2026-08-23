# 自动化开发工作流

> 本文定义新增自动化测试的开发流程，以及 AGENT.md 中与此文档的关系。
> AGENT.md 负责 Agent 的通用工作流程，本文聚焦测试开发的详细步骤。

---

## 一、AGENT.md 与本文件的关系

```
AGENT.md                          ← Agent 通用工作流程（入口）
└── 引用 → docs/testing/index.md  ← 测试规范总览（本文集入口）
              ├── directory-structure.md
              ├── naming-conventions.md
              ├── fixture-and-mock.md
              ├── test-data.md
              ├── utilities.md
              ├── allure-report.md
              └── review-checklist.md
```

**分工**：
- `AGENT.md` — 定义 Agent 在项目中的通用操作流程（如何分析、修改、提交），包含测试操作速查
- `docs/testing/development-workflow.md` — 定义**新增测试**的完整开发步骤，本文
- `docs/testing/` 下其他文件 — 定义各专项规范

---

## 二、新增测试完整流程

```
Step 1: 分析被测对象           → 确认测试范围和目录
Step 2: 选择测试类型           → 单元/集成/API/DB
Step 3: 创建测试文件           → 按目录结构规范
Step 4: 编写测试逻辑           → 按命名/Mock/数据规范
Step 5: 添加 Allure 装饰器     → 按报告规范
Step 6: 运行验证               → 通过 + 无警告
Step 7: 提交 Review            → 按 Review Checklist
```

---

## 三、Step-by-Step 详解

### Step 1：分析被测对象

```python
# 1. 确认被测函数所在位置和依赖
# 2. 列出需要 Mock 的外部依赖
# 3. 列出输入/输出/边界值
```

**参考规范**：`directory-structure.md`、`naming-conventions.md`

### Step 2：选择测试类型

| 被测对象特征 | 推荐测试类型 | 目录 |
|-------------|-------------|------|
| 纯函数/映射 | 单元测试 | `tests/{模块}/test_{功能}.py` |
| API 端点 | API 集成测试 | `tests/{模块}/test_{端点}_api.py` |
| 数据库操作 | DB 集成测试 | `tests/{模块}/test_{功能}_db.py` |
| 前端组件 | 组件测试 | `{模块}/__tests__/{组件}.test.tsx` |
| 前端 Store | Store 测试 | `stores/__tests__/{store}.test.ts` |

### Step 3：创建测试文件

选择对应的目录和文件名，参考 `directory-structure.md` 创建。

### Step 4：编写测试逻辑

**后端单元测试模板**：

```python
"""禅道映射器测试"""
import pytest
from app.integrations.sources.zentao.mapper import map_status, map_priority
from app.models.task import TaskStatus, TaskPriority


class TestMapStatus:
    """状态映射测试"""

    @pytest.mark.parametrize("input,expected", [
        ("wait", TaskStatus.NEW),
        ("doing", TaskStatus.IN_PROGRESS),
        ("", TaskStatus.NEW),
        (None, TaskStatus.NEW),
    ])
    def test_normal_and_boundary(self, input, expected):
        assert map_status(input) == expected
```

**前端组件测试模板**：

```typescript
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Component } from '../Component';

describe('Component', () => {
  it('renders with default props', () => {
    render(<Component />);
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('calls onClick when clicked', async () => {
    const onClick = vi.fn();
    render(<Component onClick={onClick} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
```

**参考规范**：`naming-conventions.md`、`fixture-and-mock.md`、`test-data.md`、`utilities.md`

### Step 5：添加 Allure 装饰器

```python
import allure

@allure.feature("我的模块")
@allure.story("我的子功能")
class TestMyFeature:

    @allure.title("测试场景描述")
    @allure.severity(allure.severity_level.NORMAL)
    def test_scenario(self):
        ...
```

**参考规范**：`allure-report.md`

### Step 6：运行验证

```powershell
# 后端
cd backend
pytest tests/{模块}/test_{文件}.py -v

# 前端
cd frontend
npx vitest run src/{模块}/__tests__/{文件}.test.tsx

# AI
cd ai
pytest tests/test_{文件}.py -v
```

### Step 7：提交 Review

按 `review-checklist.md` 逐项检查。

---

## 四、新增测试文件检查清单

- [ ] 目录符合 `directory-structure.md`
- [ ] 文件名符合 `naming-conventions.md`
- [ ] 测试函数名符合 `naming-conventions.md`
- [ ] Mock 策略符合 `fixture-and-mock.md`
- [ ] 测试数据符合 `test-data.md`
- [ ] 公共工具优先使用 `utilities.md` 中定义的函数
- [ ] Allure 装饰器符合 `allure-report.md`
- [ ] 通过了 `review-checklist.md` 的自检

---

## 五、不同场景的工作流

### 场景 A：为已有模块补测

```
1. 查看现有测试文件 → 确认测试风格
2. 确定补测的目标模块和函数
3. 在现有 `tests/{模块}/` 下新增文件
4. 复用现有的 conftest.py fixture
5. 遵循已有测试的编写风格
```

### 场景 B：新增功能 + 测试

```
1. 实现功能代码
2. 根据功能类型选择测试类型
3. 创建测试文件
4. 编写测试 → 验证通过
5. 运行全量测试确认无回归
```

### 场景 C：修复 Bug + 加回归测试

```
1. 先编写复现 Bug 的测试（预期失败）
2. 修复 Bug 代码
3. 验证测试通过（红 → 绿）
4. 补充边界值测试防止类似问题
```

---

## 六、项目根 `AGENT.md` 的维护

`AGENT.md` 应该保持轻量，只维护：

1. **基本操作流程**（分析 → 实现 → 验证 → 提交）
2. **命令行速查**（运行测试、生成报告的命令）
3. **文档索引**（指向 `docs/testing/` 和各业务文档）

所有**详细的规范内容**应放在 `docs/testing/` 下，`AGENT.md` 只做引用。

---

## 七、相关文档

| 文档 | 路径 |
|------|------|
| AGENT.md（通用工作流） | `AGENT.md` |
| 测试总览 | `index.md` |
| 速查表（常用命令） | `quick-reference.md` |
| 目录结构规范 | `directory-structure.md` |
| Review Checklist | `review-checklist.md` |
