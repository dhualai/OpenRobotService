# 测试报告规范

> 本文定义 OpenRobotService 项目中测试报告的内容要求、格式规范和归档方式。
> 测试执行策略见 `docs/automation_strategy.md`，编写规范见 `docs/testing_guidelines.md`。

---

## 一、报告工具选择

| 报告类型 | 工具 | 适用场景 | 依赖 |
|----------|------|----------|------|
| **Allure 报告** | allure-pytest + Allure CLI | 正式测试报告、长期趋势跟踪 | Java 17+、Allure CLI |
| **pytest-html** | pytest-html 插件 | 快速查看单次测试结果 | pytest-html |
| **Vitest 终端输出** | vitest | 日常开发验证 | 无 |
| **Vitest 覆盖率报告** | @vitest/coverage-v8 | 覆盖率评估 | 无 |

---

## 二、Allure 报告规范（推荐）

Allure 报告是本项目的**首选正式报告方案**，支持历史趋势、分类、步骤、附件等丰富功能。

### 2.1 运行与生成

```powershell
# 1. 运行测试并收集结果
cd backend
pytest --alluredir=./allure-results --ignore=tests/tasks

# 2. 生成 HTML 报告
allure generate ./allure-results -o ./allure-report --clean

# 3. 打开报告（启动本地 Web 服务器）
allure open ./allure-report
```

### 2.2 输出目录结构

```
backend/
├── allure-results/           # 原始测试结果数据（JSON/XML）
│   ├── {uuid}-result.json    # 每个测试用例的结果
│   ├── {uuid}-container.json # 测试容器（类/模块）
│   └── environment.properties # 环境信息（可手动创建）
└── allure-report/            # 生成的 HTML 报告
    ├── index.html            # 报告首页
    ├── data/                 # 报告数据
    └── plugins/              # 报告插件
```

### 2.3 推荐装饰器用法

```python
import allure

# 标记功能模块
@allure.feature("禅道集成")
@allure.story("字段映射")
class TestZentaoMapper:
    
    @allure.title("测试状态映射：{zentao_status} → {expected}")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize("zentao_status,expected", [
        ("wait", TaskStatus.NEW),
        ("doing", TaskStatus.IN_PROGRESS),
    ])
    def test_map_status(self, zentao_status, expected):
        assert map_status(zentao_status) == expected
    
    @allure.title("测试完整映射样例")
    @allure.description("使用真实禅道任务数据验证完整映射")
    @allure.severity(allure.severity_level.BLOCKER)
    def test_full_sample_mapping(self):
        """验证真实场景下的完整映射正确性"""
        result = zentao_task_to_external(SAMPLE_ZENTAO_TASK)
        assert result.title == SAMPLE_ZENTAO_TASK["name"]
```

### 2.4 添加环境信息

可选：创建 `backend/allure-results/environment.properties` 记录测试环境：

```properties
Python.Version=3.11.7
Backend.Framework=FastAPI 0.139
Database=None (mocked)
OS=Windows
Allure.Version=2.33.0
```

---

## 三、pytest-html 规范（备用方案）

### 3.1 运行

```powershell
pip install pytest-html
cd backend
pytest --html=report.html --self-contained-html --ignore=tests/tasks
```

`--self-contained-html` 将 CSS/JS 嵌入 HTML 文件，单个文件即可分享。

### 3.2 自定义报告标题

```powershell
pytest --html=report.html --self-contained-html \
  --report-title="OpenRobotService 后端测试报告"
```

---

## 四、前端测试报告

### 4.1 终端输出

```powershell
cd frontend
npm run test
```

默认输出格式：
```
✓ tests/integrations/test_mapper.py (29 tests) 512ms
✓ tests/integrations/test_engine.py (12 tests) 128ms
✓ tests/integrations/test_adapter.py (17 tests) 256ms

Test Files  3 passed (3)
     Tests  58 passed (58)
      Time  1.05s
```

### 4.2 覆盖率报告

```powershell
cd frontend
npm run test:coverage
```

输出覆盖 `frontend/src/` 目录，排除 `src/test/` 和 `*.d.ts` 文件。

---

## 五、报告内容要求

### 5.1 每次测试运行应包含

- [ ] 测试执行时间
- [ ] 运行环境（Python 版本、操作系统）
- [ ] 测试总数 / 通过 / 失败 / 跳过 / 错误
- [ ] 失败用例的完整错误信息（traceback）
- [ ] 警告信息汇总

### 5.2 正式发布前应包含

- [ ] Allure 报告的完整 feature/story 分类
- [ ] 覆盖率数据（≥ 目标线）
- [ ] 与前一次运行的对比（新增/修复/回归）
- [ ] 高风险区域标注

### 5.3 报告归档

```powershell
# 按日期归档
mkdir -p reports/$(Get-Date -Format "yyyy-MM-dd")
Copy-Item -Path backend/allure-report -Destination "reports/$(Get-Date -Format 'yyyy-MM-dd')/allure-report" -Recurse
Copy-Item -Path backend/report.html -Destination "reports/$(Get-Date -Format 'yyyy-MM-dd')/"
```

---

## 六、持续集成中的报告

### 6.1 GitHub Actions 集成建议

```yaml
- name: Test Report
  uses: dorny/test-reporter@v1
  if: success() || failure()
  with:
    name: pytest Results
    path: allure-results/*.xml
    reporter: java-junit
```

### 6.2 Allure 报告发布

可使用 `allurectl` 或 GitHub Pages 发布 Allure 报告。
推荐方案：GitHub Pages + `actions/upload-pages-artifact` 发布 `allure-report` 目录。

---

## 七、常见问题

### 7.1 Allure 报告中文乱码

**原因**：Allure CLI 默认编码不支持中文。

**解决**：在运行 Allure CLI 前设置环境变量：
```powershell
$env:JAVA_TOOL_OPTIONS = "-Dfile.encoding=UTF-8"
```

### 7.2 pytest-html 报告内容不完整

**原因**：部分测试信息未捕获。

**解决**：添加 `--capture=sys` 或 `--show-capture=all` 参数：
```powershell
pytest --html=report.html --self-contained-html --capture=sys
```

### 7.3 覆盖率报告路径不对

**原因**：`--cov` 参数未指定正确的源码路径。

**解决**：
```powershell
pytest --cov=app --cov-report=html --ignore=tests/tasks
```

---

## 八、相关文档

| 文档 | 路径 |
|------|------|
| 自动化测试方案 | `docs/automation_strategy.md` |
| 测试开发规范 | `docs/testing_guidelines.md` |
| 功能完成标准 | `docs/done_definition.md` |
| Code Review 清单 | `docs/review_checklist.md` |
| 常见问题排查 | `docs/troubleshooting.md` |
