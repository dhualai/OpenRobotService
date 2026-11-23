# L5 · 看板 C · 运力发挥

> 商业问题：已有运力用起来了吗？潜力吃透了多少？  
> 交付排障时，再把「没吃满」拆成**空驶率**、业务分配等待、路径等待，以及**充电、休息**（后两者单独记账，不当作「又一种派单」）。详见 [商业需求](./16-biz.md)。

## 1. 用户会问什么

- 运力使用率高不高？  
- **空驶率**高不高？（车跑了多少是空载）  
- 没变成有效作业的时间，主要在空驶、干等，还是在充电、在休息点？  
- 干等主要是没派到车，还是路上避让？

<div class="fig">
<svg viewBox="0 0 600 160" width="100%" style="max-width:600px;height:auto" xmlns="http://www.w3.org/2000/svg">
  <text x="300" y="22" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#656d76">单车时间 / 里程结构示意</text>
  <rect x="40" y="50" width="160" height="40" fill="#dafbe1" stroke="#1a7f37"/>
  <text x="120" y="75" text-anchor="middle" font-size="12" font-family="system-ui,sans-serif">有效作业</text>
  <rect x="200" y="50" width="100" height="40" fill="#fff8c5" stroke="#9a6700"/>
  <text x="250" y="75" text-anchor="middle" font-size="12" font-family="system-ui,sans-serif">空驶</text>
  <rect x="300" y="50" width="120" height="40" fill="#ffebe9" stroke="#cf222e"/>
  <text x="360" y="75" text-anchor="middle" font-size="12" font-family="system-ui,sans-serif">分配等待</text>
  <rect x="420" y="50" width="140" height="40" fill="#ddf4ff" stroke="#0969da"/>
  <text x="490" y="75" text-anchor="middle" font-size="12" font-family="system-ui,sans-serif">路径等待</text>
  <text x="300" y="120" text-anchor="middle" font-size="11" fill="#555" font-family="system-ui,sans-serif">空驶率看里程结构；等待看原因码（路径等待不算进分配）</text>
</svg>
</div>

## 2. 公式

考察时间段 \(W=[t_0,t_1]\)（见 [事实抽象](./09-fact.md)）。

### 2.1 运力使用率

可用车时 \(T_{\mathrm{avail}}\)，有效作业车时 \(T_{\mathrm{busy}}\)：

\[
\mathrm{Utilization}=\frac{T_{\mathrm{busy}}}{T_{\mathrm{avail}}}
\]

回答「潜力吃透了多少」。使用率上不去时，用下面结构解释。

### 2.2 空驶率（必出）

看板 C **应显式给出空驶率**，不要只埋在「结构」叙述里。

默认按**里程**（与现场「空跑了多少」语感一致）：

\[
\mathrm{IdleRatio}=\frac{\sum_i D_i^{\mathrm{idle}}}{\sum_i D_i}
\]

- \(D_i^{\mathrm{idle}}\)：考察窗内车辆 \(i\) 的**空载行驶里程**（未载货在跑）  
- \(D_i\)：同窗总行驶里程  

| 变体 | 公式意图 | 何时用 |
|------|----------|--------|
| **空驶率（总）** \(\mathrm{IdleRatio}\) | 窗内一切空载里程 / 总里程 | 看板 C 默认卡片 |
| **接单空驶率** \(\mathrm{IdleRatio}^{\mathrm{work}}\) | 仅为接**业务**去取货的空驶 / 总里程 | 归因到业务分配时用；去休息点、去充电位里程另计（见 [L-Assign](./06-l-assign.md)） |

可选并行报**空驶车时比**（同一语义、分母换成车时），与 Utilization 同量纲对照；卡片上须标明是里程还是车时。

解读：空驶率高 ≠ 一定分配差——可能在回休息点、去充电；下钻时用 \(\mathrm{IdleRatio}^{\mathrm{work}}\) 与充/休里程拆开。

### 2.3 等待结构

\[
W^{\mathrm{asn}}=\sum W^{\mathrm{asn}}(\tau)
\qquad
W^{\mathrm{path}}=\sum_i W_i^{\mathrm{path}}
\]

等待记账不重叠，原因码见事实层。充电、休息占时单独列，不并进「空驶率」。

| 读数 | 方向 | 回答 |
|------|------|------|
| Utilization | 常希望↑ | 运力吃透了多少 |
| **空驶率 IdleRatio** | 常希望↓ | 跑的里程里空载占多少 |
| IdleRatio\(^{\mathrm{work}}\) | 常希望↓ | 接业务空驶是否过大 |
| \(W^{\mathrm{asn}}\) / \(W^{\mathrm{path}}\) | ↓ | 干等在派单还是交管 |

## 3. 回溯到算法层

| 现象 | 优先怀疑 |
|------|----------|
| **空驶率高**（尤其 \(\mathrm{IdleRatio}^{\mathrm{work}}\) 高） | [L-Assign](./06-l-assign.md) 业务分配 |
| 空驶高但多属回休息点 / 去充电 | [L-Assign](./06-l-assign.md) §休息 / §充电；看板 C 拆里程 |
| 分配等待高 | [L-Assign](./06-l-assign.md) |
| 充电途中打断多或等桩多 | [L-Assign](./06-l-assign.md) §充电 |
| 空闲车未回休息点 | [L-Assign](./06-l-assign.md) §休息 |
| 路径等待高、冲突或重规划多 | [L-MAPF](./05-l-mapf.md) |

## 4. 数据依赖

行驶里程（空载/载货可分）；带原因码的等待起止；车辆状态；策略版本；查询带考察时间段。

## 相关文档

- [商业需求](./16-biz.md) · [事实抽象](./09-fact.md) · [L-Assign](./06-l-assign.md) · [交付需求](./15-delivery.md)
