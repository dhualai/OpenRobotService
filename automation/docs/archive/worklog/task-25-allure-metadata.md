# task-25 · Allure 报告标准化（元数据 + 参数友好化 + 本地历史）

## 本次目标

按 Allure 官方标准补齐报告元数据层（此前仅 Steps/Attachments/Labels，缺 Environment/Executor/Categories/History），解决"报告不标准"问题。

调研依据：Allure 官方文档（allurereport.org/docs，17 个 feature）+ 本地报告结构实测差距。

## 差距 → 落地对照

| 官方要素 | 此前状态 | 本次落地 |
|----------|----------|----------|
| Environment 环境信息 | ❌ 无 | ✅ `environment.properties`（AUTOMATION_ENV / mock\|real 模式 / base_url / Python / 平台 / 运行时间） |
| Executor 执行者 | ❌ 无 | ✅ `executor.json`（本地 / GitHub Actions + 运行 URL，按环境变量自动识别） |
| Categories 缺陷分类 | ❌ 无 | ✅ `categories.json`（认证失败 / 资源不存在 / 参数校验失败 / 状态冲突 / 连接超时 / 产品缺陷 / 测试代码错误） |
| Parameters 友好化 | ⚠️ 整个 case dict | ✅ 追加友好参数：用例ID / 覆盖类型 / 接口 / 预期状态（全链路用例显示"链路 N 步串联"） |
| History 本地趋势 | ❌ 本地无 | ✅ conftest 生成报告前自动复制 `allure-report/history → allure-results/history` |
| Steps 步骤树 | ✅ 上轮已补 | 不变 |

## 修改文件列表

- 新增 `automation/src/reporting/__init__.py`、`automation/src/reporting/metadata.py`（元数据生成，任何异常不阻断测试）
- `automation/conftest.py`：`pytest_configure` 钩子写元数据；`_open_allure_report` 增加历史复制
- `automation/src/runner/executor.py`：`_attach_allure_meta` 追加友好参数（dynamic.parameter）
- `automation/AGENTS.md`：新增「报告元数据」说明小节
- 新增 `automation/docs/worklog/task-25-allure-metadata.md`（本文）
- 尝试过未采用：`parametrize("case:exclude")` 移除原始 case 参数——pytest 9 收集期报错（"function uses no argument 'case:exclude'"），allure-pytest 2.13.5→2.16.0 升级后仍不支持该语法，回退为 `case`（参数区显示 case + 4 个友好参数，可接受）

## 测试结果

```
# Allure 通道（四模块）
130 passed in 5.35s

# 框架库（allure-pytest 升级至 2.16.0 后回归）
170 passed in 5.75s

# 元数据验证
environment.properties → Environment widget 6 项 ✓
executor.json         → Executor widget (Local) ✓
categories.json       → 全部通过时 Categories 为空（规则已生效，失败即归类）✓
TASK-001 参数         → 用例ID/覆盖类型/接口/预期状态 ✓
```

## Allure 报告

已生成：`automation/output/allure-report/index.html`（130 用例，含 Environments/Executor/Categories 数据）

## 风险

- allure-pytest 升级 2.13.5 → 2.16.0：框架库 170 回归通过，无破坏；pyproject.toml 未锁版本，CI 环境将自动拉取新版
- pytest 9 不支持 `:exclude` 参数排除语法（官方 issue 层面未合入），原始 case 参数无法移除，以友好参数补充方案替代

## 下一步建议

- 失败场景验证 Categories 归类（构造一次失败用例跑报告看分类效果）
- Assertion diff 结构化（断言失败时报告显示 expected/got 差异，需改断言工具）
- CI 报告页启用 flaky/retry 分析（pytest-rerunfailures + history 已具备基础）
