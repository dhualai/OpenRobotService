# 历史召回（L3）双路并行升级设计

> 状态：已定稿（2026-08-07 与项目负责人确认）
> 目标：替代原先"现拉最近500条 + 现算embedding"的简陋方案，
>       升级为「A路相似工单聚人 + B路问题域聚人」双路并行，
>       用轻量方式达成图谱效果，不引入真图谱架构。

---

## 一、为什么升级

原 L3 的问题：
1. 现拉 tasks 表最近 500 条 closed → 若 500 条里没有匹配工程师的单则 L3 失效
2. 全按"文本相似 + 故障码"匹配 → 对**需求/研发类**（无故障码车型）工单几乎失效
3. 没有"工程师擅长什么问题域"的视角——只认"碰巧像"的单，不认"长期做这个域"的人

## 二、双路并行方案

```
当前工单
   ├── A路【按相似工单聚人】  ← Qdrant 语义检索相似工单 → 按 engineer_id 聚合
   │       回答："谁解决过跟这个几乎一样的单"
   ├── B路【按问题域聚人】    ← 工程师×模块×类型 经验画像
   │       回答："谁在这个问题域上解决得多、近"
   ▼
L3 融合: score_L3 = wA×A路 + wB×B路
   ▼
进入精排队（× history_match 权重 0.10 计入总分）
```

### A路（相似工单聚人）——向量库
- **数据源**：Qdrant 独立集合 `dispatch_history`（每条 = 一条 closed 工单）
- 每条 payload：
  ```
  engineer_id        # 解决人（核心）
  title / description
  modules            # 问题域标签（从 module_keywords 提取）
  task_type          # 故障/需求/支持...
  fault_code / robot_type   # 有则存，无则空
  closed_at          # 关闭时间（时间衰减用）
  ```
- **流程**：语义检索 top-k 相似工单 → 同故障码/车型 boost → 时间衰减 → 按 engineer_id 做 Top-K 峰值聚合
- **存量**：补索引脚本 `sync/history_indexer.py` 遍历 closed 工单一次性入库；后续随工单闭环持续回写

### B路（问题域聚人）——轻量经验画像
- **域体系**：模块 × 类型（如 "车端-故障"、"任务调度-需求"）
- **数据源**：预热时构建一次**内存缓存**经验画像（不每次查库）
  ```
  { engineer_id: { "模块-类型": {count, last_closed_at} } }
  ```
- **流程**：判定当前工单的 模块×类型 → 查每个工程师在该域的经验分 = count × 最近度
- **打分**：`经验分 = count × 时间新鲜度`，归一化到 0-1 作为 score_B

### 融合
- 两路各自归一化，加权相加：`score_L3 = wA×scoreA + wB×scoreB`
- 初始 wA=0.5, wB=0.5，可在 config.yaml 调

---

## 三、与既有模块的分工（防重叠）

| 模块 | 看什么 | 偏向 |
|------|--------|------|
| L1 LLM | duty_text + responsibility_modules（静态画像） | "**宣称**擅长" |
| L3-A | 相似工单实际解决记录 | "**做过**类似的" |
| L3-B | 工程师×域实际经验统计 | "**长期做**这个域的" |
| 负载均衡 | 在途工单数 | "**当前负担**"（负向）|

L1 与 L3 互补：L1 看"怎么说"，L3 看"做过没/做得多不多"。

---

## 四、待实现文件清单

1. `recall/expertise_recall.py`（新）— B路：工程师×模块×类型经验画像 + 打分
2. `recall/history_recall.py`（改）— 重构为 A路（Qdrant）+ 双路融合
3. `sync/history_sync.py`（改）— 字段修正：task_type 规范化、modules 提取
4. `sync/history_indexer.py`（新）— 存量 closed 工单补索引脚本（手动跑）
5. `config/config.yaml`（改）— 新增 `history_recall` 增强参数 + 双路权重
6. `settings.py`（改）— 加载新配置
7. `ai/core/retrieval.py`（改）— 新增独立集合 `dispatch_history` 的集合管理 + 存取方法（engineer_id 进 payload）

---

## 五、配置项（config.yaml → history_recall）

```yaml
history_recall:
  # 增强参数（A路 + B路共用时间衰减）
  top_k: 5                # 相似聚合取前 K 条
  half_life_days: 90      # 时间衰减半衰期（天）
  sim_threshold: 0.3      # A路相似度阈值
  fault_code_boost: 0.15  # A路 同故障码加分
  robot_type_boost: 0.10  # A路 同车型加分
  decay_weight: 0.5       # 时间衰减占比
  # 双路权重
  weight_a: 0.5           # A路（相似工单聚人）
  weight_b: 0.5           # B路（问题域聚人）
```
