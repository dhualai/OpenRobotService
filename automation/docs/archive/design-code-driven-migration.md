# 设计：代码驱动迁移（自由函数模式，tasks 试点）

> 状态：设计稿，待人工确认后才进入实现
> 决策来源：grill 两轮 8 项确认（Q1~Q8）

## 一、目标

将用例从 Excel 数据驱动迁移为**代码驱动（自由 pytest 函数）**，用 `tasks` 模块（32 条）试点验证后全面推广。解决 Excel 驱动下报告展示受限、git 无法 diff、复杂场景表达困难的问题。

## 二、已确认决策（grill 结论）

| # | 决策 | 结论 |
|---|------|------|
| Q1 | Excel 去留 | 保留为只读清单（由代码反向生成），代码是唯一数据源 |
| Q2 | 用例形态 | **自由 pytest 函数**（每条用例一个 `test_xxx()`） |
| Q3 | ci_ai_gen 流水线 | 本轮不动，后续单独适配 |
| Q4 | 迁移批次 | 渐进式：tasks 试点 → 确认 → 全面迁移 |
| Q5 | 断言方式 | 复用 `automation/src/assertions`（assert_status_code / assert_fields） |
| Q6 | 用例组织 | 按功能 class 分组 |
| Q7 | 报告展示 | **直接用 @allure.feature/story/title 装饰器** + docstring |
| Q8 | executor 去留 | 保留 `src/runner/executor.py` 不动（流水线后续可复用） |

## 三、涉及文件

| 文件 | 改动 |
|------|------|
| 新增 `automation/tests/tasks/test_tasks_code.py` | 试点：32 条用例的自由函数版 |
| 修改 `automation/src/assertions/__init__.py` | 导出确认（assert_status_code / assert_fields 是否可用，缺则补 `assert_fields`） |
| 新增 `automation/scripts/cli-gen-case-inventory.py` | 从代码生成用例清单（Markdown + Excel 只读版） |
| 新增 `automation/docs/design-code-driven-migration.md` | 本文 |
| 新增 `automation/docs/worklog/task-26-code-driven-pilot.md` | worklog |
| `automation/tests/tasks/test_tasks.py`（Excel 驱动版） | 试点期间保留，对比验证 |

## 四、用例写法规范（试点模板）

```python
import allure
import pytest
from automation.src.assertions import assert_status_code, assert_fields

@allure.feature("系统任务")
class TestTaskCrud:
    """工单 CRUD 场景"""

    @allure.story("创建工单")
    @allure.title("正常：创建完整工单")
    @pytest.mark.api
    async def test_create_task_ok(self, mock_api_client, mock_auth_header):
        """正常流程：提交完整工单 -> 200"""
        r = await mock_api_client.post("/api/tasks", headers=mock_auth_header,
                                       json={"title": "Error E1001", "description": "Robot fault"})
        assert_status_code(r, 200)
        assert_fields(r, {"status": "pending"})
```

**规范约定**：
- 函数名 `test_{功能}_{场景}`；class 名 `Test{功能组}`
- `@allure.feature`（模块）写在 class；`@allure.story`（场景组）+ `@allure.title`（用例标题）写在函数
- docstring 第一行 = 覆盖类型 + 说明（供清单生成器提取）
- `@pytest.mark.api` 保留（CI 标记通道）

## 五、用例清单生成方案（Excel 只读）

`cli-gen-case-inventory.py` 用 **pytest 收集**（`pytest --collect-only -q` 或 importlib 扫描约定 class）提取：
- 模块/功能组（class 名 + feature 装饰器）
- 用例名（title 装饰器 / docstring 第一行）
- 覆盖类型（docstring 前缀：正常流程/异常流程/权限/状态流转/数据校验）
- 接口（从函数体内 method+path 提取，或约定 docstring 第二行写接口）

输出：Markdown 清单（`automation/docs/testing/case-inventory-tasks.md`）+ 可选 Excel（`testdata/cases/case-inventory.xlsx`），供评审使用。

## 六、实现步骤

1. **断言核对**：确认 `assertions` 导出 `assert_status_code` + `assert_fields`（无则补 `assert_fields`：递归校验响应字段）
2. **试点用例**：`test_tasks_code.py` 迁移 tasks 全部 32 条（按 Excel 内容逐条转）
   - 验证：`pytest tests/tasks/test_tasks_code.py -m api` 全绿 + 与 Excel 版结果一致
3. **清单生成器**：`cli-gen-case-inventory.py` 收集试点用例 → 生成清单文档
4. **报告对比**：带 `--alluredir` 跑试点 → 展示代码驱动版报告效果（装饰器版 vs Excel 版对照）
5. **人工确认**：你查看报告满意后，进入全面迁移（其他三模块）

## 七、风险分析

| 风险 | 等级 | 缓解 |
|------|------|------|
| 迁移时用例语义走样（payload/断言与 Excel 版不一致） | 中 | 逐条对照 Excel 原文迁移；试点期间双版本并存可交叉验证 |
| 自由函数用例量增加维护成本 | 低 | class 分组 + 装饰器 + docstring 规范固化 |
| 清单生成器提取接口不准 | 低 | 约定 docstring 第二行写接口，生成器优先取该行 |
| Excel 版用例与代码版并存期间的执行冲突 | 低 | 试点文件独立命名（test_tasks_code.py），不改原文件 |

## 八、验收标准

- [ ] `test_tasks_code.py` 32 条用例全部通过，与 Excel 版结果一致
- [ ] 报告展示：feature/story/title 装饰器生效，标题可读、suite 按 class 分组
- [ ] 清单生成器产出 Markdown 清单（模块/功能/场景/覆盖类型/接口）
- [ ] 你确认试点报告效果 → 启动全面迁移
