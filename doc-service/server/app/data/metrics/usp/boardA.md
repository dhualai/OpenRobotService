# L5 · 看板 A · 完成与吞吐

> 这段时间做了多少：产出够不够、积压大不大。甲方主看板之一，对应联合结果的主目标。

## 1. 用户会问什么

- 运力够不够用？  
- 这一班或近一小时完成了多少？  
- 还堆着多少没做完？

<div class="fig">
<svg viewBox="0 0 560 140" width="100%" style="max-width:560px;height:auto" xmlns="http://www.w3.org/2000/svg">
  <rect x="30" y="40" width="150" height="70" rx="8" fill="#dafbe1" stroke="#1a7f37" stroke-width="2"/>
  <text x="105" y="70" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" fill="#111">完成量</text>
  <text x="105" y="92" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">吞吐量</text>
  <rect x="210" y="40" width="150" height="70" rx="8" fill="#fff8c5" stroke="#9a6700"/>
  <text x="285" y="70" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" fill="#111">积压</text>
  <text x="285" y="92" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">未完成堆积</text>
  <rect x="390" y="40" width="150" height="70" rx="8" fill="#ddf4ff" stroke="#0969da"/>
  <text x="465" y="70" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" fill="#111">完成率</text>
  <text x="465" y="92" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">该完成的完成了吗</text>
</svg>
</div>

## 2. 公式

先选定考察时间段（默认墙上时钟，如班次；见 [事实抽象](./09-fact.md)）。未选定时不展示吞吐量。

\[
\mathrm{Throughput}(R,W)=\frac{\text{时间段内完成的任务数}}{t_1-t_0}
\qquad
\mathrm{Backlog}(t_1)=\text{截至 }t_1\text{ 已发布且未完成的任务数}
\]

完成率：取时间段内发布的任务，在结束时刻结算后计算：

\[
\mathrm{CompletionRate}(R,W)=\frac{\text{结算集合中已完成数}}{\text{结算集合大小}}
\]

卡片上同时展示时间段、单位与样本量。

## 3. 数据依赖

任务发布时间、完成时间与状态；查询带运行与时间段起止。可选维度：类型、区域、优先级。  
约定见 [事实抽象](./09-fact.md)，汇总对齐 [L-Joint](./08-l-joint.md)，落库见 [观测编码规格](./17-obs-coding.md)。

## 4. 和优化的关系

本看板对应选定时间段下的主目标。只抬吞吐、超时明显变差，仍不应合入（见 [观测层](./07-bridge.md)）。

## 相关文档

- [看板 B](./11-board-b.md) · [L-Joint](./08-l-joint.md) · [甲方需求](./14-client.md)
