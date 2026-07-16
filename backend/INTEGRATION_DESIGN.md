# 外部任务源集成设计（INTEGRATION_DESIGN）

> 状态：**设计稿**（待评审 → 实施）
> 日期：2026-07-14
> 关联：[ARCHITECTURE.md](./ARCHITECTURE.md)（总体架构 / USP 接缝）、[CODEBASE_OVERVIEW.md](./CODEBASE_OVERVIEW.md)（代码现状）
> 背景：当前「系统任务」模块尚未成熟，临时接入**禅道（Zentao）**作为外部任务源；未来本系统会开发平替禅道的原生功能，禅道最终可能**移除**。因此接入须**插件化、对核心零侵入**。

---

## 一、目标与约束

### 目标
1. 把禅道任务同步进统一的 `tasks` 表，与手工工单在「系统任务」收件箱中**统一流转、统一处理、统一统计**。
2. 确立「**外部任务源（Task Source）**」这一可插拔机制，禅道是其**第一个实例**而非硬编码特例。
3. 未来增删任务源（含最终摘除禅道），**核心代码零改动**。

### 已确认的关键决策

| 决策 | 选定方案 |
|---|---|
| **同步方向** | **单向只读**（禅道 → 本平台），禅道保持权威源；本平台变更不回写 |
| **状态合并** | **取较后状态**：禅道状态领先则同步推进本平台；本平台领先则不动（禅道允许滞后） |
| **触发方式** | **Airflow 仅做定时**，通过 HTTP 触发本平台同步接口；手动触发共用同一接口 |
| **用户映射** | 维护**显式账号映射表**（禅道 account → 本平台 user_id） |
| **架构形态** | **插件化**：核心只认中立契约，禅道是契约的一个实现 |

### 设计判据（什么算"插件化成功"）
以「**移除禅道时要改哪些地方**」为验收：

| 操作 | 核心改动 |
|---|---|
| 删 `app/integrations/sources/zentao/` | — |
| 从 `TASK_SOURCES_ENABLED` 去掉 `zentao` | — |
| 删 `.env` 的 `ZENTAO_*` + Airflow DAG | — |
| `Task` 模型 / `tasks` 模块 / 状态机 / 收件箱 / AI 派单 | **零改动** |

`source='zentao'` 的历史任务保留为归档记录，核心查询天然兼容。

---

## 二、分层架构

```
┌─────────────────────────────────────────────────────────┐
│  稳定核心层（不随源增删而变）                              │
│  ┌───────────────────────────────────────────────────┐  │
│  │ tasks 模块：Task 模型 / 状态机 / 收件箱 /          │  │
│  │ AI 派单 / 通知 / 统计 —— 不 import 任何源插件      │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────┐  │
│  │ integrations 框架（源无关）：                       │  │
│  │  base.py     ExternalTask 中立结构 + Adapter 契约  │  │  ← 核心契约
│  │  registry.py 注册表（按名发现源）                  │  │
│  │  engine.py   SyncEngine（通用 upsert / 状态合并 /  │  │
│  │              账号映射）                            │  │
│  │  api.py      /sources/{source}/... 通用路由        │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                          ▲ 实现契约（import 即自注册）
┌─────────────────────────┴───────────────────────────────┐
│  可插拔插件层（整个目录可删）                              │
│  sources/zentao/   adapter + client + mapper            │
│  sources/<未来X>/   ...                                  │
└─────────────────────────────────────────────────────────┘

  触发：Airflow DAG ──HTTP──▶ POST /api/tasks/sources/zentao/sync
                              （独立 API Key 鉴权，与用户 JWT 分离）
```

**分工一句话**：插件只负责「**拉取 + 翻译成中立结构**」，引擎负责「**落库 + 状态合并 + 账号解析**」，二者用 `ExternalTask` 这层中立表示解耦。

---

## 三、核心契约（三件套，稳定不变）

### 3.1 `ExternalTask` —— 中立中间表示

插件把外部数据翻译成此结构，翻译目标是**核心已有的枚举**。引擎与 `tasks` 模块永远不碰禅道字段。

```python
# app/integrations/base.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Iterator

from app.models.task import TaskStatus, TaskPriority, TaskType


@dataclass
class ExternalTask:
    external_id: str
    title: str
    description: str
    status: TaskStatus              # 插件已把禅道 wait→new 等映射完成
    priority: TaskPriority          # 插件已把 pri 1-4 → urgent/low
    task_type: TaskType
    assigned_account: Optional[str] # 外部账号原值；引擎查映射表转 local_user_id
    created_account: Optional[str]
    created_at: Optional[datetime]
    deadline_at: Optional[datetime]
    url: Optional[str]
    extra: dict = field(default_factory=dict)  # 工时等源特有字段，原样进 metadata_info
```

> 关键分工：
> - **枚举映射**（禅道 `wait/doing/done` → 核心 `TaskStatus`）是禅道特有知识 → 留在 zentao 插件。
> - **账号 → 本平台 user** 是跨源通用能力 → 留在引擎（查通用 `task_user_mapping` 表）。
>
> 插件因此完全不需要知道本平台用户体系。

### 3.2 `TaskSourceAdapter` —— 源插件接口

```python
# app/integrations/base.py
from abc import ABC, abstractmethod
from typing import AsyncIterator


class TaskSourceAdapter(ABC):
    name: str            # "zentao" —— 作为 tasks.source 字段值
    display_name: str    # "禅道"

    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    async def fetch(self) -> AsyncIterator[ExternalTask]:
        """拉取外部任务并翻译为 ExternalTask。引擎消费此迭代器。"""

    async def on_sync_done(self, result: "SyncResult") -> None:
        """可选钩子：同步完成回调（记日志/发通知）。默认空实现。"""
```

插件**只实现 `fetch`**（拉 + 翻译）。upsert、状态合并、账号映射、入库统一由引擎完成——这是"核心不重复、插件极薄"的关键。

### 3.3 `SyncEngine` + `SourceRegistry` —— 通用引擎

```python
# app/integrations/registry.py
class SourceRegistry:
    def register(self, adapter: TaskSourceAdapter) -> None: ...
    def get(self, name: str) -> TaskSourceAdapter: ...
    def all(self) -> list[TaskSourceAdapter]: ...


# app/integrations/engine.py
class SyncEngine:
    def __init__(self, db, registry: SourceRegistry): ...

    async def sync_source(self, source_name: str) -> SyncResult:
        adapter = registry.get(source_name)
        result = SyncResult(source=source_name)
        async for ext in adapter.fetch():
            local = await self._find(source_name, ext.external_id)
            if local is None:
                await self._create(source_name, ext, result)   # 含账号映射解析
            else:
                await self._merge_update(local, ext, result)   # 状态取较后、字段更新
        await adapter.on_sync_done(result)
        return result
```

**状态合并规则放进引擎**——它对任何源都成立（外部源与本平台比序号取 max），不属于禅道知识。禅道原始状态 → 核心 `TaskStatus` 的映射在插件，序号比较在引擎。

---

## 四、关键设计决策

### 4.1 `source` 字段用字符串，不用枚举

```python
source = Column(String(32), nullable=False, default="manual", index=True)
```

加新源无需改模型、无需迁移枚举；禅道移除后历史数据原地兼容。**核心模型不持有"源清单"**——源清单只存在于运行时注册表。

### 4.2 状态合并：取较后状态（放引擎）

定义状态进度序号，合并时取 `max`：

| 本平台状态 | 序号 | 禅道来源 |
|---|---|---|
| `new` | 0 | `wait` |
| `in_progress` | 1 | `doing` |
| `pending` | 1（同级，不视作前进） | `pause` |
| `resolved` | 2 | `done` |
| `closed` | 3 | `cancel` / `closed` |

```python
STATUS_ORD = {TaskStatus.NEW: 0, TaskStatus.IN_PROGRESS: 1, TaskStatus.PENDING: 1,
              TaskStatus.RESOLVED: 2, TaskStatus.CLOSED: 3}

def merge_status(local: TaskStatus, incoming: TaskStatus) -> Optional[TaskStatus]:
    """incoming 为插件映射后的核心状态；返回应写入的状态，None 表示不操作。"""
    if STATUS_ORD[incoming] <= STATUS_ORD[local]:
        return None          # 本平台已领先/同级 → 不动
    return incoming          # 外部领先 → 推进本平台
```

- `pending`（禅道 `pause`）与 `in_progress` 同序号 → 互不覆盖（暂停不算进展）。
- 本平台已 `closed`、禅道仍 `wait` → 本平台保持 `closed`，不会被拉回。
- 单向：本平台状态从不回写禅道。

### 4.3 账号映射：通用表 + 引擎解析

新建通用映射表（跨源复用）：

```python
# app/models/task.py
class TaskUserMapping(Base):
    __tablename__ = "task_user_mapping"
    id = Column(BigInteger, primary_key=True)
    source = Column(String(32), nullable=False, index=True)        # "zentao"
    external_account = Column(String(64), nullable=False)          # 禅道 account, 如 zhangjunlei
    external_realname = Column(String(128), nullable=True)         # 张俊磊（便于识别）
    local_user_id = Column(String(50), nullable=False)             # 本平台 user_id
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("source", "external_account", name="uq_mapping_src_account"),)
```

引擎落库时：按 `(source, ext.assigned_account)` 查表 → 命中则写入 `local_user_id`；未命中则 `assigned_to` 留空（可走 AI 派单），realname 记入 `metadata_info` 便于后续补配。管理接口：`/api/admin/task-user-mappings`（CRUD）。

---

## 五、数据模型变更

### 5.1 `Task` 表（[app/models/task.py](app/models/task.py)）

新增字段：

```python
source = Column(String(32), nullable=False, default="manual", index=True,
                comment="任务来源：manual / zentao / ...")
external_id = Column(String(64), nullable=True, index=True, comment="外部系统任务ID")
external_url = Column(String(512), nullable=True, comment="外部系统跳转链接")

__table_args__ = (UniqueConstraint("source", "external_id", name="uq_task_source_external"),)
```

> 禅道特有字段（estimate/consumed/left/execution_id/execution_name/pri）不新增列，塞进已有的 `metadata_info` JSON。

### 5.2 `task_user_mapping` 表

见 4.3。

### 5.3 Alembic 迁移

新增 `alembic/versions/20260714_add_task_source_and_mapping.py`：
- `tasks` 加 `source` / `external_id` / `external_url` 三列（存量数据 `source` 默认 `manual`、`external_id` NULL）。
- 建唯一索引 `uq_task_source_external`（`external_id IS NULL` 时唯一约束不冲突，MySQL 允许多 NULL）。
- 新建 `task_user_mapping` 表。

### 5.4 查询过滤

[get_tickets](app/modules/tasks/services/ticket_service.py#L182) / [filter_tickets](app/modules/tasks/services/ticket_service.py#L438) 的 `FIELD_MAPPING` 增加 `source` 项，支持按来源筛选。

---

## 六、字段映射（禅道 task → ExternalTask）

| 禅道字段 | ExternalTask 字段 | 规则 |
|---|---|---|
| `id` | `external_id` | 字符串化；`url = {base}/task-view-{id}.html` |
| `name` | `title` | 直映 |
| `desc` | `description` | 空则用 name |
| `pri` (1-4) | `priority` | 1→urgent, 2→high, 3→medium, 4→low |
| `status` | `status` | 见状态映射（4.2） |
| `type` (devel/test/…) | `task_type` | devel→feature, test→support, 其余→other |
| `assignedTo.account` | `assigned_account` | 引擎查表转 local_user_id |
| `openedBy.account` | `created_account` | 同上 |
| `openedDate` | `created_at` | ISO → datetime |
| `deadline` | `deadline_at` | 直映 |
| `estimate/consumed/left`、`project`/`execution` | `extra` | 工时与层级信息进 metadata_info |

> 状态/优先级/类型映射均为**禅道特有知识**，集中在 `sources/zentao/mapper.py`，引擎与核心不感知。

---

## 七、目录结构与自注册机制

```
app/integrations/
├── __init__.py        # 读 TASK_SOURCES_ENABLED，import 已启用插件包 → 触发自注册
├── base.py            # ExternalTask + TaskSourceAdapter + SyncResult（核心契约）
├── registry.py        # SourceRegistry（单例）
├── engine.py          # SyncEngine（upsert / 状态合并 / 账号映射查表）
├── api.py             # 通用路由：/sources、/sources/{name}/sync、/status
└── sources/
    └── zentao/        # ★ 整个目录可删
        ├── __init__.py   # registry.register(ZentaoAdapter())
        ├── adapter.py    # 实现 TaskSourceAdapter.fetch()
        ├── client.py     # 由 candao_dev/zentao_client.py 异步化（httpx，保留登录/分页容错）
        └── mapper.py     # 禅道字段 → ExternalTask（含 wait→new / pri→priority 等映射）
```

**自注册**：`app/integrations/__init__.py` 根据 `TASK_SOURCES_ENABLED` 动态 import 对应插件包，插件包 `__init__.py` 调 `registry.register(...)`。

```python
# app/integrations/__init__.py（示意）
from app.core.config import settings
from app.integrations.registry import registry

def _load_sources():
    for name in settings.TASK_SOURCES_ENABLED:        # ["zentao"]
        try:
            __import__(f"app.integrations.sources.{name}", fromlist=["__init__"])
        except ImportError as e:
            logger.warning("任务源插件 %s 加载失败：%s", name, e)

_load_sources()
```

`app/__init__.py` 仅增加一行 `import app.integrations  # noqa: E402`，**不感知任何具体源**。

---

## 八、路由设计（通用，按名分发）

```python
# app/integrations/api.py  挂 /api/tasks/sources，独立 API Key 鉴权（X-API-Key）
@router.get("/")                         # 列出已注册源 + 上次同步状态
@router.post("/{source}/sync")           # Airflow / 手动共用 → engine.sync_source(source)
@router.get("/{source}/status")          # 单源同步状态
```

- 账号映射 CRUD 放 `/api/admin/task-user-mappings`（跨源通用）。
- **禅道不自带路由**。加源不加路由，删源不删路由。

---

## 九、触发机制：Airflow 仅定时，业务全在本平台

**结论：禅道拉取/映射/落库全部作为本平台功能，Airflow 只负责定时触发。** 理由：

1. 同步逻辑强依赖本平台内部（`Task` 模型 / `settings` / ORM）——搬进 Airflow 会使其退化为"第二个后端"，双依赖、双写。
2. Airflow 触发与手动触发共用同一 HTTP 接口，可独立调试、可单测。
3. 职责清晰：Airflow 管调度/重试/告警，本平台管业务。
4. 降级容易：换 Celery beat / cron，本平台一行不改。

Airflow DAG（极薄，约 20 行）：

```python
# dags/zentao_sync.py
from airflow import DAG
from airflow.providers.http.operators.http import SimpleHttpOperator
from datetime import datetime

with DAG("zentao_task_sync", start_date=datetime(2026, 7, 14),
         schedule="*/10 * * * *", catchup=False) as dag:
    SimpleHttpOperator(
        task_id="trigger_sync",
        http_conn_id="helpdesk_api",
        endpoint="api/tasks/sources/zentao/sync",
        method="POST",
        headers={"X-API-Key": "{{ var.value.HELPDESK_SYNC_API_KEY }}"},
        response_check=lambda r: r.json().get("code") == 200,
        retries=2,
    )
```

鉴权采用独立 API Key（复用 `ARCHITECTURE.md` 中 USP 接缝的"机器对机器、独立鉴权"思路），与用户 JWT 分离。

---

## 十、同步流程与幂等性

```
sync_once(source="zentao"):
  adapter = registry.get("zentao")
  async for ext in adapter.fetch():
    local = engine._find("zentao", ext.external_id)        # 按 (source, external_id)
    if local is None:
        engine._create(ext)        # 解析账号映射 → 新建 Task（source=zentao）
    else:
        engine._merge_update(local, ext)   # 状态取较后；字段更新；账号重解析
  adapter.on_sync_done(result)
```

- **幂等**：按 `(source, external_id)` upsert，可安全重跑。
- **增量**：禅道 tasks 接口不支持按时间过滤，一期采用**全量拉取 + upsert**；数据量上升后用 `lastEditedDate` 过滤优化。
- **容错**：单条失败不中断整批（沿用 `candao_dev/main.py` 思路），失败计数记入 `SyncResult`。
- **与 AI 派单**：禅道任务若经映射后带 `assigned_to`，跳过 AI 派单；未带则可正常触发。
- **与通知**：同步新增/指派时复用 `NotificationUtils.send_ticket_create_notification`。
- **与状态机**：同步写入仍受 tasks 模块 `ALLOWED_TRANSITIONS` 约束。

---

## 十一、配置项（`config.py` + `.env`）

```
# 任务源总开关（逗号分隔的已启用源）
TASK_SOURCES_ENABLED=zentao

# 同步接口鉴权（Airflow 用）
HELPDESK_SYNC_API_KEY=change-me

# 禅道插件配置（插件自带，放插件包内读取或统一进 settings 均可）
ZENTAO_BASE_URL=http://zentao.example.com
ZENTAO_ACCOUNT=...
ZENTAO_PASSWORD=...
ZENTAO_VERIFY_SSL=true
ZENTAO_PROJECT_IDS=[1,2,3]            # 复用 candao_dev 的 parse_project_ids
```

禅道配置缺失时插件 `is_enabled()` 返回 False，自动跳过（与微信降级思路一致）。

---

## 十二、移除禅道 / 平替禅道（两条独立路径）

### 12.1 移除禅道
外部同步 path 退役：
1. 删 `app/integrations/sources/zentao/`
2. `TASK_SOURCES_ENABLED` 去掉 `zentao`
3. 删 `.env` 的 `ZENTAO_*` + Airflow DAG
4. `source='zentao'` 历史任务**保留为归档**（或写清理脚本转 `source='manual'`）
5. 核心零改动 ✅

### 12.2 平替禅道
是**另一条 path**——本系统原生项目管理（前端创建、`source='manual'`/`'native'`）成长。它走现有 `TicketCreate` 流程，**不经 `integrations` 层**，与禅道插件的存在/移除完全无关。

两条 path 通过统一 `tasks` 表 + `source` 字段共存；原生成熟后禅道自然停用。`integrations` 不会成为原生功能的负担或技术债。

---

## 十三、落地步骤（4 阶段，每步可独立验证）

**Phase 1 · 契约层**
- [ ] `base.py`：`ExternalTask` + `TaskSourceAdapter` + `SyncResult`
- [ ] `registry.py`：`SourceRegistry`
- [ ] `engine.py`：`SyncEngine`（`_find` / `_create` / `_merge_update` + 状态合并 + 账号映射查表）
- [ ] `Task` 加 `source`/`external_id`/`external_url` + 唯一约束；`TaskUserMapping` 表
- [ ] Alembic 迁移 `20260714_add_task_source_and_mapping`
- [ ] `tasks` 列表/过滤增加 `source` 维度

**Phase 2 · 首个插件**
- [ ] `sources/zentao/client.py`：`candao_dev/zentao_client.py` 异步化（`httpx`，保留明文/MD5 登录、`total+空页`分页、`executi1n`/对象字段等容错）
- [ ] `sources/zentao/mapper.py`：禅道字段 → `ExternalTask`
- [ ] `sources/zentao/adapter.py`：实现 `fetch()` + 自注册
- [ ] 单元测试：映射、状态合并、upsert 幂等

**Phase 3 · 触发与管理层**
- [ ] `integrations/api.py`：通用路由（`X-API-Key` 鉴权）
- [ ] `TASK_SOURCES_ENABLED` 开关 + 自注册装载
- [ ] `/api/admin/task-user-mappings` CRUD
- [ ] `app/__init__.py` 接入 `import app.integrations`

**Phase 4 · 外部与文档**
- [ ] Airflow DAG（HTTP 触发）
- [ ] `.env.example` 增配
- [ ] 更新 [ARCHITECTURE.md](./ARCHITECTURE.md)：USP 接缝一节扩为「外部任务源（可插拔）」
- [ ] 更新 [CODEBASE_OVERVIEW.md](./CODEBASE_OVERVIEW.md)：新增 `app/integrations/`

---

## 十四、风险与取舍

| 风险/取舍 | 说明 | 应对 |
|---|---|---|
| 抽象成本 | 多一层 `ExternalTask` + 一个 ABC | 被"未来替换/增源"需求 justify，控制在"够用"线 |
| 状态覆盖 | 单向同步下本平台推进可能被外部拉回 | 引擎"取较后状态"规则保护 |
| 账号漏配 | 禅道账号未在映射表 → 指派落空 | 未命中留空走 AI 派单；realname 记 metadata 供补配 |
| 禅道字段版本差异 | `executi1n` 笔误、`assignedTo` 对象结构 | 保留 `candao_dev/README.md` 记录的容错 |
| 数据量与限速 | 全量拉取在任务多时偏慢 | 一期全量；监控后改 `lastEditedDate` 增量 |
| Airflow 引入 | 多一套基础设施 | 仅作定时触发器，DAG 极薄；可随时换 Celery beat/cron |
