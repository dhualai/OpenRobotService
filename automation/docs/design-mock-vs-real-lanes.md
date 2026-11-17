# 设计：Mock 快车道 与 真实后端车道的双轨测试体系

- **日期**：2026-11-16
- **状态**：现行设计的原理说明（不改变任何代码/配置）
- **关联文档**：`AGENTS.md`（Mock 后端指南、报告要求）、`testing/fixture-and-mock.md`（Mock 规范）、`ENVIRONMENT_SCHEDULING.md`（多环境池）

---

## 一、问题：为什么需要两条车道

同一个系统有两套截然不同的验证需求：

| 需求 | 靠 Mock 能否满足 | 靠真实后端能否满足 |
|---|---|---|
| 本地无服务、无 DB 时也能跑测试 | ✅ | ❌（需起服务、建库、seed） |
| 毫秒级全量回归，改一行就跑 | ✅ | ❌（分钟级） |
| 稳定复现"超时/404/越权/超长"等异常 | ✅（直接定义返回） | ❌（难造数据、要等真实超时） |
| 用例之间完全隔离、无数据污染 | ✅（每用例新实例） | ❌（需清理+seed） |
| 验证真实业务逻辑与数据落库 | ❌（mock 是近似实现） | ✅ |
| 发现接口契约漂移（字段改名/类型变化） | ❌（mock 不跟真后端变） | ✅ |
| 作为部署门禁的最终裁判 | ❌ | ✅ |

**结论**：两条车道解决的是不同问题，缺一不可。用 Mock 追求"快而广"，用真实后端保证"准而真"。

---

## 二、设计：一套用例，两个开关

核心机制：`USE_MOCK` 环境变量 + 同一套 `api` 标记用例。

```
                    同一套 173 个用例（@pytest.mark.api）
                                  │
              USE_MOCK=1（默认）  │  USE_MOCK=0
                                  │
        ┌─────────────────────────┴─────────────────────────┐
        ▼                                                   ▼
   Mock 快车道                                        真实后端车道
   mock_api_client                                    real_api_client
   httpx.MockTransport                                config.yaml 的 api.base_url
   MockBackend() 内存态                                真实 FastAPI 服务
   function 作用域，每例独立                          需 seed + 账号
```

### 2.1 开关实现

`tests/conftest.py` 的 `mock_api_client` fixture：

```python
use_mock = os.getenv("USE_MOCK", "1") != "0"
if use_mock:
    client = ApiClient(transport=create_mock_transport(), ...)
else:
    client = ApiClient(config=load_config().api, ...)
```

用例代码不感知走哪条车道——`_api()` helper、断言方式、`expected_fields` 全部一致。

### 2.2 Mock 车道的构成

- `src/mocks/backend_mock.py`：约 1066 行，覆盖 tasks / call / admin / auth / wechat / ai 全部接口，含内建异常场景（401/404/422/400）。
- `create_mock_transport()`：每个测试新建 `MockBackend()`，天然隔离。
- 默认用户表：`testadmin/admin123`、`engineer/eng123`、`customer/cust123`、`u1_auto/u2_auto`（凭据已收敛到 `src/config/credentials.py`）。

### 2.3 真实后端车道的构成

- `USE_MOCK=0` + 真实 config（`config/{local,sit,uat}/config.yaml`）。
- CI 中由 `deploy-split.yml` 的 Ticket functional cases job 起服务 + seed 后执行。
- 凭据走 secrets/vars，回落默认值（`backend/scripts/seed_automation_env.py`）。

---

## 三、两条车道在 CI 中的分工

| 车道 | 承载工作流 | 用途 | 阻塞部署 |
|---|---|---|---|
| Mock | `ci-guard` / `deploy-split` 的 quality gate | 每次 push 快速回归，拦截本地可发现的回归 | 门禁之一 |
| Mock（DB 例外） | `db-tests` | `-m db` 真实 MySQL 集成（DB 用例无法 mock） | 否（非阻塞） |
| 真实后端 | `deploy-split` 的 Ticket functional cases / Real backend smoke | 验证真契约、场景用例、数据落库 | 是 |
| 真实后端 | `ticket-gate` / `ui-regression` | test 分支门禁、页面级回归 | 视配置 |

**关键**：部署前必须过真实后端车道；Mock 车道全绿不能替代真实后端验证。

---

## 四、Mock 的边界（必须明确登记，避免假绿）

### 4.1 原则

> Mock 是**近似实现**，不是契约本身。它返回什么取决于 `backend_mock.py` 写了什么，不取决于后端真实逻辑。

因此：
- **mock 下通过 ≠ 真实功能正确**；
- **mock 里没实现的分支，用例再绿也没有保护力**。

### 4.2 已知边界（来自复盘 `analysis/analysis-redundancy-and-gaps.md`）

当前有 3 处用例标题明确标注 `mock 暂不实现`：
- 转工单冲突检测（`call test_submit_ticket`）
- 接单冲突检测（`tasks test_assign_engineer_no_conflict`）
- 清除指派状态校验（`tasks test_assign_clear`）

这些在 mock 下只验证"接口能调通"，真实语义由 `USE_MOCK=0` 车道保证。若某个业务规则长期只在 mock 标注而未在真实车道补用例，应视为覆盖缺口而非已覆盖。

### 4.3 防假绿规则

1. 凡 `mock 暂不实现` 的用例，必须在真实后端车道有对应用例，否则登记为缺口。
2. `expected_fields` 只断言 mock 与真实契约都成立的稳定字段（total/status/type 等）；动态计数、SSE、顶层 list 不当字段断言对象。
3. AI 评估类 skip 率过高时 ci 应告警（第二批改善项 P1-4）。

---

## 五、改造成本与维护规则

- **新增接口**：先在 `backend_mock.py` 注册路由和返回，再写用例；两端点返回结构必须对齐真实契约。
- **改真实接口**：同步更新 mock 返回结构，否则真实车道会暴露契约漂移（这是真实车道的价值，不是 bug）。
- **凭据**：统一走 `src/config/credentials.py`，环境变量优先，默认值与 mock 内置用户和 seed 脚本一致。
- **数据隔离**：mock fixture 保持 function 作用域；真实用例靠 seed 脚本幂等初始化。

---

## 六、状态

- 该双轨体系为现行设计，本文是原理说明，不改变代码。
- 缺口清单（状态机、权限、mock 零覆盖接口等）见 `analysis/analysis-redundancy-and-gaps.md`，需用户解除"不新增用例"约束后另批处理。
- 存量用例改善第一批（弱断言 + 凭据收敛 + 冗余清理）见 `task-55-existing-cases-improvement-batch1.md`。
