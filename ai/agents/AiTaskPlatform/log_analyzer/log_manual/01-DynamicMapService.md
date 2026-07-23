# 01 - DynamicMapService（动态地图服务）

## 1. 职责

DynamicMapService 是整个系统的**数据中心**，维护所有运行时状态的唯一真实来源：

- 地图数据 (PathPlanningMap)
- 机器人实时状态 (Robot/RobotData)
- 机器人路径 (Path)
- 锁区 (TrafficLock) — 物理锁区、预约锁区、固定锁区
- 功能点 (FunctionalPoints) — 充电桩、休息点、库位、设备点
- 线路占用 (line_registration)
- 依赖网络 (dependency_network)

## 2. 关键代码位置

| 文件 | 内容 |
|------|------|
| `uspa_services/services/dynamic_map_service/dynamic_map_service_actor.py` | 主 Actor (2391 行) |
| `uspa_services/services/dynamic_map_service/robot_events_handler.py` | 机器人事件处理 |
| `uspa_services/services/dynamic_map_service/exceptions.py` | 异常定义 |
| `uspa_services/services/service_actor.py` | 基类 ServiceActor |

## 3. 参数配置

| Actor 参数 | 值 |
|------------|-----|
| `max_concurrency` | 4 |
| `service_name` | `"DYNAMIC_MAP"` |
| `runner_interval` | 0.2s (run 循环周期) |

## 4. 内部状态变量速查

| 变量 | 类型 | 含义 | 正常范围 |
|------|------|------|----------|
| `_path_planning_map` | Dict[str, PathPlanningMap] | 路径规划地图 | 非 None 表示地图已加载 |
| `_robots` | Dict[str, Robot] | 在线机器人 | 空 → 无机器人上线 |
| `_functional_points` | FunctionalPoints | 功能点 | None → 未初始化 |
| `_traffic_shape_manager` | TrafficShapeManager | 障碍物管理 | None → 未初始化 |
| `_map_hash` | Dict[str, str] | 各 map 的 hash | None → 地图未加载 |
| `_fixed_traffic_locks` | Dict | 固定锁区 | 空 → 无手动锁区 |
| `_line_registration` | Dict | 线路占用 | 空 → 无路径 |
| `_dependency_network` | Dict | 路径依赖网络 | 空 → 无路径 |
| `_updating_paths` | bool | 正在更新路径中 | False 正常 |
| `_refreshing_robots_data` | bool | 正在更新机器人数据 | False 正常 |
| `_synchronizing_tasks` | bool | 正在同步任务 | False 正常 |
| `_canceling_paths_event` | List | 正在取消路径 | [] 正常 |
| `_elevators_state` | Dict | 电梯占用缓存 | 空 → 无电梯 |
| `_last_refresh_robot_states_timestamp` | float | 上次机器人更新时间戳 | 持续增长 |

## 5. 业务日志全链路

### 5.1 地图加载 (`/updateMaps`)

```
[正常流程]
"更新地图请求---{maps_payload}"                          ← update_maps() 入口
"地图：{map_id}/{hash}---加载分级预处理数据"               ← 优先级1: load_level
"地图：{map_id}/{hash}---完全预处理完毕---"               ← 优先级2: fully preprocessed
"地图：{map_id}/{hash}---微预处理数据----加载微预处理地图"  ← 优先级3: minimum
"地图：{map_id}/{hash}---无预处理数据----加载原始地图"      ← 优先级4: none (⚠️ 性能差)
"地图加载成功----Map:id:{keys}"                          ← 全部加载完成
"单地图增量刷新请求---{maps_payload}"                     ← 增量刷新模式

[异常]
"地图文件不存在----Map id:{id}---Hash:{hash}"             ← 4级目录都找不到文件 → 抛出 MapNotInitialized
"单地图增量刷新----maps 为空，忽略"                       ← maps 为空，跳过

[代码位置] _load_path_planning_map_from_disk():303-364
```

### 5.2 机器人状态刷新 (`/refreshRobotStates`) — 核心接口

这是调用最频繁的接口（~200ms/次），每次刷新所有机器人状态。

```
[入口]
"同步状态：{canceling_paths}---{refreshing_robots_data}---{synchronizing_tasks}"
"收到机器人数据"

[每条机器人更新]
"更新机器人数据---{robot_dict}"                           ← 开始处理一个机器人
"机器人不在当前所有地图层上---{id}---MapId:{map_id}"      ⚠️ 机器人地图层不存在，跳过
"机器人类型不存在---{id}---Type:{type}"                    ⚠️ 机器人类型不在 robotTypes 白名单
"机器人: {id}---需要更新障碍"                             ← 车无法规划路径 → 变障碍
"Robot-Obstacle:{obstacle}"                              ← 当前活跃的机器人障碍

[过期请求保护]
"收到过期的更新机器人数据请求，跳过本次更新"               ← timestamp < 上次更新时间 (409)

[路径相关]
"车辆:{id}-等待时间超限:{wait_time}--检查预测与执行的一致性"       ← 车等待太久
"执行与预测一致性超过update阈值:{diff}---清除该车未释放路径"      ← 超 wait_time_update_gap (默认 30s)
"执行与预测一致性超过replan阈值:{diff}---发起重规划请求"          ← 超 auto_replan_gap (默认 300s)

[碰撞检测]
"检测到车辆碰撞-----Robot:{id}&Robot:{id}"               ⚠️ 两车距离 < 0.3m

[下线]
"Robot:{id}---已下线"                                    ← 不在当前在线列表中

[返回数据]
"Robot更新耗时:{time}---"                                ← 本次刷新总耗时 (time_it 装饰器)
"robot data updating:{bool}--"                            ← 机器人数据更新状态
"Data to backend:{num}"                                  ← 下发的数据量

[代码位置] refresh_robot_states():443-676
```

#### 5.2.1 机器人状态更新触发的事件链

```python
# 第1步: 更新机器人数据
robot.update_robot_data(robot_dict, logger, traffic_shape_manager, ...)
# 返回值 canceled=True → 路径被取消 → cancel_robot_path()

# 第2步: 检查障碍物更新
if robot_need_update_obstacle(new_added, robot):
    # 条件: 新上线 / can_plan_path 变化 / 位置变化 > 0.02 / 角度变化 > 0.05
    traffic_shape_manager.update_robot_obstacle(...)

# 第3步: 正常车 × 电梯占用车
# can_plan_path=False + 正占用电梯 → is_normal=True (不生成挡路障碍)
```

#### 5.2.2 电梯诊断日志

```
"[ELEV-DIAG] 电梯占用刷新 {状态}"                         ← 电梯状态变化 (run循环)
"[ELEV-DIAG] 电梯占用变化→强制释放校验"                   ← 占用名单变化触发
"[ELEV-DIAG] 转障碍判定 robot={id} can_plan_path={}..."   ← 障碍判定时含电梯信息
"[ELEV-DIAG] 释放-电梯设备障碍 robot={id} 电梯={} 占用={}" ← 电梯互斥冲突但不截断
"[ELEV-DIAG] 进梯边等待占用,保留路径不截断 robot={id}"    ← 等待电梯占用
"[ELEV-DIAG] 电梯格被他车实占,维持截断避免双锚点"          ← 被其他车占了才截断
"[ELEV-DIAG] 占用者进梯豁免依赖 robot={id}"               ← 占用者进自己电梯豁免
"[ELEV-DIAG] 非进梯边扫掠电梯格,放行 robot={id}"          ← 旋转/借道边扫到电梯格放行
```

### 5.3 功能点更新 (`/updateFunctionPoints`)

```
[正常]
"收到更新功能点数据请求"
"更新功能点数据----function_points_dicts: {...}"
"计算库位白名单----map_id: {map_id}"

[异常]
"更新功能点---地图ID不存在---{map_id}/{keys}"              ⚠️ map_id 不在地图列表中
"地图为空----无法加载功能点数据"                           ← _path_planning_map 为 None
```

### 5.4 路径同步 (`update_paths` — 由 TMS 调用)

```
[入口]
"接收到TMS路径----mapId:{map_id}"

[校验失败]
"当前删除路径处理中----本次更新拒绝--"                     ← _canceling_paths_event 非空
"取消同步校验失败，本次更新拒绝---{ts}>={ts}"              ← synchronize_t > start_cal_t

[路径过期检查]
"Robot:{id}--路径已完成：{path_id}---该次更新过时---"      ← 路径已完成
"Robot:{id}--该路径已被取消：{path_id}---该次更新过时---"   ← 路径被取消
"Robot:{id}--路径Index超过已释放的范围：...---该次更新过时---" ← 释放进度已超
"Robot:{id}--路径Index超过路径长度：...---该次更新过时---"    ← 索引越界
"Robot:{id}---避让路径已经实际创建---不允许追加"            ← task_node_id 冲突

[正常]
"路径校验通过---可更新"                                   ← 全部校验通过
"Task Node Id:{id}--updateId:{id}---updated_path:..."      ← 路径已更新
"Task Node Id:{id}--updateId:{id}---added_path:..."        ← 新路径已添加

[代码位置] update_paths():1314-1457
```

### 5.5 节点释放 (`try_release_node` — 由 run() 和 update_paths() 触发)

```
[入口]
"进入路径节点释放校验流程-----"
"更新依赖关系----"
"依赖更新成功-----"

[释放逻辑]
"Robot:{id}---待释放路径的pathId:{a}与当前pathId不一致{b}"  ← path_id 不匹配，跳过
"Robot:{id}---锁区创建成功----Traffic Lock Id:{id}---Index:{i}" ← 节点成功释放
"锁区冲突：----{resource_id}--->{traffic_lock_id}"          ← 几何冲突 → cut_robot_path_nodes
"冲突触发-路径更新---{robot_id}--"                          ← exit_to_debug 时主动 exit

[出口]
"完成路径节点释放校验流程-----释放节点数：{num}"            ← 本次释放的节点总数
```

### 5.6 路径下发 (`extract_and_send_paths` — 由 run() 触发)

```
"发送路径---"
"Task Node Id:{id}--updateId:{id}---updated_path:..."
"该车异常状态----{robot_id}----该车路径取消"                ← robot.data.can_move=False

[后端响应]
"收到响应----{data}"
"存在路径未被接收"                                        ← 后端接收数 < 发送数

[同步不匹配]
"后端current Task一致性不满足-本次发送取消--{id}：{a}/{b}"  ← task_node_id 不匹配
"{id}:任务变更:{old}--->{new}---清除掉旧任务路径"            ← 任务已切换

[代码位置] extract_and_send_paths():1518-1543, send_paths():1546-1626
```

### 5.7 协同任务创建 (`create_and_assign_cooperative_tasks`)

```
"请求创建避让任务-----{[(robot_id, node_id), ...]}"
"避让任务全部创建成功----{n}/{n}---{data}"
"Robot---{id}--避让任务同步被打断---{task_node_id}--->打断任务：{new_id}"

[失败]
"创建任务失败----"                                        ← 后端返回 data 为空
"创建任务失败-----未能创建全部必要任务：{n}/{n}"            ← 后端返回不够
"等待后端同步失败----待同步RobotId:{ids}"                  ← 超时 15s
```

### 5.8 锁区管理 (`/createTrafficLock`, `/deleteTrafficLock`, `/updateTrafficLockStatus`)

```
"收到创建锁区请求: {request}"
"创建锁区成功: {traffic_lock.id}"
"创建锁区失败: {lock_dict} {e}"
"收到删除锁区请求: {request}"
"删除锁区成功: {traffic_lock.id}"
"收到更新锁区状态请求: {request}"
"更新锁区状态成功: {traffic_lock}"
```

### 5.9 配置更新 (`/refreshSettings`)

```
"服务: DYNAMIC_MAP---收到动态修改配置参数请求: {data}"
"设置动态修改：{key}：{old}---->{new}"                     ← update_setting() 中
```

### 5.10 重规划 (`replan_task_node`)

```
"收到重规划请求返回----{data}"                             ← 向 http://127.0.0.1:6030/api/robot/command
```

### 5.11 路径校验 (`check_robots_paths`)

```
"当前地图库位点{len}：{point_ids}"
"路径校验开始"
"robot：{id}---路径：{paths}"
"路径中经过的库位点：{points}"                            ← 出现异常时
"路径校验失败---{error}"                                   ← ⚠️ 路径不合法
"路径校验结束"                                            ← 全部通过
```

### 5.12 异常报告

```
"清除已解决的异常报告: {task_node_id} (地图: {map_id})"
"子任务节点异常报告\n..."                                  ← _task_node_exception_reports.__str__()
```

## 6. 常见异常及排查

### 6.1 MapNotInitialized

```
日志: "地图为空----无法加载机器人数据"
      "地图文件不存在----Map id:{id}---Hash:{hash}"

原因:
  - 地图数据从未加载过 (_path_planning_map 为 None)
  - 磁盘上没有预处理后的地图文件
  - 地图文件路径/目录配置错误

排查:
  1. 检查 map_dir 配置是否正确 (configs/config.ini [DYNAMIC_MAP] map_dir)
  2. 检查 map_dir 下是否有 preprocessed_maps/{map_id}/{hash}.json
  3. 检查 /preprocess 是否成功执行
  4. 查看 ProgressCollector 状态: "/updatePreprocessState"
```

### 6.2 过期请求 (409)

```
日志: "收到过期的更新机器人数据请求，跳过本次更新"
      "上次更新时间: {time}---当前请求时间: {time}"

原因: 后端发来的 timestamp 比上次处理的小 (消息乱序或时钟不同步)

排查:
  1. 检查后端与算法服务器的时间同步
  2. 确认 refreshRobotStates 的调用频率是否正常 (~200ms)
```

### 6.3 机器人类型/地图不匹配

```
日志: "机器人不在当前所有地图层上---{id}---MapId:{map_id}"
      "机器人类型不存在---{id}---Type:{type}"

原因:
  - 机器人的 mapId 不在当前加载的地图列表中
  - 机器人的 type 不在配置的 robotTypes 中

排查:
  1. 检查 /updateMaps 是否传入了该 mapId 的地图
  2. 检查 robotTypes 配置是否包含该类型
  3. 检查 robot 的位置数据中 mapId 是否正确
```

### 6.4 路径下发失败

```
日志: "后端current Task一致性不满足-本次发送取消--{id}：{path}/{cur}"
      "{id}:任务变更:{old}--->{new}---清除掉旧任务路径"
      "该车异常状态----{id}----该车路径取消"

原因:
  - 后端 task_node_id 与算法侧不一致 (状态不同步)
  - 机器人 can_move=False
  - 机器人任务已被切换

排查:
  1. 后端与算法的任务状态是否同步
  2. 机器人是否处于异常状态
  3. 是否有旧的路径未清理干净
```

### 6.5 电梯死锁

```
日志: "[ELEV-DIAG] 进梯边等待占用,保留路径不截断 robot={id}"
      但持续出现此日志无进展

原因: 多辆车在等同一个电梯，但占用状态一直不授予

排查:
  1. 检查电梯占用状态: 后端 /getElevators 返回的 occupyState/occupyRobot
  2. 检查是否有车在电梯点卡住 (cur_node 的 point 是电梯设备点)
  3. 检查 base 端的呼梯逻辑是否正常触发 (triggerPointCounts)
  4. 检查是否有车 can_move=False 且占着电梯不动
```

### 6.6 auto_replan 触发

```
日志: "执行与预测一致性超过replan阈值:{diff}---发起重规划请求"

原因: 车等待时间大大超过预期 (默认 300s)

排查:
  1. 检查是否有车被障碍挡住
  2. 检查锁区是否异常 (createTrafficLock 没有 delete)
  3. 检查电梯是否故障 (电梯占用状态异常)
  4. 检查路径规划的 wait_t (等待时间预测) 是否正确
```

## 7. run() 事件循环流程

```
run() 每 0.2s 循环:
  │
  ├── _refresh_elevators_state()     ← 独立于 paused，每 1s
  │
  ├── if paused: continue            ← paused=True 则跳过后续
  │
  ├── if _try_send_paths:
  │     ├── 查找避让路径 (task_node_id is None)
  │     ├── create_and_assign_cooperative_tasks()  ← 创建避让任务
  │     ├── check_robots_paths()      ← 路径校验 (如果开启)
  │     └── extract_and_send_paths()  ← 发送路径到后端
  │
  └── 下一轮循环
```
