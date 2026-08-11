# QUICKSTART - 快速开始

> 完整规范见 [`automation/README.md`](../README.md) 与 [`automation/AGENTS.md`](../AGENTS.md)。

## 环境准备

```powershell
cd automation
pip install -e .
```

## 常用命令

```powershell
# 全部测试
pytest -v

# 指定模块
pytest tests/call/ -v

# 基础设施测试（Fast Lane，不依赖外部服务）
pytest src/ -v

# API Mock 测试 + Allure 报告
pytest tests/ -m api --alluredir=output/allure-results -v
allure generate output/allure-results -o output/allure-report --clean
```

## 添加测试用例

1. 确认 `src/mocks/backend_mock.py` 已支持该接口
2. 在 `testdata/cases/api-test-cases.xlsx` 对应 sheet 新增一行
3. 运行 `pytest tests/{module}/ -v` 自动参数化执行

## 测试数据与脚本

- 用例 Excel：`testdata/cases/api-test-cases.xlsx`
- 静态数据：`testdata/fixtures/`
- 用例模板：`testdata/templates/`
- 工具脚本：`scripts/cli-*.py`（导入用例 / 初始化 / 生成模块 / 生成报表）
