# L4 · L-Joint

> 分配与路径合在一起后的端到端成绩：主看吞吐量，**完成时延分布（尤其高分位）**做底线；有显式截止时间时再看超时率。  
> 时间段与任务圈定以 [事实抽象](./09-fact.md) 为准；优先级与落点以 [观测层](./07-bridge.md) 为准。

## 1. 联合层看什么

<div class="fig">
<svg viewBox="0 0 620 180" width="100%" style="max-width:620px;height:auto;display:block" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="联合层输入输出">
  <rect x="20" y="50" width="130" height="60" rx="8" fill="#dafbe1" stroke="#1a7f37"/>
  <text x="85" y="85" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" fill="#111">任务子域</text>
  <rect x="170" y="50" width="130" height="60" rx="8" fill="#ddf4ff" stroke="#0969da"/>
  <text x="235" y="85" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" fill="#111">路径规划</text>
  <text x="320" y="85" font-size="22" fill="#656d76">⟶</text>
  <rect x="350" y="30" width="250" height="100" rx="8" fill="#fff8c5" stroke="#9a6700" stroke-width="2"/>
  <text x="475" y="60" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14" fill="#111">联合结果</text>
  <text x="475" y="82" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">吞吐量（主）</text>
  <text x="475" y="100" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">均值 / P95 时延（底线）</text>
  <text x="475" y="118" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">完成率 · 人工介入 · 可选超时率</text>
</svg>
<div class="fig-cap">层内指标解释原因；联合结果回答系统整体好不好。对比前先对齐时间段类型与任务到达过程。</div>
</div>

## 2. 计算上下文

| 上下文 | 产品统计页默认 | 算法仿真默认 |
|--------|----------------|--------------|
| 运行 | 现场或系统仿真 | 算法试跑 |
| 考察时间段 | 墙上时钟（如班次） | 整次试跑起止 |
| 吞吐量任务范围 | 时间段内完成 | 同左 |
| 超时 / 完成率任务范围 | 时间段内发布，结束时刻结算 | 同左（试跑结束通常无长期进行中） |

\(T = t_1 - t_0\)。集合与结算见 [事实抽象](./09-fact.md)。

## 3. 公式

### 3.1 吞吐量

\[
\mathrm{Throughput}(R,W)
=\frac{\big|\{\tau:\;C(\tau)\in[t_0,t_1]\}\big|}{T}
\]

须写单位（建议任务数/小时）。按搬运量考核时，分子改为载荷之和。

分子是「这段时间完成了多少」，不是「这段时间新发布了多少」。跨段才完成的任务，计入真正完成的那一段。

### 3.2 时效（完成时延分布 + 可选超时）

时间段内已完成任务的时延样本：

\[
\mathcal{L}_W=\big\{L(\tau)=C(\tau)-r(\tau)\;\big|\; C(\tau)\in[t_0,t_1]\big\}
\]

\[
\overline{L},\quad
C_{\mathrm{P50}},\;C_{\mathrm{P95}},\;C_{\mathrm{P99}}
=\mathrm{Percentile}\{\mathcal{L}_W\}
\]

**默认底线看 \(C_{\mathrm{P95}}\)**（均值 \(\overline{L}\) 作中心观察）。不依赖截止时间。

仅当业务定义了显式截止 \(s(\tau)\) 时，再用时间段内发布、\(t_1\) 结算集算超时率：

\[
\mathrm{LateRate}(R,W)
=\frac{|\{\tau\in\mathcal{T}_{\mathrm{settled}}:\mathrm{late}(\tau)\}|}{|\mathcal{T}_{\mathrm{settled}}|}
\]

无截止字段时不上报 LateRate，详见 [看板 B](./11-board-b.md)。

### 3.3 完成率与人工介入

\[
\mathrm{CompletionRate}(R,W)
=\frac{|\{\tau\in\mathcal{T}_{\mathrm{settled}}:\mathrm{status=done}\}|}{|\mathcal{T}_{\mathrm{settled}}|}
\]

\[
N_{\mathrm{intervene}}(R,W)
=\#\{e\mid e.\mathrm{type=intervene}\land e.ts\in[t_0,t_1]\}
\]

## 4. 和看板的对应

| 读数 | 看板 | 展示时带上 |
|------|------|------------|
| 吞吐量 | [看板 A](./10-board-a.md) | 时间段；按完成时刻统计 |
| 时延均值/分位；可选超时率 | [看板 B](./11-board-b.md) | 时间段；分位用完成样本；超时率须有截止 |
| 完成率、人工介入 | [看板 D](./13-board-d.md) | 时间段；介入落在该段内 |

看板 C（运力使用率与结构）依赖事实层对车时的累计，不单靠本层几个汇总数。

## 5. 对比实验

- 只改一层时：联合结果看端到端，层内指标看问题更可能在哪侧。  
- 两次运行对比：时间段类型、长度（或班次定义）、任务到达过程须一致。  
- 合入门槛见 [观测层](./07-bridge.md)：吞吐为主，时延高分位（及有定义时的超时率）为底线。

## 相关文档

- [观测层](./07-bridge.md) · [事实抽象](./09-fact.md) · [观测编码规格](./17-obs-coding.md)  
- [L-MAPF](./05-l-mapf.md) · [L-Assign](./06-l-assign.md)
