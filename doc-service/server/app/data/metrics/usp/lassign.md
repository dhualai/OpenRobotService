# L2 · L-Assign 指标

> 任务子域指标，按三个独立子问题读数：**业务任务分配**、**休息**、**充电**。  
> 路口等待归 [L-MAPF](./05-l-mapf.md)，不要冒充本层问题。

## 1. 分层关系

<div class="pipeline">
  <div class="pipeline-step"><div class="pipeline-sym">业务分配</div><div class="pipeline-lab">空驶·等待·负载</div></div>
  <div class="pipeline-arrow">‖</div>
  <div class="pipeline-step"><div class="pipeline-sym">休息</div><div class="pipeline-lab">回休息点</div></div>
  <div class="pipeline-arrow">‖</div>
  <div class="pipeline-step"><div class="pipeline-sym">充电</div><div class="pipeline-lab">少打断·少次数·少占时</div></div>
</div>

消融时应能**只改其中一个子策略**并只解读对应指标；定义见 [任务子域](./03-assign.md)。

## 2. 业务任务分配

记业务任务 \(\tau\)：发布 \(r\)，分配 \(t_{\mathrm{asn}}\)，完成 \(C\)，时限 \(s\)。考察窗内空载/总里程等照常。

### 2.1 空驶比（接业务）

\[
\mathrm{IdleRatio}^{\mathrm{work}}=\frac{\sum D_i^{\mathrm{idle\to pick}}}{\sum D_i}
\]

只统计为接业务而产生的空驶；去休息点、去充电位的里程另计，避免和休息/充电搅在一起。

### 2.2 分配等待

\[
W^{\mathrm{asn}}(\tau)=t_{\mathrm{asn}}(\tau)-r(\tau)
\qquad
\overline{W}^{\mathrm{asn}},\;W^{\mathrm{asn}}_{\mathrm{P95}}
\]

### 2.3 负载

\[
\mathrm{LoadVar}=\frac{1}{k}\sum_{i}(n_i-\bar{n})^2
\]

\(n_i\) 为窗内完成的**业务**任务数。

### 2.4 超时的分配归因

\[
\mathrm{LateShare}^{\mathrm{asn}}=\frac{N_{\mathrm{late}}^{\mathrm{asn}}}{\sum\mathbb{1}_{\mathrm{late}}+\varepsilon}
\]

超时且更像「派晚 / 未派到」的比例。若实际是充电打断导致，应记入充电指标，而不是笼统算进分配差。

| 指标 | 方向 | 回答 |
|------|------|------|
| IdleRatio\(^{\mathrm{work}}\) | 常希望↓ | 接单空驶是否过大 |
| \(W^{\mathrm{asn}}\) | ↓ | 业务单是否压队列 |
| LoadVar | ↓ | 是否总压同一台车 |
| LateShare\(^{\mathrm{asn}}\) | 解释用 | 超时能否归因到业务派单 |

## 3. 休息

### 3.1 在测什么

无业务时是否按规则回到预配置休息点；中后期才关心「休息点选得是否利于下一单」。

### 3.2 公式示例

\[
\mathrm{RestReturnRate}=\frac{\text{无任务后按规则抵达休息点的次数}}{\text{无任务应回休息点的次数}}
\]

\[
T^{\mathrm{rest}}=\text{考察窗内处于休息点待命的车时}
\qquad
\mathrm{RestTimeRatio}=\frac{\sum T_i^{\mathrm{rest}}}{\sum T_i^{\mathrm{avail}}}
\]

早期：RestReturnRate 达标即可，RestTimeRatio 多为结构观察，不作为主优化。  
中后期（与预规划任务 / 投机性等待点结合时）：可增加「休息点相对下一单取货点的空驶」或「待命位置命中预规划区域的比例」等，项目内再定。

| 指标 | 阶段 | 说明 |
|------|------|------|
| RestReturnRate | 早期即可 | 空闲是否回到配置休息点 |
| RestTimeRatio | 观察 | 休息占可用车时比例；单独记账 |
| 选点相关（可选） | 中后期 | 与预规划 / 投机等待结合后再上 |

## 4. 充电

### 4.1 在测什么

是否做到定义目标：

1. **尽量不在做业务任务的中途被迫去充电**；  
2. **且**去充电的次数与总充电时间尽可能少（在满足 1、且不把车饿到再次中途打断的前提下）。

### 4.2 公式示例

主指标——业务途中打断：

\[
N_{\mathrm{mid\_task\_chg}}=\#\{\text{业务执行中因充电改道或中断的次数}\}
\qquad
\mathrm{MidTaskChgRate}=\frac{N_{\mathrm{mid\_task\_chg}}}{|\mathcal{T}_{\mathrm{done}}|+\varepsilon}
\]

次数与占时（次级，同方向希望↓）：

\[
N_{\mathrm{chg}}=\#\{\text{考察窗内充电会话（进桩）次数}\}
\qquad
T^{\mathrm{chg}}=\sum_i T_i^{\mathrm{chg}}
\qquad
\mathrm{ChargeTimeRatio}=\frac{\sum_i T_i^{\mathrm{chg}}}{\sum_i T_i^{\mathrm{avail}}}
\]

辅助：

\[
N_{\mathrm{soc\_interrupt}}=\#\{\text{低电导致业务无法继续或无法派发}\}
\qquad
N_{\mathrm{chg\_queue}}=\#\{\text{等充电位}\}
\]

解读：先看 MidTaskChgRate；在其中途打断不恶化（且 Joint 吞吐/时延不恶化）的前提下，再比 \(N_{\mathrm{chg}}\) 与 \(T^{\mathrm{chg}}\) / ChargeTimeRatio 是否更省。  
若次数/占时降了但 MidTaskChgRate 升了，说明「少充」过了头，未达定义。  
若 MidTaskChgRate 降了但次数与占时暴涨，说明用「狂充」换不打断，次级目标未达。

| 指标 | 方向 | 回答 |
|------|------|------|
| MidTaskChgRate | ↓ **主** | 是否还在「做任务中途去充电」 |
| \(N_{\mathrm{chg}}\) | ↓ 次 | 去充电次数是否够省 |
| \(T^{\mathrm{chg}}\) / ChargeTimeRatio | ↓ 次 | 总充电时间是否够省 |
| \(N_{\mathrm{soc\_interrupt}}\) | ↓ | 低电是否仍冲击业务 |
| \(N_{\mathrm{chg\_queue}}\) | 视场景 | 是否扎堆等桩拖累 |

## 5. 消融怎么报

固定 MAPF，每次 ideally **只动一个子策略**：

1. 只改业务分配 → 看 §2 + Joint 吞吐/时延；  
2. 只改休息规则/选点 → 看 §3；早期重点 RestReturnRate；  
3. 只改充电策略 → 看 §4：**主看 MidTaskChgRate**，次看 \(N_{\mathrm{chg}}\) 与充电总时间，并附 Joint；  
4. 「有车不干活」感官争议：先分清是在休息点、在充电，还是无故空闲（[感官看板](./18-sense.md)）。
## 相关文档

- 问题定义：[任务子域](./03-assign.md)  
- [L-MAPF](./05-l-mapf.md) · [评价方法](./04-method.md) · [感官看板](./18-sense.md)
