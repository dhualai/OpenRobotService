# 03 - TMSService（交通管理服务 / 路径规划）

## 1. 职责

TMSService 负责**多车路径规划** (MAPF — Multi-Agent Path Finding)。它是一个管理层，本身不做计算，而是为每个地图单元创建一个子 Actor (`TMSMapServiceActor`) 来独立规划。

## 2. 关键代码位置

| 文件 | 内容 |
|------|------|
| `uspa_services/services/tms_service/tms_service_actor.py` | TMSServiceActor 主 Actor (178 行) |
| `uspa_services/services/tms_service/tms_map_service_actor.py` | TMSMapServiceActor 子 Actor (按地图单元) |
| `uspa_services/services/tms_service/exceptions.py` | 异常定义 |
| `uspa_services/services/service_actor.py` | MapBasedServiceActor 基类 |

## 3. 参数配置

| Actor 参数 | 值 |
|------------|-----|
| `max_concurrency` | 2 (TMSServiceActor) |
| `service_name` | `"TMS"` |
| `runner_interval` | 0.5s |
| `heartbeat_interval` | 5s |

## 4. 架构：父子 Actor 模式

```
TMSServiceActor (max_concurrency=2)
  │
  ├── run() 主循环
  │     ├── update_dynamic_map_actor()  ← 获取 DynamicMap 引用
  │     ├── update_map_ids()            ← 获取当前地图 ID 列表
  │     └── init_sub_actors()           ← 为新增的地图创建子 Actor
  │
  └── _sub_actors: Dict[str, TMSMapServiceActor]
        │
        ├── "map_001" → TMSMapServiceActor (max_concurrency=?)
        ├── "map_002" → TMSMapServiceActor (max_concurrency=?)
        └── "map_00N" → TMSMapServiceActor

子 Actor 命名: "TMS-SERVICES-ACTOR-{map_id}"
子 Actor namespace: 继承父 Actor 的 namespace
```

## 5. TMSServiceActor 日志

### 5.1 run() 主循环

```
"TMS-SERVICES-ALIVE------"                               ← 心跳 (每 5s)
"TMS-SERVICES-MAP-IDS-UPDATE------{map_ids}"              ← 地图列表更新
```

### 5.2 子 Actor 管理

```
"初始化TMS-{map_id} actor"                                ← 为某个地图单元创建子 Actor
```

首次创建时会调用 `actor.init_logger.remote()`，后续重建时复用 map_id 判断避免重复 init。

### 5.3 重置

```
"收到重置请求"                                           ← /reset
"关闭TMS-{map_id} actor"                                 ← ray.kill 子 Actor
```

### 5.4 配置更新

```
"服务: TMS---收到动态修改配置参数请求: {data}"
"更新各地图单元配置参数: {map_ids}"                       ← 转发给所有子 Actor
"TMS----设置已刷新"
```

子 Actor 接收配置变更：
```python
ray.get(actor.refresh_settings.remote(settings_data))
```

## 6. TMSMapServiceActor (子 Actor)

每个地图单元有一个独立的 `TMSMapServiceActor`，负责：

1. **监听 DynamicMap 数据变化**：机器人位置、路径状态
2. **调用 MAPF 算法**（Multi-Agent Path Finding）计算路径
3. **将计算结果通过 `dynamic_map.update_paths()` 同步回**

### 6.1 子 Actor 的关键能力（继承自 MapBasedServiceActor）

| 方法 | 来源 | 含义 |
|------|------|------|
| `update_dynamic_map_actor()` | MapBasedServiceActor | 获取 DynamicMap 引用 |
| `update_path_planning_map(map_id)` | MapBasedServiceActor | 同步当前地图单元的 PathPlanningMap |
| `get_robots_data(map_id)` | MapBasedServiceActor | 获取本层的 RobotData |
| `get_robots_data_snapshot(map_id)` | MapBasedServiceActor | 获取 RobotData 快照（不阻塞） |
| `get_robots_next_path_node()` | MapBasedServiceActor | 获取各车路径的下一个节点 |
| `update_traffic_locks(map_id)` | MapBasedServiceActor | 获取当前锁区 |

### 6.2 路径规划的核心流程（推断）

虽然 `tms_map_service_actor.py` 较复杂，但其核心逻辑是：

```
监听触发 (机器人变化 / 定时)
  │
  ├── 获取当前状态
  │     ├── get_robots_data_snapshot(map_id)  ← 获取机器人数据快照
  │     │   返回 (robots_data, paths_info, extra_triggered, obstacles, synchronize_t)
  │     │   如果 robots_data 为 None → 数据正在更新中，跳过本轮
  │     │
  │     └── update_path_planning_map(map_id)  ← 获取最新地图
  │
  ├── 检查是否需要重新规划
  │     ├── 新机器人加入
  │     ├── 已有机器人的路径被截断/取消
  │     ├── 新锁区变化
  │     └── extra_triggered (DynamicMap 在 cancel 后标记)
  │
  ├── 调用 MAPF 算法 (usp_algorithm_mapf)
  │     └── 计算多车协同路径
  │
  └── 将结果写回 DynamicMap
        └── dynamic_map.update_paths.remote(map_id, multi_paths, line_registration, start_cal_t)
```

### 6.3 update_path_planning_map 的特殊处理

```python
# 与 TaskManager 不同，TMS 使用带 map_id 参数的版本
# 返回 (updated, newly_preprocessed)
# updated: 地图是否变化
# newly_preprocessed: 是否有新的预处理数据可用
```

如果 `newly_preprocessed=True`：
```
"TMS---同步地图预处理状态更新---{map_id}-{hash}--->FULLY READY"
```

## 7. 常见异常及排查

### 7.1 路径规划一直不触发

```
症状: 机器人有任务但一直没路径

排查步骤:
  1. 检查 TMSServiceActor 是否在线
     → 找 "TMS-SERVICES-ALIVE------" 心跳日志
  2. 检查子 Actor 是否创建
     → 找 "初始化TMS-{map_id} actor"
  3. 检查 DynamicMap 是否可达
     → 找 "动态地图服务---尝试获取新Actor----" (MapBasedServiceActor 基类)
     → 找 "动态地图Actor获取失败--{error}" (基类)
  4. 检查机器人数据是否正常
     → TMS 的子 Actor 调用 get_robots_data() 获取
     → 查 DynamicMap 日志中 "robots data updating:True" 是否频繁
       (True 表示正在更新中，TMS 会拿不到数据)
  5. 检查 paused 状态
     → TMSConfig.paused 是否为 True
```

### 7.2 地图未同步

```
症状: TMS 拿不到地图

日志:
  "服务：TMS---地图尚未获取到--"                           ← 基类 update_path_planning_map
  "TMS---同步地图预处理状态更新---{map_id}--->FULLY READY" ← 预处理数据可用
  "服务：TMS---地图已同步---{map_id}"                       ← 地图同步成功

排查:
  1. DynamicMap 是否已加载地图 (/_map_hash 非 None)
  2. 地图是否已完成预处理 (fully_ready)
  3. 子 Actor 的 map_id 是否正确
```

### 7.3 DynamicMap Actor 获取失败

```python
# 基类 MapBasedServiceActor.update_dynamic_map_actor()
# 每轮都会尝试获取

日志:
  "动态地图服务---尝试获取新Actor----"
  "动态地图Actor获取失败--{error}"
  "动态地图----获取成功"

原因:
  - DynamicMap Actor 尚未启动
  - DynamicMap Actor 崩溃重启
  - Ray 集群网络问题

排查:
  1. 检查 Ray Dashboard: 确认 DYNAMIC-MAP-SERVICES-ACTOR 是否存在
  2. 检查 DynamicMap 服务是否因异常退出
```

### 7.4 子 Actor 异常

```
症状: 某个地图单元的路径规划停止了

排查:
  1. 检查子 Actor 是否存活
     → ray list actors | grep "TMS-SERVICES-ACTOR-{map_id}"
  2. 检查子 Actor 日志是否有异常
  3. 尝试 reset → 会销毁并重建所有子 Actor
```

### 7.5 调用 DynamicMap 返回 None

```
TMS 子 Actor 调用: ray.get(dynamic_map.get_robots_data.remote(map_id))

如果 robots_data 为 None:
  → TMS 跳过本轮规划，等下次触发

原因: DynamicMap 的 robots_data_updating 为 True
  (正在 _refreshing_robots_data 或 _canceling_paths 或 _synchronizing_tasks)
```

## 8. TMS 配置参数速查

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `runner_interval` | 0.5 | 子 Actor 循环间隔 (秒) |
| `heartbeat_interval` | 5 | 心跳间隔 (秒) |
| `plan_path_extra_timer` | -1 | 额外触发定时器 (-1 永不触发) |
| `cal_line_registration` | False | 是否计算线路占用 |
| `default_planner` | "MAPF" | 默认规划器 |
| `base_solver` | "GP" | 基础求解器 |
| `use_reasoner` | True | 是否使用推理器 |
| `greedy_cp` | True | 是否贪心控制点 |
| `resort` | False | 是否重排序 |
| `paused` | True | 是否暂停 (⚠️ 默认暂停!) |
| `consider_load` | False | 是否考虑载具 |

**注意**：`paused` 默认为 `True`，这意味着 TMS 默认不会执行路径规划！必须在配置中显式设置 `paused = False` 或通过 API 修改。
