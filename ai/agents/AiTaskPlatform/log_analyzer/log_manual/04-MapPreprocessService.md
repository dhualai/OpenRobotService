# 04 - MapPreprocessService（地图预处理服务）

## 1. 职责

将原始地图数据（JSON）转换成算法能高效使用的数据结构。这是系统中最耗计算资源的模块：

- 基础预处理：moving_shape → distance_matrix → conflict_matrix
- 分级预处理 (Level 2+)：载具组合凸包、增量冲突计算
- 进度收集：向 ProgressCollector 上报，供后端轮询
- 派单调度：LoadLevelDispatcher 控制分级任务的执行顺序

## 2. 关键代码位置

| 文件 | 内容 |
|------|------|
| `uspa_services/services/map_preprocess_service/map_preprocess_service_actor.py` | 所有 Actor (1786 行) |
| `uspa_services/services/map_preprocess_service/tools.py` | 计算工具类 |
| `uspa_services/utils/data_validator/map_data_validator.py` | 地图数据校验 |

## 3. 涉及的 Actor

| Actor | max_concurrency | namespace | 定位 |
|-------|-----------------|-----------|------|
| MapPreprocessServiceActor | 4 | USP-MAP-PREPROCESS-SERVICES | 入口，单图循环处理 |
| ProgressCollector | 未限制 | USP-MAP-PREPROCESS-SERVICES | 进度收集 |
| LoadLevelDispatcher | 4 | USP-MAP-PREPROCESS-SERVICES | 分级任务派单 (全局单例) |
| DistributedPreprocessor | 2 | USP-MAP-PREPROCESS-SERVICES | 单张图的实际预处理 (dynamic创建) |

## 4. 预处理数据产出

```
/usp_algorithm_data/                        ← 由 map_dir 配置
├── none_preprocessed_maps/{map_id}/{hash}.json       ← Level 0: 原始 (无预处理)
├── minimum_preprocessed_maps/{map_id}/{hash}.json    ← Level 1: 最小可用 (仅车型)
├── preprocessed_maps/{map_id}/{hash}.json            ← Level 2: 完整 (车型+Level1载具)
├── load_level_preprocessed_maps/{map_id}/{hash}.json ← Level 3+: 分级精化
└── ignore_storages/{map_id}/{hash}.xlsx              ← 库位白名单 (Excel)
```

## 5. 进度状态机

```
UNTREATED → QUEUED → PROCESSING → FINISHED
                         │              │
                         ▼              ▼
                       ERROR         (磁盘已有) → 直接 FINISHED

QUEUED: 已收到 /preprocess 请求但尚未派发
        对外通过 update_preprocess_state 映射为 PROCESSING

cancelled 标志: 独立的取消标记，不改变 state
```

## 6. 业务日志全链路

### 6.1 预处理请求入口 (`/preprocess`)

```
"预处理开始----{request_id}"
"预处理请求地图数据为空----{request_id}"                 ← maps 为空

"Map id:{id}----Map Hash:{hash}"
"地图无需预处理 mapId: {id} mapHash: {hash}"             ← need_preprocess=False
```

### 6.2 基础预处理流程

```
[地图初始化]
"地图初始化中"                                           ← PathPlanningMap 构造开始 (progress=0.05)
"地图初始化完成"                                         ← PathPlanningMap 构造完成 (progress=0.05)

[创建 worker]
"开始基础预处理 mapId: {id} mapHash: {hash}"

[完整顺序]
preprocess_base() 内的步骤:
  1. moving_shape 计算 (车型)
  2. 载具 Level 1 moving_shape 计算
  3. minimum 落盘 + 通知 dmap ("最小可用化预处理完成")
  4. distance_matrix 计算
  5. conflict_matrix 计算 (车型)
  6. 载具 Level 1 冲突计算
  7. preprocessed_maps 落盘 + 通知 dmap ("预处理完成")
```

### 6.3 各阶段进度上报

```
[ProgressCollector.report_progress]
"Report progress-----Map id:{id}-----Map hash:{hash}---
  Module:{module}---Module Progress:{p}---
  Module State:{state}---Progress:{total}--
  Cancelled:{cancelled}---Message:{msg}"
```

进度模块及权重 (用于计算总百分比):

| 模块 | 权重 | 含义 |
|------|------|------|
| moving_shape | 0.15 | 扫掠形状计算 |
| distance_matrix | 0.40 | 全图距离表 |
| conflict_matrix | 0.45 | 冲突矩阵计算 |

总百分比 = Σ(module_progress × weight)，上限 100%

### 6.4 基础预处理完成

```
输出性能统计:
"=== 地图 {map_id} 基础预处理性能分析 ==="
"基础总耗时: {total:.2f}s"
"moving_shape      {time:.2f}s ({pct:.1f}%)"
"distance_matrix   {time:.2f}s ({pct:.1f}%)"
"conflict_matrix   {time:.2f}s ({pct:.1f}%)"
"load_level        {time:.2f}s ({pct:.1f}%)"             ← Level 1 载具处理
"  Level 1: moving_shape={ms:.2f}s, conflict={cf:.2f}s"

"基础预处理完成 mapId: {id} mapHash: {hash}"
```

### 6.5 分级预处理 (Level 2+)

```
[提交给 Dispatcher]
"分级任务已提交 Dispatcher mapId: {id} mapHash: {hash}"
     或
"Dispatcher.submit 失败 hash={hash} err={e}；放弃分级，kill worker"

[Dispatcher 派发]
"[Dispatcher] submit map_id={id} hash={hash} queued_size={n}"
"[Dispatcher] 派发分级 map_id={id} hash={hash}"

[每级循环]
"Level {n}: 新增movers = {mover_ids}"
"Level {n}: memory_ratio = {ratio} (current={mb}MB, total={mb}MB)"
"内存超限，停在 level {n}"                                ← 超过 max_memory_ratio (默认 0.8)
"没有新的movers，停止分级于level {n}"                     ← 无可继续切分的组

[完成/失败]
"[Dispatcher] 分级完成 hash={hash} levels={n}"
"[Dispatcher] 分级失败 hash={hash}: {message}"
```

### 6.6 分级事件上报

```
"Report hierarchy event-----Map id:{id}---Map hash:{hash}---
  Type:{type}---Level:{n}---Extra:{extra}"

事件类型:
  hierarchy_level_started    level=N
  hierarchy_level_completed  level=N   extra={"path":"..."}
  hierarchy_finished         level=None extra={"reason":"no_new_mover|memory_exceeded|failed"}
```

### 6.7 状态查询 (`/updatePreprocessState`)

```
"Updated status-----Map id:{id}-----Map hash:{hash}---
  Module:{module}---Module Progress:{p}---
  Module State:{state}---Progress:{total}--
  Cancelled:{cancelled}---Message:{msg}"
```

### 6.8 取消预处理 (`/cancelPreprocess`)

```
"/cancelpreprocess mapHash={hash} 开始撤销"
"cancel_all_children 超时({n}s) hash={hash}，继续 ray.kill parent"
"cancel_all_children 异常 hash={hash} err={e}"

# Dispatcher 层面的取消
"Dispatcher.cancel 失败 hash={hash} err={e}"
"/cancelpreprocess Dispatcher cancel 结果 hash={hash} {result}"

"Mark cancelled-----Map hash:{hash}---State:{state}"
```

### 6.9 异常

```
[DistributedPreprocessor Actor 异常死亡]
"DistributedPreprocessor actor 异常死亡 mapId:{id} mapHash:{hash} err={e}"

[一般预处理失败]
"预处理失败---{traceback}"
"预处理失败：{error_msg}"                                 ← 通过 ProgressCollector 上报

[取消后处理]
"预处理被撤销 mapId:{id} mapHash:{hash}"                  ← cancelled 场景
```

### 6.10 配置更新

```
"服务: MAP-PREPROCESS---收到动态修改配置参数请求: {data}"
```

## 7. LoadLevelDispatcher 内部状态

```
"[Dispatcher] status:"
  queued: {n}        ← 排队中的任务数
  running: {n}       ← 正在运行的任务数
  max_workers: {n}   ← 最大并发数
  queue_hashes: [...]  ← 排队 hash 列表
  running_hashes: [...] ← 运行中 hash 列表
```

## 8. 常见异常及排查

### 8.1 预处理总是不开始

```
症状: /preprocess 返回 200 但进度一直是 QUEUED/PROCESSING

排查步骤:
  1. 查配置 turn_on 是否为 True
  2. 查日志: "预处理开始----{request_id}" → 确认请求被接收
  3. 查日志: "地图无需预处理" → 是否被 need_preprocess 判定为不需要
  4. need_preprocess 逻辑: 
     - ProgressCollector 的 state 必须为 "UNTREATED" 或 "QUEUED"
     - 且 hashes_to_process[hash] 为 True (代表是本批次请求)
     - 如果磁盘已有 preprocessed_maps/{id}/{hash}.json → 直接 FINISHED
  5. 查 update_preprocess_state → 看 cancelled 是否为 True
```

### 8.2 预处理卡住不动 (进度条不动)

```
症状: 进度在某步卡住很久

排查步骤:
  1. 看当前在哪个模块:
     "Report progress-----Module:{module}---Module Progress:{p}"
     
  2. 如果在 moving_shape 开头卡住:
     → PathPlanningMap.__init__ 很耗时 (bigmap 30-97s)
     → 日志 "地图初始化中" → "地图初始化完成" 之间无输出是正常的

  3. 如果在 distance_matrix 卡住:
     → 全图节点数很大，N×N 距离计算很耗时
     → 检查 num_sub_workers (默认 8) 是否足够

  4. 如果在 conflict_matrix 卡住:
     → 这是最耗时的阶段，shapely 几何计算
     → cancel 时会有 "cancel_all_children" 日志

  5. 如果在分级阶段卡住:
     → 看 "[Dispatcher] status" → 是否 running 但长时间不完成
     → 内存可能接近上限
```

### 8.3 OOM (预处理内存超限)

```
症状: "内存超限，停在 level {n}"、"DistributedPreprocessor actor 异常死亡"

原因:
  - 地图太大，所有分级层的 moving_shape 和 conflict_set 占满内存
  - max_memory_ratio 默认 0.8 (占用总内存 80% 时停止)

排查:
  1. 查日志: "memory_ratio = {ratio}"
  2. 降低 max_memory_ratio 让分级提前停止
  3. 减小 num_sub_workers 减少并行 worker 数
  4. 确认 enable_load_level 是否为 True → 设为 False 不做分级
```

### 8.4 取消预处理不生效

```
症状: /cancelPreprocess 返回 200 但预处理还在跑

排查:
  1. 查日志: "/cancelpreprocess mapHash={hash} 开始撤销"
  2. 场景 A (基础尚在跑): 
     → cancel_all_children 是否超时
     → 如果 "cancel_all_children 超时({n}s)" → child task 没能在 15s 内终止
     → ray.kill(parent) 和 cancel 的并发竞争
  3. 场景 B/C (已交 Dispatcher):
     → "Dispatcher cancel 结果" → killed_running 是否为 True
```

### 8.5 Dispatcher 死锁

```
症状: "[Dispatcher] status" 显示 running 有任务但 queued 不动

排查:
  1. max_workers 默认为 1，新任务需要等当前完成
  2. 如果 running 长时间不完成 → 检查 DistributedPreprocessor 日志
  3. Dispatcher 后台线程 0.2s 一个周期，submit 和 consumer 并发安全
```

### 8.6 地图数据校验失败

```
症状: MapDataValidator 校验失败抛出异常

排查:
  1. 检查传入的 map JSON 是否完整
  2. 检查 robotTypes 和 loadTypes 中的 envelope2d 多边形是否合法
  3. 检查 points/lines/nodes/edges 的引用完整性
```

## 9. 配置参数速查

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `buffer` | 0.06 | 形状 buffer (米)，越大越宽松 |
| `max_memory_ratio` | 0.8 | 分级停止的内存比例 |
| `enable_distributed_logger` | True | DistributedPreprocessor 日志 |
| `enable_load_level` | False | 启用分级预处理 |
| `load_level_max_workers` | 1 | Dispatcher 并发上限 |
| `rotate_shape_precise` | True | 精确旋转扫掠 |
| `send_progress_interval` | 0.1 | 进度上报频率 |
| `turn_on` | True | 是否启用预处理服务 |
