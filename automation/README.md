# automation/ - 自动化测试平台

OpenRobotService 的数据驱动自动化测试框架：**Excel 用例 + Mock 后端 + pytest**。
新增接口测试只需在 Excel 加一行，无需写代码。

## 目录结构

```
automation/
├── src/                       # 框架库（import 调用）
│   ├── runner/                # 数据驱动执行器（load_cases / run_case）
│   ├── clients/               # ApiClient / MySQL / Redis / Qdrant
│   ├── assertions/            # 断言工具
│   ├── fixtures/              # pytest 夹具
│   ├── logger/                # 日志（控制台/文件/Allure）
│   ├── mocks/                 # MockBackend（httpx.MockTransport）
│   ├── ai_metrics/            # AI 评估指标（LLM judge / retrieval recall…）
│   ├── utils/                 # retry / timer / helpers
│   └── conftest.py            # 框架库测试共享夹具
├── config/                    # 配置（环境隔离）
│   ├── enums.py loader.py models.py settings.py paths.py   # 配置加载代码
│   ├── local/ sit/ uat/       # 环境目录，各含 config.yaml
│   └── tests/                 # 配置模块测试
├── tests/                     # 测试用例（按业务模块）
│   ├── call/                  # 我要摇人
│   ├── tasks/                 # 系统任务
│   ├── admin/                 # 后台管理
│   ├── auth/                  # 认证
│   ├── ai/                    # AI 评估测试
│   └── conftest.py            # Mock 后端 + 共享夹具
├── references/               # 原始文档库（PRD / 接口文档 / 原始测试用例，格式不限）
├── testdata/
│   ├── cases/                 # Excel 测试用例（数据驱动核心）
│   ├── fixtures/              # 静态测试数据（yaml/json）
│   └── templates/             # 用例模板
├── scripts/                   # CLI 工具（cli-*.py）
│   └── templates/             # 测试脚本模板
├── ci/                        # CI 本地脚本（workflow 在仓库根 .github/）
├── docker/                    # 测试容器
├── docs/                      # 文档（分析/场景/工作记录）
├── output/                    # 测试产出（gitignored）
├── conftest.py                # 全局钩子：跑完自动生成并弹出 Allure 报告
├── AGENTS.md                  # AI 工作规范
└── pyproject.toml             # pytest 配置
```

## 快速开始

```powershell
cd automation
pip install -e .

# 全部测试
pytest -v

# 指定模块
pytest tests/call/ -v

# 框架库测试（Fast Lane，不连外部服务）
pytest src/ -v

# API Mock 测试 + Allure 报告
pytest tests/ -m api --alluredir=output/allure-results -v
```

> **自动弹出报告**：带 `--alluredir` 跑完后自动 `allure generate` + 启动
> `http://localhost:8080` 并打开浏览器。CI 环境自动禁用；
> 也可设 `ALLURE_AUTO_OPEN=0` 关闭。

## 添加测试用例

1. 确认 `src/mocks/backend_mock.py` 已支持该接口
2. 在 `testdata/cases/api-test-cases.xlsx` 对应 sheet 新增一行
3. `pytest tests/{module}/ -v` 自动参数化执行

## 常用脚本

| 脚本 | 用途 |
|------|------|
| `scripts/cli-import-cases.py` | YAML 用例 → 追加写入 Excel |
| `scripts/cli-init-cases.py` | 一次性引导：内联数据 → 重建 Excel |
| `scripts/cli-generate-test-modules.py` | 扫描 Excel sheet → 生成缺失的测试文件 |
| `scripts/cli-generate-report.py` | 用例 Excel → 按模块导出报表 |
| `scripts/cli-merge-ai-cases.py` | AI 产物（`references/generated-cases/{run_id}/cases.xlsx`）→ 归一化合并进正式 Excel（`--dry-run` 预览） |

详细规范见 `AGENTS.md` 和 `docs/`。
