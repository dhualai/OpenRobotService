# Allure 报告规范

> 本文定义 Allure 测试报告的集成和使用规范。

---

## 一、Allure 概述

Allure 是项目**正式测试报告方案**，提供测试结果趋势、分类、步骤、附件等丰富功能。

| 特性 | 说明 |
|------|------|
| 框架插件 | `allure-pytest` 2.13+ |
| 报告引擎 | Allure CLI 2.33+（需 Java 17+） |
| 输出目录 | `backend/allure-results/`（数据）→ `backend/allure-report/`（HTML） |
| 数据格式 | JSON（每个测试用例一个文件） |

---

## 二、安装

### 2.1 Python 包

```powershell
pip install allure-pytest
```

已内置在全局环境（v2.13.5），若 `requirements-test.txt` 未包含则需添加。

### 2.2 Allure CLI

从 https://github.com/allure-framework/allure2/releases 下载，解压后将 `bin/` 加入 PATH：

```powershell
# 验证
allure --version
# 应输出：2.33.0
```

### 2.3 Java 环境

Allure CLI 依赖 Java 17+：

```powershell
java -version
# 应输出：openjdk version "17.x.x"
```

---

## 三、运行与生成

### 3.1 完整流程

```powershell
cd backend

# 1. 运行测试收集结果
pytest --alluredir=./allure-results --ignore=tests/tasks

# 2. 生成 HTML 报告
allure generate ./allure-results -o ./allure-report --clean

# 3. 打开报告（启动 Web 服务器）
allure open ./allure-report
# 默认访问 http://127.0.0.1:54955
```

### 3.2 增量运行（追加结果）

```powershell
# 不清理之前的运行结果
pytest tests/integrations/test_mapper.py --alluredir=./allure-results
pytest tests/integrations/test_engine.py --alluredir=./allure-results

# 合并生成
allure generate ./allure-results -o ./allure-report --clean
```

---

## 四、装饰器规范

### 4.1 装饰器速查

| 装饰器 | 用途 | 必要程度 |
|--------|------|----------|
| `@allure.feature("模块名")` | 标记功能模块 | 推荐 |
| `@allure.story("子功能")` | 标记子功能/用户故事 | 推荐 |
| `@allure.title("自定义标题")` | 自定义测试用例标题 | 推荐 |
| `@allure.severity(level)` | 标记严重级别 | 推荐 |
| `@allure.description("描述")` | 详细描述 | 可选 |
| `@allure.tag("tag1", "tag2")` | 自定义标签 | 可选 |
| `@allure.link("url", "name")` | 关联链接 | 可选 |

### 4.2 严重级别

| 级别 | 含义 | 适用场景 |
|------|------|----------|
| `BLOCKER` | 阻塞性 | 核心链路、登录、建单 |
| `CRITICAL` | 严重 | 功能缺陷、数据错误 |
| `NORMAL` | 正常 | 一般功能验证 |
| `MINOR` | 次要 | 边界值、UI 细节 |
| `TRIVIAL` | 轻微 | 提示信息、格式 |

### 4.3 装饰器使用示例

```python
# tests/integrations/test_mapper.py
import allure

@allure.feature("禅道集成")
@allure.story("字段映射")
class TestZentaoMapper:

    @allure.title("状态映射：{zentao_status} → {expected}")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize("zentao_status,expected", [
        ("wait", TaskStatus.NEW),
        ("doing", TaskStatus.IN_PROGRESS),
    ])
    def test_map_status(self, zentao_status, expected):
        assert map_status(zentao_status) == expected

    @allure.title("完整映射样例（使用真实数据）")
    @allure.description("使用从禅道实际抓取的任务数据验证完整映射流程")
    @allure.severity(allure.severity_level.BLOCKER)
    def test_full_sample_mapping(self):
        result = zentao_task_to_external(SAMPLE_ZENTAO_TASK)
        assert result.title == "【测试】验证派单流程"

    @allure.title("描述为空时使用标题作为描述")
    @allure.severity(allure.severity_level.NORMAL)
    def test_desc_non_empty_used_as_description(self):
        result = zentao_task_to_external({
            "id": 1, "name": "测试", "desc": "",
            "status": "wait", "pri": 2, "type": "devel",
        })
        assert result.description == "测试"
```

---

## 五、环境信息

创建 `backend/allure-results/environment.properties` 记录运行环境：

```properties
Python.Version=3.11.7
Backend.Framework=FastAPI 0.139
Database.Status=mocked
OS=Windows 10
Allure.Version=2.33.0
Pytest.Version=9.1.1
```

---

## 六、CI 集成（推荐配置）

```yaml
# .github/workflows/test.yml
- name: Run tests with Allure
  run: |
    cd backend
    pip install -r requirements-test.txt
    pytest --alluredir=allure-results --ignore=tests/tasks

- name: Generate Allure Report
  uses: simple-elf/allure-report-action@v1
  if: always()
  with:
    allure_results: backend/allure-results
    allure_history: allure-history

- name: Deploy Allure Report to Pages
  uses: peaceiris/actions-gh-pages@v3
  if: always()
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    publish_dir: allure-history
```

---

## 七、注意事项

| 问题 | 解决方式 |
|------|----------|
| 中文乱码 | `$env:JAVA_TOOL_OPTIONS = "-Dfile.encoding=UTF-8"` |
| allure-results 未 gitignore | 确认 `.gitignore` 包含 `allure-results/` 和 `allure-report/` |
| 多次运行结果混合 | 使用 `--clean` 参数或每次运行前清空目录 |
| 报告不显示步骤 | 使用 `@allure.step` 装饰器或在 with `allure.step("步骤名")` 中包裹 |

---

## 八、备用方案：pytest-html

```powershell
pip install pytest-html
cd backend
pytest --html=report.html --self-contained-html --ignore=tests/tasks
```

`--self-contained-html` 将 CSS/JS 嵌入 HTML，单文件即可分享。

---

## 九、相关文档

| 文档 | 路径 |
|------|------|
| 测试总览 | `index.md` |
| 目录结构规范 | `directory-structure.md` |
| Review Checklist | `review-checklist.md` |
| 开发工作流 | `development-workflow.md` |
