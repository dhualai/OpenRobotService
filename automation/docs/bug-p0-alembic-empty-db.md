# 【P0】alembic 迁移链无法从空库初始化

> **补充说明（2026-10-28）**：在排查 `deploy/` 目录后确认——**本项目实际从不使用 alembic 做数据库迁移**，
> 迁移职责由部署流水线的 `schema_sync` 承担。因此本问题的正确修法不是"修好 alembic"，
> 而是**明确废弃它**。详见第四节。

- **严重级别**：P0（阻断）
- **发现时间**：2026-10-25
- **影响模块**：`.github/workflows/ticket-gate.yml`、`.github/workflows/test.yml`、任何新建数据库场景
- **当前状态**：两处 CI 已绕过（PR #410），**根因未处理**

## 一、现象

在全新空库上执行迁移，分两层报错。

### 1.1 `alembic upgrade head` → Multiple head revisions

```
ERROR [alembic.util.messaging] Multiple head revisions are present for given argument 'head';
FAILED: Multiple head revisions are present for given argument 'head'; ...
```

`alembic heads`：

```
a3f7c21b9e04 (head)   ← a3f7c21b9e04_resp_trees.py（2026-10-22 责任双树）
e1f4a7b0c326 (head)   ← e1f4a7b0c326_knowledge_challenges.py（2026-10-21 悬赏问题入口）
```

两者 `down_revision` 同为 `d0e3f6a9b215`，**分叉后未做 merge revision**。

### 1.2 改用 `alembic upgrade heads` → Duplicate column

```
sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (1060, "Duplicate column name 'enabled'")
[SQL: ALTER TABLE permissions ADD COLUMN enabled VARCHAR(8) NOT NULL]
```

崩点：`alembic/versions/26021ebb27b1_sync_schema.py:23`。

### 1.3 失败后的库状态（关键证据）

```
tables_created   = 79              ← 迁移一步没成，表却全建好了
alembic_version  = c6281d3a8cf4    ← 只走到空 baseline
```

## 二、根因（两个缺陷叠加）

### 缺陷 A：baseline 是空实现

`backend/alembic/versions/c6281d3a8cf4_baseline.py`（创建于 2026-07-29）：

```python
def upgrade() -> None:
    pass
```

链的第一个版本什么都不创建，后续 30 个 revision 都在"修改不存在的表"。

### 缺陷 B：导入 `app` 会副作用建表

```
① alembic/env.py:15              from app.core.config import settings
② 导入子模块 → 先执行父包          app/__init__.py
③ app/__init__.py:11             from app.core.database import init_users_db
④ app/core/database.py:25        Base.metadata.create_all(bind=engine)   ← 模块级语句
⑤ 79 张表立刻建好（含 permissions.enabled）
⑥ alembic 才开始播迁移：baseline pass → 写 alembic_version
⑦ 26021ebb27b1 执行 ALTER TABLE permissions ADD COLUMN enabled → 1060
```

## 三、影响面

| 受害者 | 表现 |
|---|---|
| `.github/workflows/ticket-gate.yml` | 空库执行 `alembic upgrade head` 必然失败，门禁起不来（272 次运行全部未真正执行） |
| `.github/workflows/test.yml` 的 `test-real` job | 同一命令，实测 exit 255，真实后端冒烟永远跑不到 |
| 任何新建环境 | 新测试环境、新同事本地、新 CI 通道，全部卡在建库 |

两处 CI 已在 PR #410 中改为 `Base.metadata.create_all()` 绕过，**但新建库场景仍会踩**。

## 四、补充：为什么 alembic 会烂成这样

排查 `deploy/` 目录后发现，**本项目真正的数据库结构管理不在 alembic**。

`deploy/README.md` 原文：

> **`Base.metadata.create_all` 只会建新表、不会给已有表加新列**，而部署健康检查是纯探活、不查库，
> 所以「代码比库新」时流水线照样绿灯，直到业务接口报 `1054 Unknown column` 才暴露。

为此团队实现了 `backend/scripts/schema_sync.py`，在**部署前**比对「本次要部署的后端模型」与「目标库 information_schema」：

| 模式 | 行为 | 默认用于 |
|---|---|---|
| `off` | 跳过 | 手动应急 |
| `check` | 只读比对，库落后于代码即**中止部署** | **prod** |
| `sync` | 先自动补齐可安全补的项，补齐后仍缺才中止 | **test** |

分级策略：

- **AUTOMATIC**：仅 `ADD COLUMN`（可空或有 `server_default`）、`CREATE INDEX` → 自动执行
- **MANUAL**：主键、自增列、`NOT NULL` 无默认值、改类型、改枚举、改约束、删列 → 只报告不执行，
  可用 `-draft-dir` 生成人工迁移草稿

**结论：**

```
项目真实迁移链路 = create_all（建新表）+ schema_sync（加列/索引）+ 人工（改类型/删列）
alembic         = 从未被执行过的历史遗留物
```

这也解释了此前所有反常现象：

- `baseline` 为什么是空实现
- 31 个 revision 为什么从未真正跑过
- `a3f7c21b9e04_resp_trees.py` 的作者为什么在注释里写"链与存量库并不一致，可能被 Duplicate column 打断，所以加了 has_table 守卫"
- `backend/scripts/` 下为什么有 `create_resp_trees.py`、`create_task_proxy_relation.py`、`init_dataqa_tables.py`、`ensure_archive_schema.py`、`apply_schema_patch.py` 一堆独立建表脚本

## 五、复现步骤

```powershell
mysql -u root -p -e "CREATE DATABASE openrobot_repro CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"

cd backend
$env:DATABASE_URL="mysql+pymysql://root:<pwd>@127.0.0.1:3306/openrobot_repro?charset=utf8mb4"
$env:PYTHONPATH=(Get-Location).Path
$env:PYTHONUTF8="1"

alembic upgrade head      # → Multiple head revisions
alembic upgrade heads     # → Duplicate column name 'enabled'

mysql -u root -p -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='openrobot_repro'"
# → 79
```

## 六、建议修法（已按 deploy 目录结论修正）

### 方案 A（推荐）：明确废弃 alembic

1. `backend/alembic/` 目录归档（移到 `docs/archive/` 或在文件头标注「历史遗留，不再使用」）
2. 在 `backend/README` 或 `docs/` 写明数据库结构管理口径：
   **`create_all`（新表）+ `schema_sync`（加列/索引）+ 人工脚本（改类型/删列）**
3. 删除 `env.py` 对 `app` 包的导入依赖（或直接随目录一起归档），消除副作用建表的隐患

**成本最低，且与团队既有工作流一致。**

### 方案 B（不推荐）：扶正 alembic

需要：补 merge revision + 断开 env.py 的 app 导入 + 生成真实 baseline。
成本高，且与现有 `schema_sync` 职责重叠，容易形成两套并存。

## 七、附带发现（同一排查中发现，建议一并处理）

| 项 | 说明 |
|---|---|
| `deploy/tests/` 三个测试文件 | `test_gen_release_notes.py` / `test_notify_release.py` / `test_schema_artifacts.py` 共 32 KB，**任何 workflow 都未执行**，仅 README 提到可手工跑 |
| `automation/config/sit`、`uat` | 配置已过期：`sit.openrobot.local:8000` 与实际 test 环境（`127.0.0.1:9400`）不符，两套配置从未真正使用 |
| `deploy/` 内两个可执行文件 | `OpenRobotDeploy.exe`（12 MB）+ `.bak-local`（13 MB）同时在仓库内，可评估是否保留 |
