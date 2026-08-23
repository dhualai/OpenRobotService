# 实时评论 WebSocket 设计方案（轻量 IM 模式）

> 状态：**已落地（Phase 0-4 全部实现）**。本文描述将「工单/任务详情页评论区」改造为类聊天软件实时体验的完整方案。
> 适用范围：历史工单详情页（`frontend/src/pages/call/TicketDetailPage.tsx`）+ 系统任务详情页（`frontend/src/pages/tasks/TaskDetailPage.tsx`）。
> 技术选型：**FastAPI 原生 WebSocket**（零额外重依赖）。实时范围：**评论 CRUD 实时推送 + 在线状态 + 输入中提示 + 已读回执 + 工单状态变更推送**。

---

## 1. 背景与现状

### 1.1 现状痛点

当前两个详情页的评论区都是**拉取模式**：

- 进入页面：`GET /api/tasks/{id}?load_comments=true` 拉历史；
- 发评论：`POST /api/tasks/{id}/comments` → 成功后再 `GET` 全量刷新（`fetchComments` / `fetchDetail(true)`）；
- 别人的新评论**不会自动出现**，必须手动刷新或重进页面；
- 「派单中」状态靠前端 `setInterval(() => fetchDetail(true), 5000)` 轮询轮询，浪费请求。

这与「把评论区打造成轻量聊天软件」的目标不符。

### 1.2 目标

建立**消息订阅（发布-订阅）模式**：

- 进入详情页即订阅该工单/任务的 WebSocket 房间；
- 任何人在房间里发评论/编辑/删除，房间内所有人**实时收到**并自动上屏；
- 显示**谁在线**、**谁正在输入**、**消息已读回执**；
- 工单状态流转（派单完成、状态变更）也通过 WS 实时推送，替代 5s 轮询。

### 1.3 关键事实（决定设计走向）

- 两个详情页**共用同一个 `DiscussionPanel` 组件**（`TaskDetailPage.tsx:9/900`、`TicketDetailPage.tsx:23/784`）。
  → **只需改造 `DiscussionPanel` 一处，两页同时生效**，无需分别改两个详情页。
- 前端 REST base 路径：`/api/tasks`（dev）、`/t/api/tasks`（测试）、`/p/api/tasks`（生产），由 `frontend/src/config/api.ts` 的 `ENV_PREFIX` 自动推导。
  → WS 复用**同一前缀**，仅协议由 `http(s)` 换为 `ws(s)`，由 nginx 透传 `Upgrade`。
- 后端当前为**单进程** `uvicorn.run("app:app", ...)`（`backend/main.py`），内存房间广播即可；多实例扩展见 §9 前瞻。

---

## 2. 总体架构

```
┌─────────────── 浏览器 A（工单详情页） ───────────────┐        ┌─────────────── 浏览器 B（同一工单） ───────────────┐
│  DiscussionPanel                                      │        │  DiscussionPanel                                     │
│    └─ useTaskCommentsWS(taskId)                        │        │    └─ useTaskCommentsWS(taskId)                       │
│         └─ WebSocket(`/api/tasks/{id}/ws?token=...`)   │        │         └─ WebSocket(`/api/tasks/{id}/ws?token=...`)  │
└───────────────────────────────┬───────────────────────┘        └──────────────────────────────┬───────────────────────┘
                                 │  wss                                    wss                   │
                                 ▼                                                  ▼
=====================  nginx（/t/api/、/p/api/ 增加 Upgrade 透传）  =====================
                                 │ 透传（Connection: upgrade）
                                 ▼
=================  业务后端 FastAPI（单进程）  =================
│  ConnectionManager（按 task_id 分房间：连接集合 / 在线成员 / typing 态）          │
│  WebSocket 端点 /api/tasks/{task_id}/ws                                          │
│  REST 评论接口插入广播：                                                          │
│    POST /{id}/comments  → broadcast(comment.created)                             │
│    PUT  /comments/{id}  → broadcast(comment.updated)                             │
│    DEL  /comments/{id}  → broadcast(comment.deleted)                             │
│    PATCH /{id}/status、POST /{id}/assign → broadcast(task.updated)              │
│  AI 讨论回复落库 → broadcast(comment.created)  ← 替代 fetchDetail(true) 轮询      │  （✅ 已接入，见 §11.2）
└==================================================================================┘
```

**数据流原则**：评论本身仍 **POST 写库**（持久化不变），WS 只承担「变更通知 + 实时状态」。客户端收到通知后**增量更新**，不再全量 `GET` 刷新（首屏仍用一次 `GET` 拉历史 + 在线快照）。

---

## 3. 后端设计

### 3.1 新增文件 `backend/app/modules/tasks/api/ws.py`

负责 WebSocket 端点与 `ConnectionManager` 实例（模块级单例，随 app 进程存活）。

### 3.2 ConnectionManager（内存房间模型）

```python
class WsConnection:
    ws: WebSocket
    username: str
    name: str
    last_ping: float

class ConnectionManager:
    # task_id -> 连接集合（同一用户多端可多条连接）
    rooms: dict[int, set[WsConnection]]
    # task_id -> typing 中的 username 集合（内存态，不持久化）
    typing: dict[int, set[str]]

    async def connect(self, task_id, conn): ...      # 加入房间
    def disconnect(self, task_id, conn): ...         # 移出房间 + 触发离线广播
    async def broadcast(self, task_id, payload): ... # 给房间内所有连接发 JSON
    def online_members(self, task_id) -> list[dict]: ...  # 按用户去重的在线成员 [{username, name, avatar_resource_id}]
```

- **在线状态**：连接建立即「在线」，断开即「离线」；内存维护 `online_members(task_id)`，**按 username 去重**（同一用户多客户端算一人），返回 `[{username, name, avatar_resource_id}]` 供前端渲染头像。
- **输入中**：`typing[task_id]` 内存集合，超时（如 5s 无新 typing 事件）自动清除。
- **心跳**：客户端每 25s 发 `ping`，服务端回 `pong`；超过 60s 未收到 ping 判为掉线，触发 `disconnect`。

### 3.3 鉴权

浏览器原生 `WebSocket` **不支持自定义 Header**，token 走 **query 参数**：

```
GET /api/tasks/{task_id}/ws?token=<JWT>
```

服务端在 `websocket_endpoint` 内复用现有 JWT 解析逻辑（与 `get_current_active_user_from_token` 同源）校验；失败则 `await ws.close(code=4401)` 并拒绝。
**安全注意**：token 会出现在 URL，但全程走 `wss`（TLS），与现有 `Authorization: Bearer` 同等级别；同时服务端应对单用户连接数做上限（如 ≤5），防滥用。

### 3.4 WebSocket 端点

```python
@router.websocket("/{task_id}/ws")
async def ws_task_room(websocket: WebSocket, task_id: int, token: str = Query(None)):
    # 1. 校验 token → 解析 username/name
    # 2. 校验 task 存在（否则 4404）
    # 3. await websocket.accept()
    # 4. manager.connect(task_id, conn)
    # 5. 发送 welcome + 在线成员快照 + 最新已读游标
    # 6. 循环接收客户端帧（见 §3.5），按类型处理
    # 7. 异常/断开 → manager.disconnect + broadcast(presence 离线)
```

### 3.5 消息协议（JSON 帧）

#### 客户端 → 服务端

| type | 字段 | 说明 |
|------|------|------|
| `ping` | — | 心跳，服务端回 `pong` |
| `typing` | `value: bool` | 开始/停止输入；停止或 5s 未续则自动清除 |
| `read` | `last_read_comment_id: int`, `comment_ids?: int[]` | 上报已读：游标（最后一条 id）+ 本次实际读到的评论 id 列表（飞书式名单） |
| `fetch_history` | `after_id?: int` | （可选）断线重连后增量拉取缺失评论 |

#### 服务端 → 客户端

| type | 字段 | 说明 |
|------|------|------|
| `welcome` | `you`, `online: string[]`, `read_map: {username:comment_id}`, `read_records: {comment_id: [{username,name,avatar_resource_id,read_at}]}` | 连接成功快照（含已读名单） |
| `comment.created` | `comment: {...}` | 新评论（含附件/引用/创建人） |
| `comment.updated` | `comment: {...}` | 评论编辑 |
| `comment.deleted` | `id: int` | 评论删除 |
| `presence` | `online: [{username, name, avatar_resource_id}]`（按用户去重） | 在线成员变化 |
| `typing` | `username: string`, `value: bool` | 某人输入中 |
| `read_receipt` | `username: string`, `last_read_comment_id: int|null`, `comment_ids?: int[]`, `records?: [{comment_id,username,name,avatar_resource_id}]` | 已读回执（游标 + 名单增量） |
| `task.updated` | `status`, `assigned_to`, `assigned_to_name`, ... | 工单字段变更（替代 5s 轮询） |
| `pong` | — | 心跳回应 |
| `error` | `code`, `message` | 错误（鉴权失败/房间不存在等） |

> `comment.created/updated/deleted` 的 `comment` 结构与现有 `TicketCommentResponse` 完全一致，前端零转换成本。

### 3.6 在 REST 接口插入广播（改写点）

在 `backend/app/modules/tasks/api/task.py` 的现有接口**成功后**追加一行广播：

- `add_comment`（第 375 行之后）：`await manager.broadcast(task_id, {"type":"comment.created","comment": comment.model_dump()})`
- `update_comment`（第 519 行）：广播 `comment.updated`
- `delete_comment`（第 555 行）：广播 `comment.deleted`
- `update_task_status`（第 583 行）/ `assign_task`（第 615 行）/ `update_task`（第 305 行）：广播 `task.updated`
- **AI 讨论回复落地广播（✅ 已接入）**：AI 回复由独立 AI 服务进程写库，写库后经后端内部端点 `POST /api/tasks/{id}/internal/broadcast-comment`（复用 `X-API-Key` = `HELPDESK_SYNC_API_KEY`，与派单通知同源）回推 `comment.created`，使在线客户端实时收到 AI 回复（替代前端 `fetchDetail(true)` 全量刷新）。详见 §11.2。

> 广播调用需 `try/except` 包裹，WS 异常**不得影响主流程**（REST 已返回 200）。

### 3.7 数据模型（仅已读回执需持久化）

新增表 `task_comment_read`（typing / presence 为内存态，不落库）：

```python
class TaskCommentRead(Base):
    __tablename__ = "task_comment_read"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, index=True)
    username = Column(String(64), index=True)
    last_read_comment_id = Column(Integer)
    updated_at = Column(DateTime, default=func.now())
    __table_args__ = (UniqueConstraint("task_id", "username", name="uq_task_user_read"),)
```

- 客户端发 `read` → upsert `(task_id, username, last_read_comment_id)` → 广播 `read_receipt` 给房间。
- 评论项展示：根据各成员 `last_read_comment_id` 计算「N 人已读」/ 双勾「已读」。

**已读名单（飞书式，Phase 5 落地）**：新增表 `task_comment_read_record`，逐条评论记录「谁在何时读」，支持「每条消息的已读人员名单 + 按阅读时间倒序」：

```python
class TaskCommentReadRecord(Base):
    __tablename__ = "task_comment_read_record"
    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), index=True)  # 级联删除
    comment_id = Column(BigInteger, index=True)
    username = Column(String(50), index=True)
    read_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("comment_id", "username", name="uq_comment_read_user"),)
```

- 与 `task_comment_read`（游标）互补：游标算「读到哪」，明细表出「每条消息的名单」。
- 幂等写入：唯一键 `(comment_id, username)`，`INSERT` 前查重，重复不新增。
- 名单查询按 `read_at` 倒序（`_read_records_map`），联查 `users` 补 `name`/`avatar_resource_id`。
- `welcome` 下发全量 `read_records` 快照；`read_receipt` 增量广播 `records` 供前端合并。

---

## 4. 前端设计

### 4.1 WS 客户端封装 `frontend/src/api/ws.ts`

```ts
export function buildWsUrl(path: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const root = API_ROOT; // '/api' | '/t/api' | '/p/api'
  const token = getToken();
  return `${proto}://${location.host}${root}${path}?token=${encodeURIComponent(token || '')}`;
}

export class TaskRoomSocket {
  // 连接 /{task_id}/ws，自动重连（指数退避 1s→2s→4s…≤10s），心跳 ping/pong
  // 事件分发：on(event, handler)；send(obj)
  // 暴露 sendTyping(value)、sendRead(commentId)
}
```

- **自动重连**：断线后指数退避重连；重连成功后发 `fetch_history`（带本地最新 `comment.id`）增量补齐，并全量 `GET` 一次历史做最终对齐（防丢消息）。
- **鉴权**：token 取自 `client.ts` 的 `getToken()`，随 URL 传递；token 刷新后重连即用新 token。

### 4.2 `useTaskCommentsWS(taskId)` Hook

```ts
function useTaskCommentsWS(taskId: string | number) {
  // 返回 { online: string[], typingUser: string|null, readMap, sendTyping, sendRead, onRemoteEvent }
  // 内部维护 TaskRoomSocket 生命周期（mount 建连 / unmount 关连）
}
```

### 4.3 DiscussionPanel 改造（核心，两页共用）

文件：`frontend/src/shared/components/DiscussionPanel.tsx`

改动点：

1. **首屏**：保留 `GET /api/tasks/{id}/comments` 拉历史（作为基线）。
2. **订阅**：进入即 `useTaskCommentsWS(taskId)` 建连。
3. **增量更新（去重关键）**：
   - 收到 `comment.created` → 若 `comments` 中**不存在该 id** 则 append；已存在则忽略（防止自己乐观更新 + 广播重复）。
   - 收到 `comment.updated` → 按 id 替换；`comment.deleted` → 按 id 过滤。
   - 自己发评论：`POST` 成功后**乐观更新**（本地插入返回的真实 id 记录），WS 广播到达因 id 已存在而被忽略 → 天然去重，无重复气泡。
4. **在线状态条**：讨论区顶部展示在线成员头像（`online` 列表，按用户去重），有 `avatar_resource_id` 渲染图片头像，无则回退首字母；离线/上线经 `presence` 事件实时更新。
4a. **微信化消息 UI**：每条消息带头像（他人左/自己右，从 `online` 携带的 `avatar_resource_id` 或当前用户 `authStore.avatarResourceId` 取，无图回退首字母）；同作者连续消息（间隔 < 5min）省略头像/姓名/尾巴并缩小间距；气泡带 CSS 小尾巴三角；自己消息改微信绿 `#95EC69`；消息区高度改为弹性 `flex:1`（最大 60vh）；新消息提示条——滚在历史区时不强制跳底，改为显示「↓N 条新消息」悬浮条，点击跳底。
5. **输入中提示**：评论输入框 `onChange` 时 `sendTyping(true)`，停 3s 自动 `sendTyping(false)`；收到他人 `typing` 事件显示「XXX 正在输入…」。
6. **已读回执（含飞书式已读名单）**：
   - 列表滚动到底 / 新消息到达时，自动 `sendRead(最新comment.id, 本次实际读到的评论id列表)`；
   - 每条自己消息根据 `readRecords`（精确名单）或 `readMap`（游标兜底）显示「已读 N 人」；
   - 点击「已读 N 人」弹出名单 Popover（头像 + 姓名 + 相对阅读时间，按阅读时间倒序）。
7. **工单状态实时**：收到 `task.updated` → 回调上层更新 `ticket.status / assigned_to / assigned_to_name`，**移除现有的 5s 派单轮询**（TicketDetailPage.tsx:360-364）。
8. **卸载**：关闭 socket，清定时器。

> 设计约束：WS 仅做「增量通知」，不替代首屏 `GET`；任何 WS 异常都**降级**为现有轮询/手动刷新，保证功能不回退。

### 4.4 两个详情页接入

- `TicketDetailPage` 与 `TaskDetailPage` 均通过 `DiscussionPanel` 渲染评论区，**无需各自改动**；
- 仅需把 `task.updated` 事件回调接到各自已有的 `setTicket` 逻辑（一行的 `onTaskUpdated` prop）。

---

## 5. nginx 配置改动

当前 `deploy/nginx/conf/nginx.conf` 的 `/t/api/`、`/p/api/` **未开启 WebSocket 透传**。需增加标准 `Upgrade` 处理（参考文件内已有 `/minio/ws/`、`/airflow/` 写法）：

```nginx
# 在 http {} 顶部增加
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

# 修改 /t/api/ 与 /p/api/ 两个 location，增加：
location /t/api/ {
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;   # WS 长连接，延长读超时
    proxy_pass http://test_backend/api/;
}
# /p/api/ 同理改为 prod_backend
```

> `map` 让普通 REST 请求（`Connection: ''` → `close`）与 WS 请求（`Connection: upgrade`）共存，不影响现有接口。

---

## 6. 鉴权与安全

- token 经 `wss` 加密传输，与 `Bearer` 等价；服务端复用现有 JWT 校验，失败直接关闭。
- 单用户连接数上限（如 ≤5），防连接耗尽。
- 广播内容**不含 token/密码**等敏感字段，仅评论与状态数据。
- 评论增删改的**写权限仍由 REST 接口的现有鉴权保证**（WS 只推送，不接收写操作）。

---

## 7. 边界与风险

| 风险 | 处理 |
|------|------|
| 消息重复（乐观更新 + 广播） | 以服务端评论 `id` 去重，已存在则忽略 |
| 断线期间漏消息 | 重连后 `fetch_history`（after_id 增量）+ 全量 `GET` 对齐 |
| AI 讨论回复实时性 | AI 回复落库时广播 `comment.created`，替代 `fetchDetail(true)` 轮询 |
| WS 服务异常 | 降级为现有 `GET` 刷新 / 手动刷新，功能不回退 |
| 派单轮询去留 | `task.updated` 推送稳定后移除 5s 轮询，先双轨并存再切 |
| 单进程内存房间重启丢失 | 重启后客户端自动重连 + 首屏 `GET` 重建状态，可接受 |
| 心跳误判离线 | 25s ping / 60s 超时，弱网下退避重连 |

---

## 8. 实施阶段（建议顺序）

- **Phase 0 — 基础设施（✅ 已落地）**：`ws.py`（ConnectionManager + 端点）、`TaskCommentRead` 模型 + Alembic 迁移（`9f3b7c2a1d40`）、nginx `Upgrade` 配置。
- **Phase 1 — 评论实时（✅ 已落地）**：REST 三接口（`add_comment`/`update_comment`/`delete_comment`）插入广播；前端 `ws.ts` + `useTaskCommentsWS` + DiscussionPanel 增量更新（按 id 去重）。**核心聊天体验已可用。**
- **Phase 2 — 在线状态 + 输入中（✅ 已落地）**：presence / typing 广播与 UI（在线成员条 + 「XXX 正在输入…」）。
- **Phase 3 — 已读回执（✅ 已落地）**：`task_comment_read` upsert 读写 + `read_receipt` 广播 + 回执 UI（自己的消息「已读」标记）。
- **Phase 4 — 状态变更推送（✅ 已落地）**：`task.updated` 广播（`update_task_status`/`assign_ticket`/`update_task`），**已移除**两详情页 5s 派单轮询。
- **Phase 5 — 已读人员名单（✅ 已落地）**：新增 `task_comment_read_record` 表，逐条评论记录读者与阅读时间；`welcome`/`read_receipt` 下发名单快照与增量；前端「已读 N 人」可点击弹出名单 Popover（头像 + 姓名 + 相对时间，按阅读时间倒序），对齐飞书已读体验。

---

## 9. 未来扩展（多实例/多 worker）

当前单进程内存房间够用。若日后 `uvicorn --workers N` 或多副本部署：

- `ConnectionManager` 抽象为接口，后端实现切换为 **Redis pub/sub**（每个进程订阅 `task:{id}` channel，本地内存仅维护本进程连接）；
- 在线/typing 状态可放 Redis（带 TTL），实现跨进程一致；
- 前端与单实例方案完全兼容，无需改动。

---

## 10. 测试要点

- 单浏览器开两个标签页同一工单：A 发评论，B 实时出现；A 删除，B 实时消失。
- 输入中：A 输入，B 显示「A 正在输入…」；停 3s 消失。
- 在线：A 进入，B 在线列表出现 A；A 关闭，B 列表移除 A。
- 已读：A 滚动到底，B 的对应评论显示「A 已读」。
- 派单：后台 assign 后，前端 `task.updated` 实时刷新处理人，5s 轮询被移除。
- 断网重连：断网期间 B 发的评论，A 重连后补齐不丢失、不重复。
- 鉴权：无 token / 错误 token 连接被拒（4401）。

---

## 11. 落地状态（实现记录）

### 11.1 已交付文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `backend/app/modules/tasks/api/ws.py` | 新增 | WebSocket 端点 + `ConnectionManager`（内存房间）+ 广播封装 `ws_broadcast_*` |
| `backend/app/models/task.py` | 修改 | 新增 `TaskCommentRead`（`task_comment_read` 表） |
| `backend/alembic/versions/9f3b7c2a1d40_add_task_comment_read.py` | 新增 | 建表迁移（down_revision `1120f2c12ed6`） |
| `backend/app/modules/tasks/__init__.py` | 修改 | 挂载 `ws_router` |
| `backend/app/modules/tasks/api/task.py` | 修改 | 6 个 REST 接口成功后插入广播（try/except 包裹） |
| `frontend/src/api/ws.ts` | 新增 | `buildWsUrl` + `TaskRoomSocket`（指数退避重连 + 心跳 ping/pong） |
| `frontend/src/shared/hooks/useTaskCommentsWS.ts` | 新增 | WS 订阅 hook（去重合并 + 在线/输入中/已读/状态变更） |
| `frontend/src/shared/components/DiscussionPanel.tsx` | 修改 | 接入 WS（在线条/输入中/已读标记），两详情页共用 |
| `frontend/src/pages/tasks/TaskDetailPage.tsx` / `TicketDetailPage.tsx` | 修改 | 移除 5s 轮询，接 `onTaskUpdated` |
| `frontend/src/shared/styles/global.css` | 修改 | 在线条/输入中/已读 样式 |
| `deploy/nginx/conf/nginx.conf` | 修改 | `map $http_upgrade` + `/t/api`、`/p/api` Upgrade 透传 |
| `backend/app/modules/tasks/api/task.py` | 修改 | 新增内部端点 `POST /{id}/internal/broadcast-comment`（X-API-Key 鉴权），供 AI 服务回调广播 AI 评论 |
| `ai/agents/AiTaskPlatform/pipeline.py` | 修改 | AI 写评论复用方法写库后 best-effort 回调上述端点（跨进程 pub-sub，讨论/摘要/诊断全覆盖） |
| `backend/apply_task_comment_read_migration.py` | 新增 | 生产一键幂等建表脚本（替代 `alembic upgrade head`，规避多 head 问题） |
| `backend/app/models/task.py` | 修改 | 新增 `TaskCommentReadRecord`（`task_comment_read_record` 表，飞书式已读名单） |
| `backend/alembic/versions/3c2d1e0f9a8b_add_task_comment_read_record.py` | 新增 | 建表迁移（down_revision `1a2b3c4d5e6f`，本地保留不推送） |
| `backend/app/modules/tasks/api/ws.py` | 修改 | `read` 上报支持 `comment_ids` 列表；`welcome`/`read_receipt` 下发名单；新增 `_mark_comment_read`/`_read_records_map` |
| `frontend/src/api/ws.ts` | 修改 | 新增 `ReadRecord`/`ReadRecordDelta` 类型；`sendRead` 支持 `commentIds`；`welcome`/`read_receipt` 字段扩展 |
| `frontend/src/shared/hooks/useTaskCommentsWS.ts` | 修改 | 新增 `readRecords` 状态，处理 `welcome.read_records` 与 `read_receipt.records` 增量合并 |
| `frontend/src/shared/components/DiscussionPanel.tsx` | 修改 | 「已读 N 人」可点击弹出名单 Popover（头像+姓名+相对时间倒序）；上报实际读到的评论 id 列表 |
| `frontend/src/shared/styles/global.css` | 修改 | 已读按钮样式 + 名单弹层样式 |

### 11.2 与设计的偏差

- **单用户连接数上限**（`MAX_CONN_PER_USER = 5`）已在 `ws.py` 预留常量，但当前未在 `connect` 处强制拦截（单进程场景风险低，后续可加）。
- **AI 讨论回复落地广播（✅ 已接入）**：AI 服务写 `task_comments` 是独立进程，不持有后端 WS 连接。改为在写库复用方法 `_add_diagnosis_comment_short` 中 best-effort 回调后端内部端点 `POST /api/tasks/{id}/internal/broadcast-comment`（复用 `X-API-Key` = `HELPDESK_SYNC_API_KEY`，与派单通知同源），由后端加载评论并广播 `comment.created`，讨论/摘要/诊断三类 AI 评论一处全覆盖。在线客户端无需 `fetchDetail(true)` 等全量刷新即可实时上屏 AI 回复。
- **typing 超时自动清除**：当前依赖前端 3s 后主动 `sendTyping(false)`，服务端不额外做超时兜底（见 §7 风险表）。
- **删除即时消失**：前端 `useTaskCommentsWS` 维护 `deletedIdsRef`（已删除评论 id 集合），WS `comment.deleted` 事件到达即从 `displayComments` 移除并记入 `deletedIdsRef`；基线（父级 comments）刷新合并时一律排除 `deletedIdsRef` 中的 id，避免「删除后基线未更新又被补回」导致需刷新页面才消失。长按菜单的「删除」为唯一删除入口（已移除气泡右上角垃圾桶图标，避免与长按菜单重复）。
- **进入即显示最新消息（贴底跟随）**：`DiscussionPanel` 在消息列表外层 `.detail-chat-messages`（滚动容器）内包一层 `.detail-chat-messages__inner`（内容容器，`chatContentRef`），用 `ResizeObserver` 监听其高度变化。用户处于贴底态（含初始进入，`isAtBottomRef` 初值 `true`）时，内容增高（图片附件加载、Markdown 渲染、消息追加等撑高）自动 `scrollTop = scrollHeight` 跟随滚到底；用户主动上滚阅读历史时不打断，仅显示「↓N 条新消息」提示条。解决此前「进入后停在顶部」「已读消息卡在底部边框被截断」等问题——根因是 `scrollToBottom()` 在 `useEffect` 同步执行时图片/Markdown 等异步内容尚未撑高 `scrollHeight`，导致滚不到底。

### 11.3 验证建议

按 §10 测试要点回归：双标签页实时收发、输入中、在线上下线、已读回执、派单 `task.updated` 实时刷新、断网重连补齐不重复、无 token 拒绝（4401）。微信化 UI 回归：每条消息头像正确（自己/他人）、连续消息合并省略、气泡尾巴与绿色配色、滚历史时新消息提示条出现且点击跳底、贴底时自动跟随。
```

