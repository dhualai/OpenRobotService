# L0 · 理论顶层：多车调度评价与优化

> **一句话**：在路网拓扑上，多台 AGV 既要「接到正确的任务」，又要「互不卡住地跑完」；理论层先把这两件事说清楚，再谈怎么量好坏。

## 1. 场景长什么样

下图是一个极简仓内拓扑：节点是站点/路口，边是可通行路段；三台车 \(V_1,V_2,V_3\) 各自有当前位置；待办任务挂在取货点/送货点上。

<div class="fig">
<svg viewBox="0 0 640 280" width="100%" style="max-width:640px;height:auto;display:block" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="仓内拓扑与多车多任务示意">
  <rect x="0" y="0" width="640" height="280" fill="transparent"/>
  <!-- edges -->
  <g stroke="#8b949e" stroke-width="2" fill="none">
    <line x1="90" y1="90" x2="220" y2="90"/>
    <line x1="220" y1="90" x2="360" y2="90"/>
    <line x1="220" y1="90" x2="220" y2="190"/>
    <line x1="220" y1="190" x2="360" y2="190"/>
  </g>
  <!-- nodes -->
  <g font-family="system-ui,sans-serif" font-size="12" text-anchor="middle">
    <circle cx="90" cy="90" r="28" fill="#ddf4ff" stroke="#0969da" stroke-width="2"/>
    <text x="90" y="86" fill="#111">A</text>
    <text x="90" y="102" fill="#555" font-size="10">拣选</text>
    <circle cx="220" cy="90" r="28" fill="#ddf4ff" stroke="#0969da" stroke-width="2"/>
    <text x="220" y="86" fill="#111">B</text>
    <text x="220" y="102" fill="#555" font-size="10">交叉口</text>
    <circle cx="360" cy="90" r="28" fill="#ddf4ff" stroke="#0969da" stroke-width="2"/>
    <text x="360" y="86" fill="#111">D</text>
    <text x="360" y="102" fill="#555" font-size="10">出库</text>
    <circle cx="220" cy="190" r="28" fill="#ddf4ff" stroke="#0969da" stroke-width="2"/>
    <text x="220" y="186" fill="#111">C</text>
    <text x="220" y="202" fill="#555" font-size="10">缓存</text>
    <circle cx="360" cy="190" r="28" fill="#ddf4ff" stroke="#0969da" stroke-width="2"/>
    <text x="360" y="186" fill="#111">E</text>
    <text x="360" y="202" fill="#555" font-size="10">待机</text>
  </g>
  <!-- vehicles -->
  <g font-family="system-ui,sans-serif" font-size="11">
    <rect x="188" y="48" width="64" height="22" rx="4" fill="#dafbe1" stroke="#1a7f37"/>
    <text x="220" y="63" text-anchor="middle" fill="#111">V₁ @B</text>
    <rect x="58" y="48" width="64" height="22" rx="4" fill="#dafbe1" stroke="#1a7f37"/>
    <text x="90" y="63" text-anchor="middle" fill="#111">V₂ @A</text>
    <rect x="188" y="228" width="64" height="22" rx="4" fill="#dafbe1" stroke="#1a7f37"/>
    <text x="220" y="243" text-anchor="middle" fill="#111">V₃ @C</text>
  </g>
  <!-- tasks -->
  <g font-family="system-ui,sans-serif" font-size="11">
    <rect x="430" y="60" width="170" height="56" rx="6" fill="#ffebe9" stroke="#cf222e"/>
    <text x="515" y="82" text-anchor="middle" fill="#111">任务 τ₁：A → D</text>
    <text x="515" y="100" text-anchor="middle" fill="#555" font-size="10">取货 A / 送货 D</text>
    <rect x="430" y="140" width="170" height="56" rx="6" fill="#ffebe9" stroke="#cf222e"/>
    <text x="515" y="162" text-anchor="middle" fill="#111">任务 τ₂：C → D</text>
    <text x="515" y="180" text-anchor="middle" fill="#555" font-size="10">取货 C / 送货 D</text>
  </g>
  <text x="12" y="20" font-family="system-ui,sans-serif" font-size="12" fill="#656d76">路网 G=(N,E) · 多车 + 多任务</text>
</svg>
<div class="fig-cap">绿框 = 车辆当前位置；红框 = 待办任务；圆点 = 拓扑节点。</div>
</div>

读图时只需记住三件事：

1. **拓扑**决定「能怎么走」；
2. **车辆状态**决定「谁现在在哪、能不能接单」；
3. **任务**决定「要把货从哪搬到哪」。

调度软件要同时回答：**业务谁去做、空闲去哪待命、何时补电**（任务子域），以及 **怎么走**（多车路径）。

## 2. 问题总览

把一次调度周期抽象为：任务子域给出各车目标点，再规划路径，最后得到可下发轨迹。

<div class="pipeline" role="img" aria-label="任务子域 到 多车路径 到 可执行轨迹">
  <div class="pipeline-step">
    <div class="pipeline-sym">π<sub>task</sub></div>
    <div class="pipeline-lab">任务子域</div>
  </div>
  <div class="pipeline-arrow" aria-hidden="true">⟶</div>
  <div class="pipeline-step">
    <div class="pipeline-sym">π<sub>mapf</sub></div>
    <div class="pipeline-lab">多车路径</div>
  </div>
  <div class="pipeline-arrow" aria-hidden="true">⟶</div>
  <div class="pipeline-step">
    <div class="pipeline-sym">轨迹</div>
    <div class="pipeline-lab">可执行轨迹</div>
  </div>
</div>

任务子域内三者**并列、分开描述**（细节见 [任务子域](./03-assign.md)）：

<div class="pipeline" role="img" aria-label="业务分配 休息 充电 并列">
  <div class="pipeline-step"><div class="pipeline-sym">业务分配</div><div class="pipeline-lab">谁做哪张单</div></div>
  <div class="pipeline-arrow" aria-hidden="true">‖</div>
  <div class="pipeline-step"><div class="pipeline-sym">休息</div><div class="pipeline-lab">无任务回休息点</div></div>
  <div class="pipeline-arrow" aria-hidden="true">‖</div>
  <div class="pipeline-step"><div class="pipeline-sym">充电</div><div class="pipeline-lab">少打断·少次数·少占时</div></div>
</div>

用符号写是：

\[
\pi_{\mathrm{task}}\;\longrightarrow\;\pi_{\mathrm{mapf}}\;\longrightarrow\;\text{可执行轨迹}
\]

其中 \(\pi_{\mathrm{task}}\) 含业务分配、休息、充电三类策略输出（目标点），不是「同一种派单」。

| 层 | 输入（直觉） | 输出 | 详解 |
|----|--------------|------|------|
| 任务子域 | 待办业务 + 车辆状态 | **业务分配**（独立）；**休息**（无任务回休息点）；**充电**（少打断，且少次数、少占时） | [L1 任务子域](./03-assign.md) |
| 多车路径 (MAPF) | 各车起终点 + 拓扑 + 时间 | 无冲突的时空路径（可落地默认含跟随冲突，以支撑 ADG/TPG） | [L1 MAPF](./02-mapf.md) |

评价不是只看「算法酷不酷」，而是看：**在固定场景下，这两层合在一起的结果能不能复现、能不能对比、能不能归因**——方法见 [L1 评价方法](./04-method.md)。

## 3. 形式化骨架

设：

- 路网 \(G=(N,E)\)：节点集 \(N\)，有向/无向边集 \(E\)
- 车辆集 \(\mathcal{V}=\{V_1,\ldots,V_m\}\)
- 任务集 \(\mathcal{T}=\{\tau_1,\ldots,\tau_n\}\)，每个任务 \(\tau\) 有取货点 \(p(\tau)\)、送货点 \(d(\tau)\)、发布时间 \(r(\tau)\)、可选时限 \(s(\tau)\)

一次完整解可记为：

\[
\Pi = \big(\, a,\; r^{\mathrm{rest}},\; c^{\mathrm{chg}},\; \{\pi_i\}_{V_i\in\mathcal{V}} \,\big)
\]

其中：

- \(a:\mathcal{T}\rightarrow\mathcal{V}\cup\{\emptyset\}\) 为**业务**分配映射（\(\emptyset\) 表示暂未分配）
- \(r^{\mathrm{rest}}\)、\(c^{\mathrm{chg}}\) 为休息与充电策略输出（目标点 / 可接单约束等），与 \(a\) **并列**，不是同一套业务匹配
- \(\pi_i\) 为车辆 \(V_i\) 的时空路径（位置随离散时间变化）

**联合目标（本阶段约定）**：以吞吐为主，以超时为护栏——细节在衔接层与联合指标中展开；理论层先保证问题定义不糊。

## 4. 为什么要「评价理论」

没有统一尺子时，会出现：

- 改了路径规划，吞吐涨了，但超时爆炸——不知道算不算进步；
- 改了业务分配，空驶降了，但地图更堵——两边互相甩锅；
- 把休息、充电和业务派单混成一锅，指标对不上问题陈述。

因此理论层向下要求：

1. **问题定义清楚**（MAPF / 业务分配 / 休息 / 充电各自的可行域与目标）；
2. **指标分层**（L2 起给出可计算量）；
3. **实验可复现**（场景、种子、版本固定）。

## 5. 本阶段边界

| 纳入主线 | 暂不纳入主线 |
|----------|----------------|
| MAPF + 任务子域（业务分配 ‖ 休息 ‖ 充电，分开描述） | 把充电本身当成唯一商业主目标 |
| 可复现仿真评测 | 产线 OEE 全量对标 |
| 吞吐主目标 + 超时底线 | 安全 PL（ISO）混入性能分 |

## 6. 向下阅读

- 先读 [MAPF](./02-mapf.md)：多车在拓扑上如何「不撞、不堵死」
- 再读 [任务子域](./03-assign.md)：业务分配、休息、充电三者分开看
- 然后读 [评价方法](./04-method.md)：怎样公平地比两个策略
- 指标细表：[L-MAPF](./05-l-mapf.md) · [L-Assign](./06-l-assign.md)
