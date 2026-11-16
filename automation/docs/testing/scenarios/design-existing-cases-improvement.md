# 设计：存量用例弱断言补强与凭据收敛（第一批）

- **日期**：2026-11-16
- **对应分析**：`analysis/analysis-existing-cases-assertions.md`
- **状态**：待人工确认，确认后方可进入实现

---

## 1. 涉及文件清单（6 个，未超 10 个阈值）

| 文件 | 变更 |
|---|---|
| `tests/call/test_call_code.py` | 补 3 处 dict 断言 + 凭据收敛 |
| `tests/tasks/test_tasks_code.py` | 补 5 处 dict 断言 + 凭据收敛 |
| `tests/admin/test_admin_code.py` | 补 5 处 dict 断言 + 凭据收敛 |
| `tests/auth/test_auth_code.py` | 补 2 处 dict 断言 + 凭据内联收敛 |
| `tests/business_chain/test_call_to_ticket_close.py` | 凭据收敛 |
| `src/config/credentials.py`（新建） | 统一凭据读取入口 |

> `tests/conftest.py` 的 mock 凭据保持不动（Mock 内置用户契约，非测试侧散落凭据）。

---

## 2. 模块职责划分

### 2.1 统一凭据入口 `src/config/credentials.py`

```python
"""测试账号凭据：环境变量优先，默认值与 Mock 内置用户/seed 对齐。"""
import os

def role_credentials(role: str) -> tuple[str, str]:
    ...
```

- 映射表：`customer`→`CUST_USERNAME/CUST_PASSWORD`(默认 customer/cust123)、`engineer`→`ENGINEER_*`(默认 engineer/eng123)、`admin`→`ADMIN_*`(默认 testadmin/admin123)、`u1`/`u2`→`U1_*`/`U2_*`(默认 u1_auto/u2_auto + 123456)。
- 默认值与 `AGENTS.md` 默认用户表 + `backend/scripts/seed_automation_env.py` 完全一致 → 不改环境变量时行为与现状**零差异**。
- 各测试文件的 `_ROLE_CREDENTIALS` / `U1` / `U2` 改为调用此函数，消除明文。

### 2.2 断言补强原则

- 只对 **dict 响应** 用 `expected_fields`（helper 已支持递归子集断言）。
- **list / SSE / 204 / 空列表** 保持状态码断言，加 `# 注：` 说明为何不补字段（避免下轮被当成遗漏重复扫描）。
- 字段取自 mock handler 的稳定返回键（已逐一核对源码），且在真实后端契约中也成立的通用字段（total / items / success / status 等）。

---

## 3. 逐处断言变更清单

### call/test_call_code.py（3 处补断言）

| 行 | 用例 | 端点 | 补的 expected_fields | 依据（mock 返回） |
|---|---|---|---|---|
| L52 | test_list_conversations | GET /api/call/conversations | `{'total': 0}`? → 改为 `{'items': [], 'total': 0}` 风险：会话已创建时非 0 | 返回 `{items, total}`，但总数随用例执行变化 → **改为断言 `total` 为 int 不可行（helper 只等值断言）**。经权衡：`{'total': 0}` 在 mock 初始态成立但该用例前可能已建会话 → **本处保持仅状态码 + 注释**，不补 |
| L239 | test_my_tasks_list | GET /api/call/my-tasks/ | 返回 `{'items': [], 'total': 0}` 固定空 → 可补 `{'total': 0}` | mock 固定返回空列表 ✅ |
| L247 | test_my_tasks_create | POST /api/call/my-tasks/ | `{'id': 1, 'title': 'My task'}` | mock 返回 `{id: tid, title: body.title}` ✅ |
| L300 | test_cuiban_long_note | POST /api/tasks/cuiban-notification | `{'success': True, 'message': 'Urge sent'}` | mock 固定返回 ✅ |

→ call 实际可补 **2 处**（L247、L300），L239 可补 `{'total': 0}`，共 3 处；L52 / L313 / L321 / L337（list 或动态总数）加注释。

### tasks/test_tasks_code.py（5 处补断言）

| 行 | 用例 | 端点 | expected_fields | 依据 |
|---|---|---|---|---|
| L93 | test_list_tasks | GET /api/tasks | `{'page': 1, 'size': 10}` | mock 分页返回固定 page/size ✅ |
| L100 | test_list_tasks_pagination | GET /api/tasks?page=2&size=5 | `{'page': 2, 'size': 5}` | 需读该用例实际参数确认 |
| L107 | test_list_tasks_size_200 | GET /api/tasks?size=200 | `{'size': 200}` | ✅ |
| L244 | test_filter_keyword | POST /api/tasks/filter | `{'total': 0}`? 关键字 `error` 可能命中 → 用 `{'items': []}` 风险 | 需实测；保守起见仅对 `total` 存在性无法断言 → **加注释或实测后定** |
| L251 | test_stats_overview | GET /api/tasks/stats/overview | `{'total': 3}` (mock 初始 3 任务) | 任务数可能被前面用例改变 → 改为断言结构键不可行 → **加注释** |
| L262 | test_ai_assign | POST /api/tasks/1/ai-assign | `{'task_id': 1, 'assigned_to': 'engineer-01', 'method': 'auto'}` | mock 固定 ✅ |
| L280 | test_full_flow_create_to_closed 首步 | POST /api/tasks | 首步返回值已用变量承接？需读该文件确认 | 若有变量可加断言 |

> 说明：以上 L100/L244/L251/L280 标"需确认"的，实现前精读该用例上下文后定，原则是**只补稳定字段，绝不为凑数加会挂的断言**。

### admin/test_admin_code.py（5 处补断言）

| 行 | 用例 | 端点 | expected_fields | 依据 |
|---|---|---|---|---|
| L43 | test_tickets_list | GET /api/admin/tickets | `{'total': 3}`? 动态 → 加注释 | `{items,total}` 动态 |
| L51/59 | test_tickets_stats(_again) | GET /api/admin/tickets/stats | `{'total': 3}` 动态 → 加注释 | 动态 |
| L72 | test_projects_list | GET /api/admin/projects | list → 注释 | list |
| L135 | test_risks_list | GET /api/admin/projects/risks | list → 注释 | list |
| L198 | test_dashboard_summary | GET /api/admin/dashboard/tickets/summary | `{'total_tickets': 3}` 动态 → 注释 | 动态 |
| L211/219 | users list | list → 注释 | list |
| L306/314 | roles list | list → 注释 | list |
| L432 | test_resources_list | GET /api/admin/resource-manager/resources | `{'items': [], 'total': 0}` | mock 固定空 ✅ |

→ admin 仅 **L432 可补真实断言**，其余 10 处加"响应为 list / 动态计数，StatusCode 即断言"注释。

### auth/test_auth_code.py（2 处补断言）

| 行 | 用例 | 端点 | expected_fields | 依据 |
|---|---|---|---|---|
| L125 | test_wechat_tags | GET /api/wechat | `{'tags': []}`? tags 可能被其他用例添加 → 动态 → 注释 | `{tags:[...]}` 动态 |
| L133 | test_wechat_callback | POST /api/wechat | `{'tag': {'name': ''}}` | mock 返回 `{tag:{id, name: body.name}}`，body 是 xml 无 name → name='' ✅ |

→ auth L133 可补，L125 加注释。

### 汇总：真实补断言 vs 加注释

| 类别 | 处数 | 处理 |
|---|---|---|
| 可补 dict 字段断言 | 约 8-12 处 | 加 `expected_fields` |
| list / 动态计数 / SSE / 204 | 约 17-21 处 | 加 `# 注：` 说明，保持状态码断言 |

> 与设计初衷（"31 处补断言"）的调整：精读 mock 后发现多数列表响应是 list 或动态计数，无法用稳定字段断言。强行补会导致用例不稳定。**改为"能补则补、不能补则注明理由"**，既提升有效断言，又保持用例稳定——这是更专业的做法。

---

## 4. 风险分析

| 风险 | 等级 | 缓解 |
|---|---|---|
| 动态计数断言导致用例间耦合失败 | 高 | 一律不用动态总数做断言，只补 mock 固定值字段 |
| list 响应误用 expected_fields | 高 | 已全部识别，只加注释不改断言 |
| 凭据环境变量与 mock 内置用户不一致 | 中 | 默认值完全对齐，不改环境变量时行为零差异 |
| 真实后端字段差异 | 中 | 补的字段为通用契约字段；workflow_dispatch 预验证 |

---

## 5. 实现步骤（确认后按此顺序）

1. 新建 `src/config/credentials.py`，提供 `role_credentials(role)`。
2. 改 call 文件：补 3 处断言 + 凭据收敛 → 本地跑 `pytest tests/call/ -q`。
3. 改 tasks 文件：补断言 + 凭据收敛 → 跑 `pytest tests/tasks/ -q`。
4. 改 admin 文件：注释为主 + 1 处断言 + 凭据收敛 → 跑 `pytest tests/admin/ -q`。
5. 改 auth 文件：1 处断言 + 凭据收敛 → 跑 `pytest tests/auth/ -q`。
6. 改 business_chain：凭据收敛 → 跑 `pytest tests/business_chain/ -q`。
7. 全量 mock 车道：`pytest tests/ -m api -q`。
8. 更新用例清单文档、worklog、质量报告状态。

**设计完成，停止，等待人工确认。**
