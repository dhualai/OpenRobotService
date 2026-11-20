# AI unit tests (non-blocking) 失败根因分析

> 分析人：自动化测试（AI）｜日期：2026-11-12｜依据：deploy-split Run #144 (id 43145138944) @ 0547c497 + 本地复现
>
> 结论概要：5 条失败 = 1 个测试污染缺陷（影响 2 条）+ 1 个实现数据不同步（影响 2 条）+ 1 个测试与模板文案脱节（影响 1 条）。**全部位于 ai/ 目录，超出自动化测试修改边界，需 AI/后端团队处理。**

## 失败清单（CI 与本地复现完全一致）

| # | 测试 | 错误 | 根因分类 |
|---|------|------|---------|
| 1 | AiTaskPlatform/contexts/test_project_roles.py::test_format_project_roles_uses_contact_person | `assert '项目主数据角色' in ''`（实现吞异常返回空串，warning: OperationalError） | 测试污染 |
| 2 | AiTaskPlatform/contexts/test_project_roles.py::test_format_project_roles_resolves_contact_person_id | 同上 | 测试污染 |
| 3 | AiDiagnosisPlatform/assigner/tests/test_dual_tree_experiment.py::TestWalk::test_missing_skill_still_returns_org_people | `KeyError: '网络通信'` @ algorithm.py:272 | 实现数据不同步 |
| 4 | AiDiagnosisPlatform/assigner/tests/test_dual_tree_experiment.py::TestWalk::test_vehicle_anchor_opens_department | 同上 | 实现数据不同步 |
| 5 | AiDiagnosisPlatform/assigner/tests/test_step6.py::TestIronRulesPrompt::test_support_contact_priority_in_step6 | `assert '优先派给该对接人' in prompt` | 测试/模板文案脱节 |

## 根因一：test_report.py 模块替换污染（失败 #1/#2）

### 复现路径（本地实证）
- 单跑 `pytest ai/agents/AiTaskPlatform/contexts/test_project_roles.py` → **4 passed**
- 全量 CI 命令 `pytest ai/tests ai/agents/AiTaskPlatform ai/agents/AiDiagnosisPlatform/assigner/tests ai/core` → **5 failed，与 CI 完全一致**
- 最小组合 `pytest ai/tests/test_report.py ai/agents/AiTaskPlatform/contexts/test_project_roles.py` → **2 failed**

### 机制
`ai/tests/test_report.py` 的 `_preload_database_module()`（:29-50）用
`importlib.util.spec_from_file_location("ai.core.database", db_file)` 手工加载副本，并
`sys.modules["ai.core.database"] = db_mod`。**但它不设置父包属性 `ai.core.database`**，导致：

- `from ai.core.database import SessionLocal`（contexts.py 实现，走 **sys.modules**）→ 拿到**副本**模块
- `monkeypatch.setattr("ai.core.database.SessionLocal", ...)`（测试，走 **`__import__`+getattr 包属性链**）→ patch 的是**另一份**模块

两者指向不同对象 → **monkeypatch 失效** → `format_project_roles` 使用副本里的真 `sessionmaker` 连
`mysql+pymysql://root:123456@127.0.0.1:3306/helpdesk` → CI 无 MySQL → `OperationalError`
→ 实现里 `except Exception: return ""`（contexts.py:394）吞掉 → 断言拿到空串失败。

且该替换**无 fixture 恢复**（test_report.py 是 print 式脚本改造的测试，没有 teardown），污染跨文件持续；
test_user_profile.py:38-46 与 test_vehicle_mode.py:40-50 已有注释适配此污染，唯独 test_project_roles.py 未适配。

### 修复建议（ai/ 团队）
1. **首选**：`test_report.py` 改造为规范 pytest（fixture + monkeypatch 自动恢复），或在
   `_preload_database_module()` 里同步父包属性：`setattr(sys.modules["ai.core"], "database", db_mod)`，
   并在用例结束后还原 `sys.modules["ai.core.database"]`。
2. **规避**（CI 侧，不动 ai/）：deploy-split 的 AI unit tests 步骤将 `ai/tests/test_report.py` 拆到独立 pytest 进程先行执行。

## 根因二：DOMAINS 与 DOMAIN_BRIEFS 不同步（失败 #3/#4）

- `ai/agents/AiDiagnosisPlatform/assigner/dual_tree/domains.py:27` `DOMAINS` 元组有 **9** 项，含「网络通信」「现场环境」；
- `ai/agents/AiDiagnosisPlatform/assigner/dual_tree/algorithm.py:18` `DOMAIN_BRIEFS` 字典只有 **7** 项，**缺「网络通信」「现场环境」**；
- `algorithm.py:271-272` `for name in DOMAINS: ... DOMAIN_BRIEFS[name]` 直接下标访问 → `KeyError`。

**修复建议**：补齐 `DOMAIN_BRIEFS` 两个 key 的 brief 文案（与 DOMAIN_BRIEFS 现有风格一致），
或把访问改为 `DOMAIN_BRIEFS.get(name, "")` 并在 `check_trace` 里把缺 brief 记为 issue。

## 根因三：step6 铁律文案与测试脱节（失败 #5）

- 测试断言 `ai/agents/AiDiagnosisPlatform/assigner/tests/test_step6.py:113` 期望「优先派给该对接人」；
- 模板 `ai/agents/AiDiagnosisPlatform/assigner/prompts/step6.py` 现文案为「**优先派对接人**」（:29）、「优先项目对接人某某」（:34），全仓库无「优先派给该对接人」字样。
- 模板改版后测试未同步。

**修复建议**：与产品口径确认后二选一——改测试断言对齐新文案（推荐，成本最低）；或模板补回「优先派给该对接人」原句。

## 附：验证环境说明
- 本地复现使用隔离 venv（WorkBuddy managed Python 3.13，`envs/default`），安装 ai/requirements.txt + backend/requirements.txt + greenlet/aiomysql/python-jose/minio/redis/celery 等补齐依赖后，复现结果 5 failed / 1046 passed / 27 skipped，与 CI Run #144 一致。
- 修复后预期：AI unit tests job 5 条全过，deploy-split 整轮除已 skip 外全绿。
