# 02 - TaskManagerService（任务管理服务）

## 1. 职责

TaskManagerService 是系统的**决策中心**，负责决定"哪个机器人做什么任务"：

- 业务任务分配（贪心匹配）
- 充电任务分配（闲时 + 紧急）
- 休息任务分配（闲时）
- 预分配休息任务（业务任务做完后）
- 跨楼层资源平衡（拍卖算法）

## 2. 关键代码位置

| 文件 | 内容 |
|------|------|
| `uspa_services/services/task_manager_service/task_manager_service_actor.py` | 主 Actor (1762 行) |
| `uspa_services/services/service_actor.py` | MapBasedServiceActor 基类 |

## 3. 参数配置

| Actor 参数 | 值 |
|------------|-----|
| `max_concurrency` | 4 |
| `service_name` | `"TASK-MANAGER"` |
| `runner_interval` | 0.5s |
| `heartbeat_interval` | 5s |

## 4. 关键电量阈值逻辑

```
emergency_charging_battery (20%)  ← 紧急充电，可抢占他人充电桩
        │
working_charging_battery (25%)    ← 低于此值不能接业务任务
        │
charging_workable_battery (35%)   ← 充电中电量达到可打断
        │
idle_charging_battery (70%)       ← 闲时充电阈值
        │
100%                              ← 满电

定时充电时段:
  fixed_time_charging_battery (80%)        ← 定时充电时段的最低工作电量
  fixed_time_charging_workable_battery (90%) ← 定时充电时段的打断阈值
```

## 5. 内部触发标志

| 标志 | 含义 | 何时设为 True |
|------|------|---------------|
| `_schedule_triggered` | 需要分配业务任务 | refresh_scheduling_tasks() / update_robots() 发现新空闲车+待分配任务 |
| `_charge_triggered` | 需要检查充电 | update_robots() 每次 |
| `_rest_triggered` | 需要检查休息 | update_robots() 有可用车时 |

每次 run() 周期会**消耗**这些标志（设为 False），防止重复触发。

## 6. 业务日志全链路

### 6.1 run() 主循环

```
"TASK-MANAGER-SERVICE-ALIVE------"                       ← 心跳 (每 5s)
```

```
[每轮循环]
"任务池信息更新-------{tasks_dict}"                      ← refresh_scheduling_tasks 调用
"当前任务：{unassigned_tasks}--------"                    ← 当前待分配任务列表

"各地图单元业务任务: {map_id: [task_ids]}"
"当前所有车辆信息\n{robot_info_list}"                     ← 所有机器人状态详情
"无可用车辆"                                             ← 过滤后无可分配的车

"开始任务分配"                                           ← schedule_business_tasks()
"车辆当前路径的下一个节点: {nodes}"                       ← 用于距离计算
"各地图单元候选车辆: {map_id: [robot_ids]}"
"任务分配结果: {[(robot_id, task_id)]}"

"匹配完成--当前匹配结果：{formatted}"                     ← send_assignments() 中

"充电逻辑判断 auto_charge: {bool} charge_triggered: {bool}"
"休息逻辑判断 auto_rest: {bool} rest_triggered: {bool}"
```

### 6.2 任务校验 (`/refreshSchedulingTasks`)

```
[正常]
"任务池信息更新-------{tasks_dict}"

[异常]
"地图尚未获取到"                                         ← path_planning_map 为空
"任务ID为空"                                             ← task 缺少 id
"任务节点为空 taskId:{id}"                                ← task 缺少 taskNodeList
"子任务ID为空 taskId:{id}"                                ← taskNode 缺少 id
"子任务目标点为空 taskId:{id} taskNodeId:{id}"            ← 缺少 pointId 和 nodeId
"子任务目标点不存在 taskId:{id} taskNodeId:{id}"          ← nodeId/pointId 不在地图中
"子任务目标点和目标节点不匹配 taskId:{id} taskNodeId:{id}" ← node 和 point 不一致
"子任务目标点和目标地图不匹配 taskId:{id} taskNodeId:{id}" ← 声称的 mapId 和实际不一致
```

### 6.3 业务任务分配

```
[候选车辆过滤]
"车辆: {id} 正在等待任务同步"                             ← task_sync_state 为 True
"车辆: {id} 状态异常，不可分配业务任务"                    ← can_assign_system_task/business_task=False

[分配执行]
"各地图单元业务任务: {map_id: [task_ids]}"
"各地图单元候选车辆: {map_id: [robot_ids]}"
"无可用车辆"                                              ← 候选列表为空
"地图单元 {map_id} 无可用车辆"                            ← 该地图无候选
"地图单元 {map_id} 分配业务任务异常: {e}"                 ← 分配过程异常

[发送结果]
"等待车辆任务同步: {id} 不发送任务分配结果"               ← sync state 保护
"发送任务分配结果"
"任务分配后端返回结果: {response}"
"任务分配结果发送失败"                                    ← 后端返回 data 为空
"任务分配成功: {[(robot_id, task_id)]}"
```

### 6.4 充电任务分配

```
"各地图单元待充电车辆: {map_id: [(robot_id, battery)]}"
"No Robot need to charge"                                ← 所有车电量都够
"各地图单元充电任务候选车辆: {map_id: [robot_ids]}"
"各地图单元候选充电桩: {map_id: [station_ids]}"
"充电任务分配结果: {[(robot_id, point_id)]}"

"充电桩实时状态: {status}"                               ← get_resource_status("/getChargingStations")
"充电桩 {id} 不可用"                                      ← 充电桩 enable=False
"充电桩 {id} 不存在"                                      ← 不在 functional_points 中
"充电桩 {id} 状态异常，当前占用的车辆 {id} 需要充电"       ← 占用者也需要充电

[新生成充电任务]
"新生成充电任务----{charge_tasks}"
"发送任务: {tasks}"
"发送任务后端返回结果: {response}"
"创建任务失败----"                                        ← 后端 data 为空
"创建任务失败-----未能创建全部必要任务：{n}/{n}"            ← 后端不够
```

### 6.5 紧急充电

```
[紧急充电 - 抢占充电桩]
"紧急充电Robot: {id} 当前分配充电桩: {station_id} 已被 {other_id} 占用，尝试停止充电"
"尝试停止充电后端返回结果: {response}"
"尝试停止充电失败"
"紧急充电任务分配成功"
"紧急充电任务分配失败"
```

### 6.6 休息任务分配

```
"各地图单元休息任务候选车辆: {map_id: [robot_ids]}"
"各地图单元候选休息点: {map_id: [rest_point_ids]}"
"No Robot need to rest"                                  ← 所有车都不需要休息
"休息点实时状态: {status}"                                ← get_resource_status("/getRestPoints")
"休息点 {id} 不可用"
"休息点 {id} 不存在"
"Robot:{id} 已占用休息点 {point_id}"                     ← 已在休息点

"新生成休息任务----{rest_tasks}"
"休息任务分配结果: {[(robot_id, point_id)]}"
```

### 6.7 预分配休息任务

```
"开始预分配休息任务: {[(robot_id, task)]}"
"各地图单元预分配休息任务: {map_id: [(robot_id, task_id)]}"
"各地图单元预分配可用休息点: {map_id: [point_ids]}"
"预分配休息任务: {[(robot_id, point_id)]}"

"新增待分配休息任务: robotId:{id} taskId:{id}"
"当前待分配休息任务: {[(robot_id, task_id)]}"
"Robot: {id} 当前任务开始执行，预分配休息任务"
"Robot: {id} 不存在，删除待分配休息任务"
```

### 6.8 跨楼层资源平衡

```
"Cross Map Candidate Robots: {[robot_ids]}"
"合法的转移：{elevators}--{traversal}"                    ← 哪些场景可跨楼层

"拍卖计算 Robot:{id} transition:{from}->{to} cost:{c} profit:{p}"

"{task}---虚拟匹配"                                       ← 虚拟占位某任务
"跨楼层任务发送--{cross_map_tasks}"
"发送跨楼层任务: {formatted}"
"发送跨楼层任务返回结果: {response}"
"跨楼层任务发送失败: {e}"

"Robot Transitions Expired: {robot_ids}"                 ← 过期清理
```

### 6.9 机器人数据同步

```
"TASK-MANAGER 更新Robots"
"robots data updating"                                   ← get_robots_data_snapshot() 返回 None
"Robot:{id} 已下线"
```

### 6.10 配置更新

```
"服务: TASK-MANAGER---收到动态修改配置参数请求: {data}"
```

## 7. 常见异常及排查

### 7.1 任务不分配

```
症状: 有任务池，有可用车，但 allocation 不输出

排查步骤:
  1. 查日志: "当前所有车辆信息" → 逐个检查每个 robot 的状态
     - can_assign_system_task 是否为 True
     - can_assign_business_task 是否为 True
     - battery_percentage 是否 >= working_charging_battery (25%)
     - working_state 是什么 (IDLE / CHARGING / 异常)
     - cur_task 是否为 None (有任务的车不会分配新任务)

  2. 查日志: "各地图单元业务任务" → 确认任务在哪个 map_id
     和 "各地图单元候选车辆" 的 map_id 是否匹配

  3. 检查 task_sync_state 是否有卡住的 robot
     → 查找 "等待车辆任务同步" 是否反复出现同一 robot

  4. 检查 refresh_scheduling_tasks 是否被调用
     → 查找 "任务池信息更新" 日志
```

### 7.2 充电任务一直不触发

```
排查步骤:
  1. 查配置: auto_charge 是否为 True
  2. 查日志: "充电逻辑判断" → 看 charge_triggered 是否为 True
  3. 查后台: GET /getChargingStations → 充电桩 enable 状态
  4. 查日志: "充电桩实时状态" → 是否有可用充电桩
  5. 查机器人: battery_percentage 是否低于阈值
     - 紧急: < emergency_charging_battery (20%)
     - 工作: < working_charging_battery (25%)
     - 闲时: < idle_charging_battery (70%) + 空闲 > idle_charging_time (300s)
```

### 7.3 跨楼层不触发

```
排查步骤:
  1. 查配置: enable_auto_cross_map 是否为 True
  2. 查日志: "合法的转移" → 是否有合法的楼层通道 (device_stations)
  3. 查日志: "Cross Map Candidate Robots" → 候选车为空?
     - 可能有车被 filtered out: "Robot:{id} 当前有正在生效的跨地图任务"
  4. 查日志: "拍卖计算" → cost vs profit → profit > cost 才触发
  5. 查参数: cross_map_interval (15s) 内不会再次转移同一车
```

### 7.4 紧急充电抢占失败

```
症状: "紧急充电任务分配失败"

排查步骤:
  1. 查日志: "紧急充电Robot: {id} 当前分配充电桩: {id} 已被 {id} 占用"
  2. 查日志: "尝试停止充电后端返回结果" → 后端是否接受 STOP_CHARGING
  3. 被抢占的车是否在充电中 (working_state == CHARGING)
  4. 检查 robot_command_backend_url 是否正确
```

### 7.5 预分配休息任务失败

```
症状: "Robot: {id} 不存在，删除待分配休息任务"

原因: 业务任务分配后机器人下线了

排查:
  1. 检查机器人是否在业务任务和休息任务之间下线
  2. 检查 _pending_pre_schedule_rest_task 是否有僵尸条目
```

## 8. 发送任务到后端的两条路径

```
普通任务 (同楼层/跨楼层):
  send_task_request(route, tasks, timeout=10)
    → POST {send_task_backend_url}/createTasks
    或 POST {send_task_backend_url}/allocateRobots

跨楼层任务:
  send_cross_map_task_request('/move-task', tasks, timeout=10)
    → POST {task_flow_url}/move-task
    然后 time.sleep(5) 等待后端处理
    然后 send_task_request('/createTasks', ...)
```

## 9. 任务同步保护机制

`task_sync_state` 是一个字典，标记哪些机器人正在等待后端确认：

```python
# 发送任务前:
self.task_sync_state[robot.id] = True

# 收到后端响应后 (finally 块):
del self.task_sync_state[robot.id]
```

被标记的机器人会被跳过：
- 业务任务分配 → "等待车辆任务同步: {id} 不发送任务分配结果"
- 充电/休息分配 → "等待车辆任务同步: {id} 不发送任务"

如果后端一直没有响应（或异常），`finally` 块仍会清除状态。
