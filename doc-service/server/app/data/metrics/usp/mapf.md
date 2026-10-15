# L1 · MAPF：多车路径规划

> 给定每台车的起终点，在同一张路网、同一套时间轴上，为所有车找到互不冲突的路径。  
> 可落地的 AGV 方案通常还须处理**跟随冲突**——这是用 ADG/TPG 按「已完成结果」释放后续路径、而不是按时间承诺（promise）执行的基础。

## 1. 先看一张「会撞」的图

两台车在交叉口相遇：若都坚持最短路且同秒通过 \(B\)，就会发生**点冲突**。

<div class="fig">
<svg viewBox="0 0 560 220" width="100%" style="max-width:560px;height:auto;display:block" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="交叉口点冲突示意">
  <g stroke="#8b949e" stroke-width="2">
    <line x1="40" y1="110" x2="520" y2="110"/>
    <line x1="280" y1="30" x2="280" y2="190"/>
  </g>
  <circle cx="280" cy="110" r="26" fill="#fff8c5" stroke="#9a6700" stroke-width="2"/>
  <text x="280" y="114" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14" fill="#111">B</text>
  <text x="70" y="100" font-family="system-ui,sans-serif" font-size="12" fill="#555">A</text>
  <text x="480" y="100" font-family="system-ui,sans-serif" font-size="12" fill="#555">C</text>
  <text x="292" y="48" font-family="system-ui,sans-serif" font-size="12" fill="#555">D</text>
  <text x="292" y="180" font-family="system-ui,sans-serif" font-size="12" fill="#555">E</text>
  <!-- V1 left to right -->
  <rect x="100" y="78" width="70" height="28" rx="4" fill="#ddf4ff" stroke="#0969da"/>
  <text x="135" y="97" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111">V₁→C</text>
  <polygon points="178,92 198,92 198,86 214,96 198,106 198,100 178,100" fill="#0969da"/>
  <!-- V2 top to bottom -->
  <rect x="246" y="40" width="70" height="28" rx="4" fill="#dafbe1" stroke="#1a7f37"/>
  <text x="281" y="59" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111">V₂→E</text>
  <polygon points="275,72 287,72 287,86 295,86 281,104 267,86 275,86" fill="#1a7f37"/>
  <rect x="150" y="150" width="260" height="36" rx="6" fill="#ffebe9" stroke="#cf222e"/>
  <text x="280" y="173" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" fill="#111">冲突：同一时刻两车都要占 B</text>
</svg>
<div class="fig-cap">MAPF 要消解的是这类时空冲突，而不是各自最短路的简单叠加。</div>
</div>

MAPF 要做的，不是「每台车各自最短」，而是「**联合起来仍然可行**」——常见手段：其中一车等待、绕行、或调整到达顺序。

## 2. 问题定义

### 2.1 输入

- 图 \(G=(N,E)\)
- 车辆 \(i=1,\ldots,k\)，起点 \(s_i\in N\)，目标 \(g_i\in N\)
- 离散时间 \(t=0,1,2,\ldots\)；每步可沿边移动一步，或在原地等待

单车路径：

\[
\pi_i = \big( \pi_i(0),\;\pi_i(1),\;\ldots,\;\pi_i(T_i) \big),\quad
\pi_i(0)=s_i,\;\pi_i(T_i)=g_i,\;
(\pi_i(t),\pi_i(t+1))\in E \;\text{或}\; \pi_i(t+1)=\pi_i(t)
\]

联合解：

\[
\Pi=\{\pi_1,\ldots,\pi_k\}
\]

### 2.2 冲突

以下约束在一次评测内必须写死。对**可落地的 AGV 调度**，默认同时禁止点冲突、边冲突与跟随冲突；论文式变体若关闭跟随，须在场景说明中显式声明，且通常不能直接当作现场可执行方案。

**点冲突（vertex conflict）**  
存在 \(i\neq j\) 与时刻 \(t\)，使得

\[
\pi_i(t)=\pi_j(t)
\]

**边冲突 / 对向交换（edge / swapping conflict）**  
存在 \(i\neq j\) 与时刻 \(t\)，使得

\[
\pi_i(t)=u,\;\pi_i(t+1)=v
\quad\text{且}\quad
\pi_j(t)=v,\;\pi_j(t+1)=u
\]

**跟随冲突（following conflict）**  
前车尚未真正让出某资源时，后车已按计划「贴上去」。离散形式常见写法是：存在 \(i\neq j\) 与时刻 \(t\)，使得后车进入前车刚占用或即将交出的顶点而缺少足够间隔，例如

\[
\pi_i(t)=v,\;\pi_j(t+1)=v
\quad(i\text{ 为前车，}j\text{ 为后车；具体间隔按项目运动学写死})
\]

只消解点/边冲突、允许紧跟随，往往依赖「大家都会按时到达」的时间承诺。现场一旦延误，承诺失效，容易贴脸、急停甚至死锁。

无上述禁止冲突的 \(\Pi\) 称为**可行解**。

### 2.3 为何 AGV 落地要带跟随冲突：ADG / TPG

仓内可执行路径，很少是「按墙上时钟 promise 逐步走」；更常见是用 **ADG（Action Dependency Graph）** 或 **TPG（Temporal Plan Graph）** 一类机制：

- 规划产出的是带依赖的动作/路段顺序；  
- **执行时按前序动作是否已经完成**来释放后序占用，而不是假设车辆一定在某时刻到达；  
- 延误时后车自动多等，而不是继续按失效的时间表往前冲。

要让这种「按完成结果释放」站得住，规划阶段就须把跟随关系消解干净：后车占用某资源，必须在依赖上排在前车释放该资源之后。  
若规划里仍允许紧跟随、只靠时间戳隔开，ADG/TPG 无法从「已完成」推出安全释放，执行层又会退回 promise，现场一抖就碎。

因此本体系默认：

| 设定 | 适用 |
|------|------|
| 禁止点 + 边 + **跟随** | AGV 可落地方案、系统仿真与现场相关评测 |
| 仅点 + 边（跟随可选） | 学术对照或明确声明的非执行向实验 |

冲突定义版本应记入评测配置（见 [评价方法](./04-method.md)），避免「仿真能跑、上车不行」却对不上账。

### 2.4 目标函数

**Makespan（最晚完工）**

\[
\mathrm{Makespan}(\Pi)=\max_{1\le i\le k} T_i
\]

**Sum of Costs / Flowtime（总代价）**

\[
\mathrm{SoC}(\Pi)=\sum_{1\le i\le k} T_i
\]

> 约定：到达 \(g_i\) 后的等待是否计入 \(T_i\)，必须事先声明（经典设定通常计入直至「不再离开目标」的时刻）。

可选代理：**Sum-of-Fuel**（不计等待的移动步数之和），更贴近能耗/磨损。

## 3. 与任务子域的接口

MAPF 通常拿到的是「每台车下一段要去的点」。这些点来自任务子域——可能是业务取送点、预配置休息点，或充电位：

<div class="pipeline" role="img" aria-label="任务子域到MAPF到轨迹">
  <div class="pipeline-step">
    <div class="pipeline-sym">业务‖休息‖充电</div>
    <div class="pipeline-lab">任务子域</div>
  </div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step">
    <div class="pipeline-sym">目标序列</div>
    <div class="pipeline-lab">各车途经/终点</div>
  </div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step">
    <div class="pipeline-sym">Π</div>
    <div class="pipeline-lab">MAPF 联合路径</div>
  </div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step">
    <div class="pipeline-sym">轨迹</div>
    <div class="pipeline-lab">下发 / 重规划</div>
  </div>
</div>

- 任一子策略改终点 → MAPF 实例变了  
- MAPF 只保证「走得通」，**不保证**「这单该不该派」「该不该回休息点」「该不该去充电」

因此消融时路径层与任务子域（及子域内三者）应可独立替换，见 [任务子域](./03-assign.md) 与 [评价方法](./04-method.md)。

## 4. 在线情形

仓内很少一次解完就结束。更常见：

- 新任务到达 → 部分车目标更新 → **重规划**
- 某车延误 → 原时间表不可信 → 依赖 ADG/TPG 按完成结果卡住后车，或触发局部修复

评价在线质量时，除 Makespan、SoC 外，还要看**吞吐量**（须绑定考察时间段），以及重规划次数、规划耗时、死锁次数——见 [L-MAPF 指标](./05-l-mapf.md)。  
评测配置须写明：是否禁止跟随冲突、执行层是 promise 还是 ADG/TPG。

## 5. 最小例子

| 项目 | 值 |
|------|-----|
| 车 | \(V_1: A\to C\)，\(V_2: D\to E\)，共享交叉口 \(B\) |
| 若各走最短且不同步 | 可能在 \(B\) 点冲突 |
| 一种可行解 | \(V_2\) 在 \(D\) 等待 1 步，再过 \(B\) |
| 代价 | Makespan 可能 +1；SoC 通常也 +1（等待计入时） |

## 6. 向下 / 相关

- 指标细表：[L-MAPF](./05-l-mapf.md)
- 总览：[理论顶层](./01-theory.md)
- 对照阅读：[任务子域](./03-assign.md)
