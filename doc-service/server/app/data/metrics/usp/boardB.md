# L5 · 看板 B · 任务时效

> 做得够不够快、有没有拖太久。在吞吐量优先的前提下，这里是必须看得见的底线。  
> **通用底座是完成时延的分布**（均值 + 分位）；「超时率」只在业务定义了显式截止时间时才有意义。

## 1. 用户会问什么

- 从下发到完成大概要多久？（平均 / 中位）  
- 有没有特别慢的长尾？（高分位）  
- （若业务有截止时间）超时比例多少？

<div class="fig">
<svg viewBox="0 0 560 150" width="100%" style="max-width:560px;height:auto" xmlns="http://www.w3.org/2000/svg">
  <text x="280" y="24" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#656d76">完成时延分布示意</text>
  <g fill="#ddf4ff" stroke="#0969da">
    <rect x="60" y="90" width="40" height="30"/>
    <rect x="120" y="70" width="40" height="50"/>
    <rect x="180" y="50" width="40" height="70"/>
    <rect x="240" y="60" width="40" height="60"/>
    <rect x="300" y="80" width="40" height="40"/>
    <rect x="360" y="95" width="40" height="25"/>
    <rect x="420" y="40" width="40" height="80" fill="#ffebe9" stroke="#cf222e"/>
  </g>
  <line x1="40" y1="120" x2="520" y2="120" stroke="#8b949e"/>
  <text x="440" y="36" font-family="system-ui,sans-serif" font-size="11" fill="#cf222e">长尾</text>
  <line x1="180" y1="50" x2="180" y2="120" stroke="#1a7f37" stroke-dasharray="3 2"/>
  <text x="180" y="140" text-anchor="middle" font-family="system-ui,sans-serif" font-size="10" fill="#1a7f37">均值示意</text>
  <line x1="300" y1="45" x2="300" y2="120" stroke="#9a6700" stroke-dasharray="4 3"/>
  <text x="300" y="36" text-anchor="middle" font-family="system-ui,sans-serif" font-size="10" fill="#9a6700">P95</text>
</svg>
<div class="fig-cap">时效的通用读法是分布：均值看中心，P50/P95 看典型与长尾。只报平均会盖住长尾；有截止时间时再叠加超时率。</div>
</div>

## 2. 公式

与看板 A 共用考察时间段 \(W=[t_0,t_1]\)（默认墙上时钟；见 [事实抽象](./09-fact.md)）。对齐 [L-Joint](./08-l-joint.md)。

### 2.1 完成时延分布（通用，默认展示）

只对**时间段内已完成**的任务，取发布到完成的时长：

\[
L(\tau)=C(\tau)-r(\tau)
\qquad
\mathcal{L}_W=\big\{L(\tau)\;\big|\; C(\tau)\in[t_0,t_1]\big\}
\]

在 \(\mathcal{L}_W\) 上报告：

\[
\overline{L}=\frac{1}{|\mathcal{L}_W|}\sum_{L\in\mathcal{L}_W} L
\qquad
C_{\mathrm{P50}},\;C_{\mathrm{P95}},\;C_{\mathrm{P99}}
=\mathrm{Percentile}\{\mathcal{L}_W\}
\]

| 读数 | 角色 |
|------|------|
| \(\overline{L}\)（平均任务时间） | 中心趋势；沟通成本低 |
| \(C_{\mathrm{P50}}\) | 典型完成要多久 |
| \(C_{\mathrm{P95}}\)（及可选 P99） | **底线**：长尾是否可接受 |

不依赖截止时间字段。卡片上同时展示时间段与样本量 \(|\mathcal{L}_W|\)。  
合入/对比时：吞吐为主；时效底线**优先约束 \(C_{\mathrm{P95}}\)**（或约定的高分位），均值作并列观察，避免「平均好看、尾巴很差」。

### 2.2 超时率（有显式截止时间时才启用）

许多现场**没有**为每张业务单定义 \(s(\tau)\)。此时不要虚构超时率，也不要用「经验阈值」冒充业务截止，除非项目书面约定。

当任务带截止时间 \(s(\tau)\) 时，取时间段内**发布**、并在 \(t_1\) 纳入结算的 \(\mathcal{T}_{\mathrm{settled}}\)：

\[
\mathrm{LateRate}(R,W)
=\frac{|\{\tau\in\mathcal{T}_{\mathrm{settled}}:\mathrm{late}(\tau)\}|}{|\mathcal{T}_{\mathrm{settled}}|}
\]

\(\mathrm{late}(\tau)\)：已超过 \(s(\tau)\)（含「未完成但截止已过」）。结算细则见事实层。  
无截止时间的任务不进本指标分母；全窗无截止则本卡隐藏或标「未定义」。

### 2.3 可选：分配响应时延

与端到端完成时延分开展示：

\[
W^{\mathrm{asn}}(\tau)=t_{\mathrm{asn}}(\tau)-r(\tau)
\qquad
\overline{W}^{\mathrm{asn}},\;W^{\mathrm{asn}}_{\mathrm{P50}},\;W^{\mathrm{asn}}_{\mathrm{P95}}
\]

只对已成功分配的任务统计。

## 3. 作为底线

在与看板 A 相同的时间段下：

- **始终可用：** \(C_{\mathrm{P95}}\)（及均值作辅助）设上限；  
- **有截止定义时：** 再加 \(\mathrm{LateRate}\) 上限。  

与吞吐量构成「主目标 + 时效底线」。见 [观测层](./07-bridge.md)。

## 4. 数据依赖

发布时间、完成时间（分布类指标必需）；截止时间（仅超时率）；查询带时间段起止。

## 相关文档

- [看板 A](./10-board-a.md) · [L-Joint](./08-l-joint.md) · [L-Assign](./06-l-assign.md)
