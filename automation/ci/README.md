# ci/ - CI/CD 集成

## 本地脚本

scripts/ 目录包含以下脚本：

| 脚本 | 用途 |
|------|------|
| `setup-env.bat` | 启动 Docker 测试依赖（MySQL/Redis/Qdrant） |
| `run-fast-lane.bat` | 运行基础设施层测试（Config/Logger/Clients/Assertions/Fixtures） |
| `run-full-lane.bat` | 运行全部 API Mock 测试 |
| `generate-allure-report.bat` | 从 allure-results 生成 Allure HTML 报告并自动打开 |

**使用方式：**

```batch
REM 从项目根目录运行
automation\ci\scripts\run-fast-lane.bat       :: 跑基础测试 + 生成报告
automation\ci\scripts\run-full-lane.bat        :: 跑 API 测试 + 生成报告
automation\ci\scripts\generate-allure-report.bat  :: 单独生成报告
```

> 注意：`generate-allure-report.bat` 需要 Java 17+ 环境。
> 首次使用前请确保已安装 Allure CLI：`npm install -g allure-commandline`

## GitHub Actions

`.github/workflows/test.yml`（已复制到项目根）定义了 CI 流水线：

1. **test-infra** — 基础设施测试 + Upload allure-results
2. **test-api** — API Mock 测试 + Upload allure-results
3. **test-auth** — 真实后端认证测试 + Upload allure-results
4. **report** — 汇总 allure-results → 生成 Allure HTML → 发布到 GitHub Pages

> 合并到 `develop` 分支后自动触发 GitHub Pages 部署。
> Allure 报告地址：`https://<org>.github.io/<repo>/`

## 目录结构

```
ci/
+-- .github/workflows/test.yml       # GitHub Actions workflow 定义（原始副本）
+-- scripts/
|   +-- setup-env.bat                 # Docker 环境启动
|   +-- run-fast-lane.bat             # 快速测试 + Allure 报告
|   +-- run-full-lane.bat             # 全部测试 + Allure 报告
|   +-- generate-allure-report.bat    # Allure 报告生成工具
+-- README.md
```

根目录 `.github/workflows/test.yml` 是 GitHub Actions 自动发现的入口。
`automation/ci/.github/workflows/test.yml` 是设计文档副本，两者应保持同步。
