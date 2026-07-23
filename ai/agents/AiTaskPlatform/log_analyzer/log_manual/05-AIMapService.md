# 05 - AIMapService（AI 地图服务）

## 1. 职责

提供 AI 辅助功能，包括：

- AI 自动建图（标注 → 路网）
- 障碍物识别（SLAM 图片 → 障碍区域）
- 障碍物-路网碰撞检测
- 地图连通性校验
- AI 工具集（点合并、曲线生成、库位连接、准备点）
- 库位重发点 + 电梯等待点计算
- 叉车进库车头方向校验
- 动态卡车地图（异步回调模式）
- 动态集装箱地图（已下线）

## 2. 关键代码位置

| 文件 | 内容 |
|------|------|
| `uspa_services/services/ai_map_service/ai_map_service_actor.py` | 主 Actor (1081 行) |
| `uspa_services/services/ai_map_service/progress_collector.py` | 进度收集器 (271 行) |
| `uspa_services/services/service_actor.py` | ServiceActor 基类 |

## 3. 涉及的 Actor

| Actor | max_concurrency | namespace | 定位 |
|-------|-----------------|-----------|------|
| AIMapServiceActor | 2 | USP-ALGORITHM-SERVICES | AI 服务主入口 |
| AIMapProgressCollector | 10 | USP-ALGORITHM-SERVICES | 多任务进度收集 (独立) |

## 4. 接口清单

| 接口 | 模式 | 返回方式 |
|------|------|----------|
| `/AIMakeMap` | 异步 (线程) | 立即返回 taskId → 轮询 `/getAIMapProgress` → `/getAIMapResult` |
| `/AIObstacleRecognize` | 同步 | 直接返回结果 |
| `/AICollisionDetect` | 同步 | 直接返回结果 |
| `/mapConnectivity` | 同步 | 直接返回结果 |
| `/AITools` | 同步 | 直接返回结果 |
| `/AITools/storageAndElevatorPoint` | 同步 | 直接返回结果 |
| `/AITools/storageOrientationCheck` | 同步 | 直接返回结果 |
| `/AIDynamicMapTruck` | 异步 (回调) | 立即返回受理 → 完成后 POST 到 notificationUrl |
| `/AIDynamicMapContainer` | 同步 | 返回不支持 (已下线) |

## 5. 业务日志全链路

### 5.1 AI 建图 (`/AIMakeMap`)

```
[接口入口]
"请求传入 /AIMakeMap "                                  ← handle_request() 中
"收到 AI_map 请求 /AIMakeMap"                            ← process_request() 中

[异步任务提交]
"AI地图生成任务已提交，task_id: {task_id}"               ← 任务已创建，返回 taskId 给调用方

[异步执行 - 后台线程]
"AI地图生成任务开始"                                     ← ProgressCollector: state="PROCESSING"
... 算法层执行 (planner_service.run_make_map)
"AI_MAP_SUCCESS"                                         ← ProgressCollector: state="FINISHED"

[失败]
"AI地图生成失败: {error_msg}"                            ← ProgressCollector: state="ERROR"
"AI地图生成任务失败，task_id: {task_id}, error: {error}"
```

### 5.2 查询 AI 任务结果 (`/getAIMapResult`)

```
[正常]
"任务结果已返回并清理，task_id: {task_id}"               ← 成功查询，清理内存

[异常]
"错误任务已清理，task_id: {task_id}"                     ← 任务以 ERROR 状态结束

[错误码]
AI_MISSING_TASK_ID    ← 缺少 task_id (400)
AI_TASK_NOT_FOUND     ← 任务不存在 (404)
```

### 5.3 查询任务进度 (`/getAIMapProgress`)

```
[直接访问 ProgressCollector Actor]
不走 AIMapServiceActor 的并发限制 (max_concurrency=2)
独立 Actor max_concurrency=10

返回结构:
{
  task_id, state (PROCESSING/FINISHED/ERROR/NOT_FOUND),
  percentage, message, task_type,
  modules: { module_name: { progress: 0.0-1.0 } }
}
```

### 5.4 障碍物识别 (`/AIObstacleRecognize`)

```
[入口]
"请求传入 /AIObstacleRecognize "
"AI 障碍物识别调用"                                      ← ai_collision_detect() 中也有类似日志

[任务开始]
"障碍物识别任务已提交，task_id: {task_id}"               ← 同步执行 (非异步)
"障碍物识别任务开始"                                     ← ProgressCollector

[完成]
"障碍物识别完成"                                         ← ProgressCollector: FINISHED

[失败]
"障碍物识别失败: {error_msg}"                            ← ProgressCollector: ERROR
AI_MISSING_MAP_CONTENT    ← 缺少 map.content (400)
AI_MISSING_SLAM_IMAGE     ← 缺少 mapSlam (400)
```

### 5.5 碰撞检测 (`/AICollisionDetect`)

```
[日志]
"AI 障碍物-路网碰撞检测调用"
"碰撞检测任务开始"                                       ← ProgressCollector
"碰撞检测完成"                                            ← ProgressCollector: FINISHED
"碰撞检测失败: {error_msg}"                              ← ProgressCollector: ERROR

[错误码]
AI_MISSING_MAP_CONTENT    ← 缺少 map.content (400)
AI_MISSING_SLAM_IMAGE     ← 缺少 mapSlam (400)
```

### 5.6 地图连通性校验 (`/mapConnectivity`)

```
[正常]
"校验完成"

[异常]
"地图连通性校验失败: {error_msg}"
AI_MISSING_MAPS_LIST      ← maps 为空或不是 list (400)
```

### 5.7 AI 工具 (`/AITools`)

```
[入口]
"AI工具服务调用，type: {tool_type}"                       ←

[子工具日志]
type="pointMerge":
  "AI工具服务【合并近距离点】调用成功"

type="preparationPoint":
  "AI工具服务【路口准备点生成】调用开始"                   ← 注意：这里 type 值叫 preparationPoint
  "AI工具服务【路口准备点生成】调用成功，stats: {stats}"

type="curveGenerate":
  "AI工具服务【生成交点曲线】调用成功，stats: {stats}"

type="storageConnect":
  "AI工具服务【生成库位连接线】调用成功，stats: {stats}"

[异常]
AI_MISSING_TOOL_TYPE      ← 缺少 type 字段 (400)
AI_UNSUPPORTED_TOOL_TYPE  ← 不支持的工具类型 (400)
```

### 5.8 库位重发点+电梯等待点 (`/AITools/storageAndElevatorPoint`)

```
"收到库位重发点与电梯等待点请求"
"库位重发点与电梯等待点请求成功，out: {out}"
```

### 5.9 库位朝向校验 (`/AITools/storageOrientationCheck`)

```
"叉车进库车头方向（库位朝向）校验完成，result: {result}"
```

### 5.10 动态卡车地图 (`/AIDynamicMapTruck`) — 异步回调模式

```
[受理]
"动态卡车地图任务已受理，后台生成完成后将回调: {url}"

[后台线程执行]
"动态卡车回调已发送: url={url} status={status}"
"动态卡车回调返回非成功状态: resp.status_code 为 {code} {url}"
"动态卡车回调请求失败: url={url} err={error}"

[失败回调]
"动态卡车地图生成失败: {error_msg}"
(同时 POST 失败通知到 notificationUrl)
```

### 5.11 异常处理

```
[通用异常日志格式]
"[{error_code}] {msg}\n{traceback}"

[RecordedException 转换]
所有异常在 handle_exception() 中统一格式化:
  code → response.code
  msg  → response.msg
  data.exception_record → 完整异常元数据

[非 RecordedException]
"未知异常: {str(e)}"                                     ← _convert_algorithm_exception 中
```

### 5.12 配置更新

```
(无独立 refreshSettings 日志，继承自 ServiceActor)
```

## 6. ProgressCollector 独立并发设计

`/getAIMapProgress` 不经过 `AIMapServiceActor`，而是直接调用独立的 `AIMapProgressCollector`：

```python
# start_services.py 中 /getAIMapProgress 的实现
progress_collector = get_progress_collector_actor()
status = ray.get(progress_collector.get_status.remote(task_id))
```

原因：`AIMapServiceActor.max_concurrency=2`，如果两个耗时 AI 任务占满并发，进度查询会被阻塞。进度收集器有独立 `max_concurrency=10`，不受影响。

## 7. 常见异常及排查

### 7.1 AI 建图任务返回但无结果

```
症状: /getAIMapResult 返回任务不存在或一直 PROCESSING

排查步骤:
  1. 查 /getAIMapProgress → 看 state:
     - PROCESSING: 还在执行，查 AIMapService 日志
     - ERROR: 执行失败，看 error message
     - NOT_FOUND: 任务已被清理 (1小时后自动清理) 或 taskId 错误
     - FINISHED 但 /getAIMapResult 查不到: 说明已被查询过并清理了
     
  2. 如果 state 一直是 PROCESSING:
     → 检查 AIMapServiceActor 是否活着
     → 检查后台线程是否异常 (daemon=True 的线程可能静默失败)
     → 检查算法层是否有 problem
     
  3. 如果直接抛异常:
     → 检查 task_id 是否正确 (uuid4().hex = 32 字符 hex)
```

### 7.2 障碍物识别失败

```
症状: AI_MISSING_MAP_CONTENT 或 AI_MISSING_SLAM_IMAGE

排查:
  1. 确认请求中 map 字段包含 content
  2. 确认请求中 mapSlam 字段存在 (base64 图片或文件路径)
  3. 确认 mapSlamYaml 的内容正确 (如果提供)
```

### 7.3 动态卡车地图回调未收到

```
症状: /AIDynamicMapTruck 返回 accepted 但通知迟迟不来

排查步骤:
  1. 查日志: "动态卡车地图任务已受理，后台生成完成后将回调: {url}"
  2. 确认 notificationUrl 可访问且非空
  3. 查日志: 是否有 "动态卡车回调已发送" 
     → 如果没有 → 后台线程还在计算或已异常
     → 如果有 → check notificationUrl 端是否收到
  4. 查日志: "动态卡车回调返回非成功状态" → 回调端返回非 200
  5. 查日志: "动态卡车回调请求失败" → 网络错误
  6. 检查 mode 参数是否在 LOAD/UNLOAD
  7. 检查 scan 数组是否非空、每项 scanPoints 至少 2 个
```

### 7.4 AIMapServiceActor 并发阻塞

```
症状: AI 任务执行中但进度接口无响应

原因: max_concurrency=2，两个慢任务占满

排查:
  1. /getAIMapProgress 走独立 Actor，应该不受影响
  2. 如果其他 AI 接口也超时 → 确认是否 Actor 确实卡住
  3. 检查是否有 AI 任务一直在 executed (线程未结束)
```

### 7.5 异常记录不完整

```
症状: 错误消息是不完整的 "未知异常"

原因: 算法层抛出的异常不是 RecordedException → 被 _convert_algorithm_exception 包装

排查:
  1. 查日志中 "[AI_UNKNOWN_ERROR]" → 说明原始异常没有被正确分类
  2. 看 exception_record 中的 exception_type 确定原始异常类型
  3. 检查算法层是否需要升级为返回 RecordedException
```

### 7.6 工具服务调用失败

```
症状: AI_UNSUPPORTED_TOOL_TYPE

支持的类型:
  - pointMerge        → 合并近距离点
  - preparationPoint  → 路口准备点生成 (⚠️ 注意拼写)
  - curveGenerate     → 生成交点曲线
  - storageConnect    → 生成库位连接线

排查: 确认请求 type 字段是否在上述列表中
```

## 8. 响应格式

### 正常响应

```json
{
  "code": "200",
  "data": { ... },
  "msg": "成功信息"
}
```

### 异常响应

```json
{
  "code": "400" | "404" | "500",
  "data": {
    "exception_record": {
      "errorCode": "AI_MISSING_MAP_CONTENT",
      "description": "错误描述",
      "level": "ERROR",
      "parameters": { "task_id": "..." },
      "suggestion": null,
      "details": null
    }
  },
  "msg": "错误描述",
  "error": {
    "key": "AI_MISSING_MAP_CONTENT",
    "params": { "task_id": "..." }
  }
}
```
