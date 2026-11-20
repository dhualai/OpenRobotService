# 设计：真实业务场景用例接入 CI 真实车道（场景 ③④⑦）

- 日期：2026-11-10
- 状态：已确认（用户拍板「要做」）
- 来源：Allure 报告发现 `tests.real.test_ticket_scenarios` 全部 skipped（USE_MOCK=1），追问「为什么没接测试环境」

## 1. 现状与缺口

| 车道 | 触发 | 环境 | 跑什么 |
|---|---|---|---|
| mock 快车道（quality-gate） | push test | USE_MOCK=1 | 全量收集，`tests/real/*` 进 fixture 即 skip（设计如此） |
| 真实车道（deploy-split real-smoke） | push test | USE_MOCK=0 + uvicorn 8400 | 仅 `-m smoke` 6 条冒烟 + OpenAPI 契约冒烟 |
| 真实车道（deploy-split ticket-acceptance） | push test | USE_MOCK=0 + seed + uvicorn 8400 | 仅 cli-ticket-gate.py |

`test_ticket_scenarios.py`（场景 ③ 撤回/暂停/无法解决、④ 打回重开、⑦ 评论+已读回执，标记 `api + e2e`）在两条真实车道都没有被选中 → CI 从未真实执行。

根因：其依赖的三个账号里，`plain_user`（非管理员第三方）没有任何种子脚本创建，CI 空库不存在该账号。

## 2. 设计

### 2.1 种子补齐（backend/scripts/seed_automation_env.py）

- 新增 `SEED_PLAIN_USERNAME`（默认 `plain_user`）/ `SEED_PLAIN_PASSWORD`（默认 `Passw0rd!`，与用例文件默认值一致）。
- `plain_user` 权限为 `permissions=["user"]`（与 backend/app/core/auth_routes.py:122、wechat auth_service.py:75 的普通用户惯例一致），区别于 automation_bot 的 `["admin"]`。
- `seed_user()` 增加 permissions 参数（顺带消除 admin 硬编码）；幂等逻辑不变。
- 输出摘要增加 plain_user 项，失败仍返回 1。

### 2.2 CI 接入（.github/workflows/deploy-split.yml，ticket-acceptance job）

在 `Start backend` 之后、`Prepare SSH key` 之前插入一步：

```yaml
- name: Run real scenario tests（业务场景 ③④⑦）
  env:
    USE_MOCK: "0"
    REAL_API_BASE_URL: http://localhost:8400
    REAL_U1/U2/U3 凭据（secrets/vars 优先，回落与种子一致的默认值）
  run: pytest automation/tests/real/test_ticket_scenarios.py -q --alluredir=automation/output/allure-results-scenarios --junitxml=automation/output/junit-scenarios.xml
```

配套：
- job env 增加 `SEED_PLAIN_USERNAME: "plain_user"`。
- 新增 `Upload scenario test results`（always，allure-results-scenarios + junit-scenarios.xml，独立 artifact，避免与 ticket-gate 的 allure-results 混流）。
- 归属 ticket-acceptance 的理由：该 job 已具备 MySQL service、seed 机制、真实后端，且 continue-on-error 非阻断（沿用 ticket-gate 哲学，场景用例失败先观察不拦部署）。

### 2.3 不做的事（明确边界）

- 不改用例文件中的默认密码回落值（本地开发依赖），凭据优先级改为 CI 环境变量 > 文件默认值；明文密码收敛到 secrets 属后续安全专项。
- 本期不把场景结果聚进 allure-report job（needs 已含 quality-gate/real-smoke，改动面大）；artifact 已可下载分析，聚合列为后续项。
- 不加 pull_request 触发（另议，见 db-tests 观察）。

## 3. 风险

- 场景用例依赖后端启动时初始化 admin（backend/app/core/database.py:163-170，smoke job 已验证可行）。
- ticket-acceptance 每次部署都跑一次场景链路，耗时增加约 1-2 分钟（job timeout 40min 充裕）。
- 普通用户权限语义若与用例预期不符（预期 U3 被拒），首跑可能暴露真实 bug——这恰是用例价值，非阻塞观察期内记录不拦车。

## 4. 验证计划

1. 本地：py_compile + 种子脚本逻辑走查（无本地 MySQL，无法真跑）。
2. CI 预验证：workflow_dispatch（environment=test, git_ref=修复分支）跑 ticket-acceptance job——注意会连带 build/deploy test 环境，需用户确认后执行。
3. 合入后 push test 自动跑，预期 10 条场景用例 passed（或暴露真实缺陷，非阻塞记录）。
