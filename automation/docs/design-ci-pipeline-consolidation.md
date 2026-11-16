# 设计：CI 流水线收敛方案（回归"push test 一条主线"）

- **日期**：2026-11-16
- **状态**：方案待确认，未改任何 workflow
- **触发原因**：用户指出设计意图是"推代码到 test 分支即跑自动化"，但现有 8+3 条流水线是演进堆积，主次颠倒
- **关联**：`design-mock-vs-real-lanes.md`（双车道体系）、`analysis/analysis-redundancy-and-gaps.md`（用例缺口）

---

## 一、现状（远端实测，2026-11-16）

### 1.1 触发规则核对

| 流水线 | 分支 | 自动触发 | 手动 | 真实用途 |
|---|---|---|---|---|
| `ci-guard` | test | ✅ 仅改 `.github/workflows/deploy-split.yml` 时 | ✅ | 保护 workflow 合法性（防 step 写错致全瘫） |
| `db-tests` | test | ✅ 改 automation/tests、backend models 等路径时 | ✅ | 真实 MySQL 集成 17 条 |
| `ui-smoke` | test/develop | ✅ 改 frontend/ui 路径时 | ✅ | Playwright 登录冒烟 |
| `deploy-split` | 验证分支 | ⚠️ 仅 `chore-cicd-split-deploy-verify`（不是 test） | ✅ | **11 jobs：门禁+测试+场景+构建+部署+回滚**，事实上的主流水线 |
| `ui-regression` | — | ❌ | ✅ | UI 链路+真实环境（SSH 隧道，重） |
| `ai-eval` | hxg/develop + 定时周五 | ⚠️ 分支不对 | ✅ | AI L1/L2/L3 评估 |
| `deploy` / `rollback` | — | ❌ | ✅ | 手动运维（旧版直接部署） |
| main: `test` | hxg/develop | ✅ 但非 test | ❌ | 旧主测试（连败 6×） |
| main: `ai-test` | hxg/develop | ✅ 但非 test | ❌ | AI 生成测试（连败 6×） |

### 1.2 问题诊断

1. **主次颠倒**：最完整的 `deploy-split`（含 API/DB 之外的几乎全部测试）不随 push test 自动跑，反而手动 dispatch；自动跑的是三条局部流水线。
2. **测试被切碎**：同一批 push，db-tests、ui-smoke、ci-guard 各自按路径触发 2-3 条，结果分散、无统一入口。
3. **部署线重复**：`deploy`（旧）+ `deploy-split`（新）+ `rollback` + `ci-guard`（保护）四层演进堆积，`deploy` 未下线。
4. **僵尸流水线**：main 的 `test`/`ai-test` 指向 hxg/develop，连败 6 次无人管；`ui-regression` 停更 09-24。
5. **UI 双轨**：smoke 自动 + regression 手动，因 regression 需 SSH 隧道连真实环境、耗时长。

---

## 二、设计目标与原则

**目标**：回归"push test → 一条主线跑完整自动化"，手动流水线仅保留运维与重场景。

**原则**：
- **不破坏现有部署架构**：deploy-split 的分离构建/部署、非阻塞门禁、回滚机制保持不动。
- **不删历史能力**：僵尸流水线先"冻结/归档"，验证新主线稳定后再删。
- **一次只改一处**：每步改完做结构级校验（每个 step 有且仅有 `uses` 或 `run`），防止重蹈 PR #454 覆辙。
- **测试与部署解耦判定**：push test 只跑"测试门禁"，真部署仍需人工确认——避免 push 即上线。

---

## 三、目标流水线结构（收敛后）

```
push 到 test
     │
     ▼
┌─────────────────────────────────────────────┐
│ 主线：test-gate.yml（新建）                       │
│  jobs:                                       │
│   ├─ structure-guard（workflow 合法性）       │
│   ├─ quality-gate（框架库 + API mock）        │
│   ├─ db-integration（真实 MySQL，-m db）      │ ← 并入（环境轻）
│   ├─ real-smoke（真实后端 smoke + contract）  │
│   ├─ scenarios（业务场景 6 条）               │
│   └─ allure-report（汇总，含 db 结果）        │
└─────────────────────────────────────────────┘
     │ 全绿
     ▼
（人工决定部署）→ deploy-split（手动 dispatch，含完整 11 jobs）

独立保留（不并入主线）：
  ├─ ui-smoke.yml  —— 环境最重（Playwright 浏览器 + npm ci + dev server），
  │                    失败隔离价值高，继续按前端路径触发
  ├─ ui-regression.yml —— 手动，SSH 隧道连真实环境
  ├─ ai-eval.yml —— 定时/手动评估
  └─ deploy / rollback —— 运维
```

> **折中决策（2026-11-16 用户确认）**：db-tests 并入主线（独立 job，保留非阻塞），
> ui-smoke **不并入**——它要装 Playwright 浏览器 + npm ci + 起 dev server（3-5 分钟），
> 塞进主线会拖慢并放大"环境写错致全瘫"的风险面；保留独立按路径触发，失败隔离更清晰。

**手动/定时保留**（不属于 push 主线）：
- `deploy-split`：部署（含自己的门禁，作为部署前二次确认）
- `deploy` / `rollback`：运维
- `ui-regression`：重场景（SSH 隧道）
- `ai-eval`：定时评估

---

## 四、逐条处置建议

| 流水线 | 处置 | 动作 | 风险 |
|---|---|---|---|
| `ci-guard` | **保留** | 合并进主线的 structure-guard job，或保留独立（它只盯 workflow 文件变更，触发成本低） | 低 |
| `db-tests` | **并入主线** | 把 `db-integration` job 逻辑复制进主线（含 mysql service + create_all + `continue-on-error`）；原文件先保留但去掉 push 触发，避免双跑 | 中（DB service 配置） |
| `ui-smoke` | **保留独立（不并入）** | 继续按 frontend/ui 路径触发；环境最重（浏览器+npm ci+dev server 3-5min），失败隔离价值高；主线全绿判定不含它 | 低 |
| `deploy-split` | **改为手动专属** | 去掉 push 验证分支触发（本就不针对 test），专心做部署 | 低 |
| `ui-regression` | **保留手动** | 停更问题单独修；不进主线（SSH 隧道太重） | 低 |
| `ai-eval` | **保留+修分支** | 把 push 分支从 hxg/develop 改 test，或纯定时 | 低 |
| `deploy` | **冻结→归档** | 与 deploy-split 重复，确认无引用后删除 | 中（需确认无外部调用） |
| `rollback` | **保留但归属 deploy-split** | deploy-split 已有 rollback job，独立版可归档 | 中 |
| main `test` / `ai-test` | **归档** | 连败 6×、非 test 分支，无维护价值；先 disable workflow 文件保留 30 天 | 低 |

---

## 五、实施路线（分 3 批，每批可独立回滚）

### 第一批：新建主线（不动现有）
1. 新建 `.github/workflows/test-gate.yml`（test 分支），从 deploy-split 复制 quality-gate/real-smoke/scenarios 三个 job 定义。
2. 加 db-integration job（从 db-tests.yml 复制，含 mysql:8.0 service、`create_all`、`continue-on-error: true`）。
3. 加 allure-report 汇总 job（聚合 db 结果）。
4. **不复制 ui-smoke**——保留其独立流水线。
5. **结构级校验**：逐 job 检查每个 step 有且仅有 uses 或 run（防 PR #454 事故重演）。
6. push test 验证：确认主线全绿；ui-smoke 仍独立触发正常。

### 第二批：关停重复触发
1. `db-tests.yml` 去掉 push 触发（保留 workflow_dispatch 供单独排查）——主线已覆盖。
2. `ui-smoke.yml` **保持 push 触发不变**——它是独立快速反馈，不进主线。
3. `ci-guard.yml` 保留（防 workflow 写错，成本极低）。
4. `ai-eval.yml` 修 push 分支为 test。
5. 观察一周：确认主线 + ui-smoke 覆盖全部原能力。

### 第三批：归档僵尸
1. main 的 `test.yml` / `ai-test.yml`：先 `workflow_dispatch` 之外全部去掉，30 天后删文件。
2. `deploy.yml` / `rollback.yml`：确认无外部调用后归档（deploy-split 已含）。
3. 更新 `docs/README.md` 与领导版报告的流水线清单。

---

## 六、风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 改 workflow 致整个文件非法（历史事故） | **高** | 每 step 结构级校验（uses/run 二选一）；新建文件而非原地改；push 前用 ci-guard 预检 |
| 主线 job 太多导致单条流水线超时 | 中 | 设 job 级 timeout；DB/UI 可加 `continue-on-error` 不阻塞主线结论 |
| push test 即部署的误触发 | 高 | 主线**不含 build/deploy job**；部署仍手动 dispatch |
| 关停 db-tests 后 DB 回归漏检 | 中 | 第二批前确认主线 db-integration job 稳定运行 ≥3 次 |
| 删除僵尸流水线影响其他分支 | 中 | 先 disable（改 trigger 为 workflow_dispatch），30 天无异议再删 |
| self-hosted runner 资源占用 | 中 | 主线全部用 hosted runner；deploy-split 的 self-hosted 保持手动 |

---

## 七、与用户设计意图的对照

用户原话："只有推代码到 test 分支才需要跑自动化流水线"。

- ✅ 收敛后：push test → `test-gate.yml` 一条主线跑完全部自动化测试（API/DB/场景/UI 冒烟）。
- ✅ 部署、回滚、UI 重回归、AI 评估均为手动/定时，不随 push 触发——符合"自动化 ≠ 部署"的边界。
- ⚠️ 例外保留：`ci-guard` 仅改 workflow 时触发（自我保护，成本极低）；`ai-eval` 定时周五（持续质量趋势）。若用户希望绝对只有一条，可进一步合并这两个。

---

## 八、确认状态（2026-11-16）

| 事项 | 状态 | 决策 |
|---|---|---|
| 1. 主线只测不部署 | ✅ 已确认 | push test 只跑测试，部署仍手动 dispatch |
| 2. db-tests / ui-smoke 处置 | ✅ 已确认 | **db 并入主线**（独立 job，非阻塞）；**ui-smoke 保留独立**（环境重、失败隔离） |
| 3. 僵尸流水线（main test/ai-test） | ⏳ 未确认 | 方案建议先冻结 30 天再删；实施第三批前确认 |
| 4. ai-eval 定时评估 | ⏳ 未确认 | 方案建议保留（AI 质量趋势）；实施第二批前确认 |

> 用户 20:25 "按你的来" = 采纳折中方案（含前两项确认 + 后两项按方案建议执行）。
> 本方案已进入可实施状态；第三批涉及删除的动作执行前再与用户确认一次。

**下一步**：第一批——新建 `test-gate.yml` 主线（structure-guard + quality-gate + db-integration + real-smoke + scenarios + allure-report），
结构级校验后 push test 实测。
