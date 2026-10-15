# L1 · 任务子域：业务分配 · 休息 · 充电

> 本模块在「任务 / 车辆调度」一侧，包含三个宜**分开描述**的子问题：  
> **业务任务分配**、**休息**、**充电**。  
> 它们都会影响谁在干什么、车开向哪里，但问题陈述与优化重心不同，不要混成「同一种分配」。  
> 路口时空冲突由 [MAPF](./02-mapf.md) 消解。

---

## 0. 三者怎么并列、怎么区分

| 子问题 | 在问什么 | 与「业务任务分配」的关系 |
|--------|----------|--------------------------|
| **业务任务分配** | 哪台车做哪张业务单（及顺序） | 本子问题本身 |
| **休息** | 没有业务可做时，车是否、以及去哪个**预配置休息点**待命 | **独立策略**；早期可不优化，只做规则回休息点 |
| **充电** | 尽量不在做业务任务的中途被迫去充电；**且**去充电的次数与总充电时间尽可能少 | **独立策略**；会约束「哪些车此刻可接单」，但不等于又一种业务单匹配 |

三者输出都可能变成「下一程目标点」（取货点 / 休息点 / 充电位），再交给 MAPF 生成路径。  
评价时业务匹配、休息行为、充电行为应能分别消融、分别归因（见 [L-Assign](./06-l-assign.md)）。

---

## 1. 业务任务分配

### 1.1 在看什么

下图左侧是待办**业务**任务，右侧是可用车辆。这里要画的箭头是：业务任务 → 车。

<div class="fig">
<svg viewBox="0 0 620 240" width="100%" style="max-width:620px;height:auto;display:block" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="业务任务到车辆匹配示意">
  <text x="70" y="24" font-family="system-ui,sans-serif" font-size="12" fill="#656d76">业务任务 T</text>
  <text x="430" y="24" font-family="system-ui,sans-serif" font-size="12" fill="#656d76">车辆集 V</text>
  <rect x="20" y="40" width="200" height="44" rx="6" fill="#ffebe9" stroke="#cf222e"/>
  <text x="120" y="67" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111">τ₁  A→D  已等 40s</text>
  <rect x="20" y="100" width="200" height="44" rx="6" fill="#ffebe9" stroke="#cf222e"/>
  <text x="120" y="127" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111">τ₂  C→D  刚发布</text>
  <rect x="20" y="160" width="200" height="44" rx="6" fill="#ffebe9" stroke="#cf222e"/>
  <text x="120" y="187" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111">τ₃  A→C  高优先级</text>
  <rect x="400" y="40" width="200" height="44" rx="6" fill="#dafbe1" stroke="#1a7f37"/>
  <text x="500" y="67" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111">V₁ @B  空载</text>
  <rect x="400" y="100" width="200" height="44" rx="6" fill="#dafbe1" stroke="#1a7f37"/>
  <text x="500" y="127" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111">V₂ @A  空载</text>
  <rect x="400" y="160" width="200" height="44" rx="6" fill="#eaeef2" stroke="#6e7781"/>
  <text x="500" y="187" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#111">V₃ @E  忙碌</text>
  <g stroke="#0969da" stroke-width="2" fill="none" marker-end="url(#arr)">
    <defs>
      <marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
        <path d="M0,0 L6,3 L0,6 Z" fill="#0969da"/>
      </marker>
    </defs>
    <path d="M220,182 C300,182 320,122 400,122"/>
    <path d="M220,62 C300,62 320,62 400,62"/>
    <path d="M220,122 C300,122 320,182 400,182" stroke-dasharray="5 4" stroke="#6e7781"/>
  </g>
  <text x="300" y="100" font-family="system-ui,sans-serif" font-size="11" fill="#0969da">优先</text>
  <text x="300" y="150" font-family="system-ui,sans-serif" font-size="11" fill="#6e7781">暂缓</text>
</svg>
<div class="fig-cap">实线 = 已匹配；虚线 = 暂缓。本小节只关心业务箭头，不把回休息点、去充电画进同一套「派单」叙事。</div>
</div>

### 1.2 符号与决策

- 业务任务 \(\tau\in\mathcal{T}\)：取货点 \(p(\tau)\)，送货点 \(d(\tau)\)，发布 \(r(\tau)\)，时限 \(s(\tau)\)，优先级 \(w(\tau)\)  
- 车辆 \(V_i\)：位姿 \(x_i\)，可用时刻 \(t_i^{\mathrm{free}}\)，能力 \(\kappa_i\)；是否因充电策略被标为「暂不可接长单」等，作为**外部约束输入**，不是本小节的决策变量  

\[
a:\mathcal{T}\rightarrow\mathcal{V}\cup\{\emptyset\}
\]

可选：每车业务序列 \(\sigma_i\)。可行性示例：能力兼容；车在决策时刻可接业务（未被充电策略锁死等）。

代价示例：

\[
c(i,\tau)=
\delta(x_i,\,p(\tau))
+\alpha\cdot \max\{0,\; t_i^{\mathrm{free}}-r(\tau)\}
+\beta\cdot \mathbb{1}[\widehat{C}_{i\tau}>s(\tau)]
\]

批量时常 \(\min_a \sum c(a(\tau),\tau)\)。这里的最优 ≠ 联合系统最优；端到端见 [L-Joint](./08-l-joint.md)。

### 1.3 典型失效

空驶很低但超时很多；单车过劳；频繁改派导致重规划爆炸。

---

## 2. 休息（独立子问题）

### 2.1 定义

**休息**：车辆在**当前没有业务任务**时，按规则自动前往（或留在）**预先配置的休息点**待命，而不是停在随便一个路中位置挡道、或无目的地闲逛。

它回答的不是「把哪张业务单派给谁」，而是：

> 空闲车去哪里待命？

### 2.2 阶段重心

| 阶段 | 做法 |
|------|------|
| **早期** | 不必单独做复杂优化。固定休息点表 + 简单规则即可（例如：无任务则回所属休息点 / 最近休息点；有任务则被业务分配召回）。 |
| **中后期** | 休息点**选择**可与更丰富的概念结合，例如：**预规划任务**（预估下一单可能在哪一带，休息点偏向那边）、**投机性等待点选择**（在预期拥堵外或关键走廊口待命）。那时休息策略才有明显的「选点」优化空间。 |

休息占用的车时在 [看板 C](./12-board-c.md) 里应单独记账；早期「回默认休息点」本身通常不作为主优化目标。

### 2.3 与业务分配的接口

- 有业务可派时：业务分配优先召回休息中的车（规则可配置冷却或保持）。  
- 无业务时：休息策略给出目标休息点 → MAPF 负责开过去。  
- 不要把「回休息点」建模成与 \(\tau\) 同类的业务任务去抢同一套 \(c(i,\tau)\)，以免语义和指标搅在一起。

---

## 3. 充电（独立子问题）

### 3.1 定义（问题陈述）

充电策略要解决的核心可以表述为：

> **尽量不在做业务任务的中途被迫去充电；且去充电的次数与总充电时间尽可能少。**

拆开看是两层约束（优先级从前到后）：

1. **不打断业务（硬优先）**：避免执行中因低电改道、中途被迫进桩；  
2. **少充、短充（在满足 1 与电量可行的前提下）**：充电会话次数 \(N_{\mathrm{chg}}\) 与总充电时间尽可能小——不是「多充几趟保平安」，也不是把充电本身做成主商业目标。

等价说法：充电行为尽可能不对「做任务」产生负面影响，同时本身占用运力要省。

「负面影响」主要包括：执行中因低电打断、半路改去充电、为等桩拖长业务完成、或充电安排不当导致可接单运力骤降从而拖垮吞吐/超时；以及无必要的频繁进桩、过长占桩。

### 3.2 在做什么、不在做什么

| 做 | 不做（交给别的子问题） |
|----|------------------------|
| 决定何时允许/触发去充电、去哪个充电资源、充到什么程度再回到可接单 | 不替代业务任务匹配 \(a(\tau)\) |
| 尽量把补电安排在任务间隙、班次间隙或低负载窗口 | 不负责路口避让（MAPF） |
| 在避免中途打断的前提下，减少进桩次数与总充电时长 | 早期也不必与休息选点绑死；可与休息点共址，但是两个策略 |
| 约束业务分配：电量不足的车不可接超程单等 | |

成功的充电策略，直观上应：先压低**业务途中被迫充电 / 低电打断**；再压低**充电次数与总充电时间**；并控制对吞吐与时延的连带伤害（见 [L-Assign](./06-l-assign.md)）。

### 3.3 与业务分配的接口

- 充电策略输出「车是否可接单、是否应去充电、目标桩位」；  
- 业务分配在**可接单集合**上做匹配；  
- 去桩路径由 MAPF 执行。  

评价充电好坏时：主看对业务的扰动，次看次数与占时是否省；不要把「充电会话」当成又一批被优化的搬运单，也不要把「充得越久越好」当成目标。

---

## 4. 和 MAPF 怎么咬合

<div class="pipeline">
  <div class="pipeline-step"><div class="pipeline-sym">业务分配</div><div class="pipeline-lab">a(τ)</div></div>
  <div class="pipeline-arrow">+</div>
  <div class="pipeline-step"><div class="pipeline-sym">休息 / 充电</div><div class="pipeline-lab">独立策略</div></div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step"><div class="pipeline-sym">目标点</div><div class="pipeline-lab">取货/休息/充电</div></div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step"><div class="pipeline-sym">MAPF</div><div class="pipeline-lab">时空路径</div></div>
</div>

| 来源 | 负责 | 不负责 |
|------|------|--------|
| 业务分配 | 谁做哪张业务单 | 回哪个休息点、何时充电 |
| 休息 | 无任务时去哪待命 | 业务匹配优劣 |
| 充电 | 少打断；少次数；少占时 | 路口秒级避让 |
| MAPF | 开往上述目标点且（默认可）无跟随冲突 | 该不该接单、该不该充 |

---

## 5. 相关文档

- 指标：[L-Assign](./06-l-assign.md)（业务分配、休息、充电分节）  
- [MAPF](./02-mapf.md) · [评价方法](./04-method.md) · [理论顶层](./01-theory.md)
