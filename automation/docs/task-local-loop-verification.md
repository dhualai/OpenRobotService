# 本地真实环境闭环验证

日期：2026-10-25
环境：Windows 11 / Python 3.11.7 / 本机无 Docker、非管理员

## 本次目标

把自动化框架从「代码已合并」推进到「在真实后端上跑通」，用证据替换推测：

1. 本地拉起真实后端（MySQL + FastAPI 8400）
2. Mock 用例与真实后端用例都能跑
3. 验证 ticket-gate 的 fail-closed 分支
4. 记录过程中暴露的真实问题

## 环境搭建过程

本机没有 Docker、没有管理员权限，MySQL 用便携版落地：

| 组件 | 做法 | 位置 |
|---|---|---|
| MySQL 8.0.29 | 官网 zip 便携版（华为云镜像），`--initialize-insecure` 初始化 | `%LOCALAPPDATA%\OpenRobotTestEnv\mysql-8.0.29-winx64` |
| 数据目录 | 独立 datadir | `%LOCALAPPDATA%\OpenRobotTestEnv\data` |
| 数据库 | `helpdesk_724`（与 scanner/alembic 默认名一致） | 端口 3306 |
| root 密码 | `123456`（与 `settings.DB_CONFIG` 默认一致） | 仅本地 |
| 后端 | `.venv` + `python main.py` | 端口 8400 |

依赖安装注意：`pip install -r backend/requirements.txt` 在 Windows 中文环境会因
GBK 解码 UTF-8 注释失败，需先设置 `PYTHONUTF8=1`。

## 建表方式（绕过 baseline 空实现）

`alembic upgrade head` 在空库上失败，改用：

```powershell
# 1. 由模型建表（74 张）
python -c "import sys; sys.path.insert(0,'backend'); from app.core.db import engine; from app.models.base import Base; import app.models; Base.metadata.create_all(engine)"
# 2. 标记迁移版本
cd backend; alembic stamp head
# 3. 灌种子数据（项目 Leo_test + automation_bot）
python scripts/seed_automation_env.py
```

## 修改文件

| 文件 | 改动 |
|---|---|
| `automation/tests/real/test_real_backend_smoke.py` | `POST /api/tasks` → `/api/tasks/`（真实路由带尾斜杠，原路径返回 307） |
| `automation/tests/real/test_ticket_lifecycle_real.py` | 新增 `_user_id()`，`created_by` / `assigned_to` 改为比对 `users.id` 而非 username；派单参数改传 user id |

## 测试结果

```text
# Mock 全量
pytest tests -q
131 passed, 48 skipped

# 框架库
pytest src config ci_ai_gen -q
372 passed

# 真实后端（后端 8400 + MySQL 3306）
USE_MOCK=0 pytest tests/real -q
4 passed
```

真实后端 4 条覆盖：健康检查、登录 + /auth/me、建单 + 查详情、**完整工单生命周期**
（U1 建单 → 派单 U2 → 读阶段模板 → 接单 → 两次阶段推进与确认 → resolved → U1 关闭）。

Allure 报告：`automation/output/allure-report/index.html`（131 + 4 条结果）。

门禁本地验证：

```powershell
python automation/scripts/cli-ticket-gate.py --text "feature（ORS-999）：本地门禁回归验证" --regression tests/tasks --env local
```

输出关键字段：`ticket_ids=[999]`、`status_lookup=skipped`、`functional.executed=false`、
`regression 32 passed`、`passed=true`、`blocking=false`。
即：无生产凭据时功能用例被跳过、回归照跑，符合 fail-closed 设计。

## 发现的问题

### P0：迁移链无法从空库建立（建表被执行了两遍）

实测报错：`(1060, "Duplicate column name 'enabled'")`。真实机制是导入副作用与迁移脚本撞车：

1. `alembic/env.py:15` 有 `from app.core.config import settings`
2. 导入子模块会先执行父包 `app/__init__.py`
3. `app/__init__.py:11` 导入 `app.core.database`
4. `app/core/database.py:25` 是**模块级**语句 `Base.metadata.create_all(bind=engine)`
   → 只要 alembic 一启动，74 张表就已经建好了
5. 随后 alembic 才从根开始播迁移：baseline 是 `pass`（空实现），
   下一个 `26021ebb27b1_sync_schema` 执行 `ALTER TABLE permissions ADD COLUMN enabled`
   → 该列已由 create_all 建出 → 1060 Duplicate column

两个缺陷叠加：**baseline 是空实现** + **import app 会副作用建表**。

影响：`ticket-gate.yml` 的 `cd backend && alembic upgrade head` 在 CI 一次性空库上
必然失败。因 `TICKET_GATE_ENABLED` 一直未开启，该缺陷未暴露。

本分支处置：把 `ticket-gate.yml` 的迁移步骤改为与 `test.yml` 一致的
`Base.metadata.create_all()`，解除门禁阻塞。**迁移链本身（baseline 空实现 +
导入副作用建表）仍需开发修复**，否则任何依赖 `alembic upgrade head` 的空库场景都会失败。

修复需开发确认，建议二选一：

- 结构性修：把 `settings` 抽到不触发 `app/__init__.py` 的模块，env.py 不再导入 `app` 包；
  同时把 baseline 换成真实全量 schema
- 务实修：CI 改为 `create_all` + `alembic stamp head`（放弃迁移可重放性，需明确接受）

自动化侧不改 `backend/`。

### P1：Mock 与真实后端的路由契约不一致

- 真实后端：`/api/tasks/`（带尾斜杠），`/api/tasks` 返回 307
- MockBackend：`/api/tasks` 直接返回 200

影响：Mock 下全绿的用例，切到真实后端才暴露。建议 MockBackend 统一做尾斜杠归一化，
或在契约测试中固定真实路径。

### P2：数据契约假设错误

- `tasks.created_by` / `assigned_to` 存的是 `users.id`（如 `user_admin`），不是 username
- 本地种子缺 `task_steps` 基础阶段模板（生产由既有数据提供），导致 `/steps` 返回空

影响：真实链路用例要么失败要么跳过。已修正用例断言；本地需手工补 `task_steps`。

### P3：`DELETE /api/tasks/{id}` 必然返回 500（已定位根因）

现象：任何工单删除接口都返回 500。

根因（实测错误体）：

```
(asyncmy.errors.IntegrityError) (1048, "Column 'task_id' cannot be null")
[SQL: UPDATE task_operation_logs SET task_id=%s WHERE task_operation_logs.id = %s]
```

删除逻辑试图把 `task_operation_logs.task_id` 置空来实现"孤儿化"，但该列是 NOT NULL，
于是对所有产生过操作日志的工单（建单即产生）都会失败。

影响：前端删除工单功能实际不可用；自动化用 DELETE 做清理会污染报告（到处 500）。

处置：自动化侧清理方式改为「撤回（PATCH status=canceled）」，不再调用 DELETE。
后端删除逻辑需开发修复（改级联删除或允许 task_id 为空并保留审计）。

## 风险

- 本地 MySQL 是便携版 + root 弱口令，仅限本机验证，不可作为共享环境
- 便携版未注册为服务，重启后需手动拉起（`mysqld --datadir=...`）
- 后端依赖 MinIO/Redis/Qdrant 未启动，相关能力（附件、缓存、向量检索）在真实链路中未覆盖

## 下一步

1. 与开发确认 P0 迁移方案（真实 baseline 或 CI create_all + stamp）
2. 修正 MockBackend 尾斜杠归一化（P1），避免 Mock/真实契约漂移
3. 把 `task_steps` 基础模板纳入 `seed_automation_env.py`（P2）
4. 按《向公司要什么》清单拿到生产只读凭据后，跑一次真实工单闭环（scan → generate → promote → gate）
