# L2 · L-MAPF 指标

> 只改路径协同层时用这组量。定义写清楚，避免「冲突少了、其实大家都在干等」这类假进步。

## 1. 指标作用在哪一层

<div class="fig">
<svg viewBox="0 0 560 160" width="100%" style="max-width:560px;height:auto" xmlns="http://www.w3.org/2000/svg">
  <rect x="20" y="30" width="120" height="50" rx="6" fill="#ddf4ff" stroke="#0969da"/>
  <text x="80" y="60" text-anchor="middle" font-size="13" font-family="system-ui,sans-serif">π₁</text>
  <rect x="160" y="30" width="120" height="50" rx="6" fill="#ddf4ff" stroke="#0969da"/>
  <text x="220" y="60" text-anchor="middle" font-size="13" font-family="system-ui,sans-serif">π₂</text>
  <rect x="300" y="30" width="120" height="50" rx="6" fill="#ddf4ff" stroke="#0969da"/>
  <text x="360" y="60" text-anchor="middle" font-size="13" font-family="system-ui,sans-serif">π₃</text>
  <text x="220" y="20" text-anchor="middle" font-size="12" fill="#656d76" font-family="system-ui,sans-serif">同一拓扑上的多车轨迹</text>
  <path d="M80,80 V100 H220 V110" stroke="#8b949e" fill="none"/>
  <path d="M220,80 V110" stroke="#8b949e" fill="none"/>
  <path d="M360,80 V100 H220 V110" stroke="#8b949e" fill="none"/>
  <rect x="100" y="110" width="240" height="36" rx="6" fill="#eaeef2" stroke="#6e7781"/>
  <text x="220" y="133" text-anchor="middle" font-size="12" font-family="system-ui,sans-serif">Makespan · 吞吐量 · SoC · 冲突 · 耗时</text>
</svg>
<div class="fig-cap">分配给出的目标视为输入；本层评路径协同。可落地默认还禁止跟随冲突，以支撑 ADG/TPG 按完成结果释放路径（见 [MAPF](./02-mapf.md)）。</div>
</div>

任务子域给出的目标（业务点 / 休息点 / 充电位）当作**给定输入**；本层不评价「单派得对不对」或「该不该去充/休」。

### Makespan 与吞吐量，各自回答什么

经典一次性 MAPF（一批车、一批目标、解完即止）里，**Makespan** 很自然：最后一辆车何时到齐。  

产品侧更常见的是持续在线调度：任务不断到达，车辆做完一单再接下一单。这时没有「整局结束」的天然终点，**吞吐量**和 Makespan 一样重要——甚至更贴近「这段时间路径层到底帮系统送出了多少活」。

| 指标 | 更适合回答 | 典型场景 |
|------|------------|----------|
| Makespan | 这一批目标，整体何时做完？ | 一次性实例、固定任务批、消融里的短 episode |
| 吞吐量 | 选定时间段内，路径执行实际完成了多少任务？ | 持续在线（lifelong）、长仿真、与联合层主目标对齐 |

两者并不互斥：短场景可以同时报；长场景必须以考察时间段为前提来报吞吐量（定义见 [事实抽象](./09-fact.md)）。

## 2. 公式

记可行联合解 \(\Pi=\{\pi_i\}\)，\(T_i\) 为车 \(i\) 到达当前目标并按约定停止计时的时刻；\(k\) 为车辆数。  
一次性评测常跑 \(N\) 个实例；持续在线评测则在某次运行上选定考察时间段 \([t_0,t_1]\)。

### 2.1 成功率

\[
\mathrm{SuccessRate}=\frac{1}{N}\sum_{n=1}^{N}
\mathbb{1}\big[\Pi^{(n)}\ \text{在时限}\ T_{\mathrm{plan}}\ \text{内求得且无死锁}\big]
\]

### 2.2 Makespan / SoC

\[
\mathrm{Makespan}(\Pi)=\max_{1\le i\le k} T_i
\qquad
\mathrm{SoC}(\Pi)=\sum_{1\le i\le k} T_i
\]

报告时给出：均值、分位数（建议 P50/P95），以及相对对照方案的

\[
\Delta\mathrm{SoC}=\frac{\mathrm{SoC}(\theta_1)-\mathrm{SoC}(\theta_0)}{\mathrm{SoC}(\theta_0)}
\]

### 2.3 吞吐量

在考察时间段 \(W=[t_0,t_1]\) 上，与联合层同一口径：分子取**该时间段内完成**的任务数（完成时刻落在窗内），分母为时间长度：

\[
\mathrm{Throughput}_{\mathrm{MAPF}}(R,W)
=\frac{\big|\{\tau:\;C(\tau)\in[t_0,t_1]\}\big|}{t_1-t_0}
\]

建议单位：任务数 / 小时。

说明：

1. **必须先声明考察时间段。** 缺时间段的吞吐量无效。产品仿真器里通常取整次运行；做对照实验时，两侧的时间长度和任务到达过程要对齐。  
2. **本层报吞吐量，不等于替代联合层。** 这里固定分配、只换路径配置，用来看「路径协同有没有把完成节奏带上去」。系统对外说的主读数仍以 [L-Joint](./08-l-joint.md) / 看板 A 为准。  
3. 若业务按搬运量考核，分子可改为载荷之和，与联合层保持同一开关。

### 2.4 冲突与重规划

对日志中检测到的冲突事件计数（须与 [MAPF](./02-mapf.md) 中写死的冲突定义一致；可落地默认含**跟随冲突**）：

\[
N_{\mathrm{conflict}}=\#\{\text{点 / 边 / 跟随冲突触发记录}\}
\qquad
N_{\mathrm{replan}}=\#\{\text{重规划触发}\}
\]

**死锁** \(N_{\mathrm{deadlock}}\)：系统进入无进展状态的次数（需产品定义，例如全体在一段 \(\Delta t\) 内几乎无有效位移，且存在互相等待环）。

### 2.5 规划耗时

设单次规划的墙钟时间为 \(\tau_{\mathrm{cpu}}\)，则

\[
\tau_{\mathrm{P95}}=\mathrm{Percentile}_{95}\{\tau_{\mathrm{cpu}}\}
\]

在线系统通常要求 \(\tau_{\mathrm{P95}}\le T_{\mathrm{slo}}\)。

### 2.6 路径等待

车 \(i\) 在路径执行中因避让或交管产生的等待时长记为 \(W_i^{\mathrm{path}}\)：

\[
W^{\mathrm{path}}=\sum_{i=1}^{k} W_i^{\mathrm{path}}
\quad\text{或}\quad
\overline{W}^{\mathrm{path}}=\frac{1}{k}\sum_{i} W_i^{\mathrm{path}}
\]

必须与**分配等待**（任务还没派到车上的等待）分开记账，否则归因会失效。

## 3. 读数卡片

| 指标 | 期望方向 | 主要回答 |
|------|----------|----------|
| 成功率 | 越高越好 | 这种密度下还能不能稳定求出可行解 |
| Makespan / SoC | 越低越好 | 一批目标的协同是否更紧凑 |
| **吞吐量** | **越高越好** | **选定时间段内，路径层是否帮系统送出了更多完成量** |
| 冲突次数、重规划次数 | 视场景解读 | 协调是否吃力 |
| 死锁次数 | 越低越好（接近 0） | 有没有硬失败 |
| 规划耗时 P95 | 越低越好 | 能不能上线 |
| 路径等待 | 越低越好 | 是不是靠「干等」换来的无冲突 |

读数时注意：Makespan 变好但吞吐量变差，或反过来，都要写进对比结论——两者刻画的时间尺度不同，不能只报对自己有利的那一个。

路径层还容易触发「看起来绕路、停很久等」的感官争议；绕行率、背离目标比例、重规划抖动等见 [感官看板](./18-sense.md)，不要用「把绕行率压到最低」替代吞吐量目标。

## 4. 消融时怎么报

固定场景与随机种子，只改路径规划配置：

1. 先看成功率与死锁；  
2. 短场景 / 固定任务批：看 Makespan、SoC 与路径等待；  
3. 持续在线或长仿真：**必须同时报吞吐量**（写明考察时间段），并与路径等待对照，避免「等出来的无冲突」；  
4. 再看规划耗时 P95 是否仍满足线上时限；  
5. 最后对照联合层的超时底线——防止路径指标好看、任务却大面积拖期。

## 5. 相关文档

- 问题定义：[MAPF](./02-mapf.md)  
- 对照层：[L-Assign](./06-l-assign.md)  
- 时间段口径：[事实抽象](./09-fact.md)  
- 端到端主读数：[L-Joint](./08-l-joint.md)  
- 方法：[评价方法](./04-method.md)
