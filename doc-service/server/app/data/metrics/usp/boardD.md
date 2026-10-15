# L5 · 看板 D · 服务可靠性

> 该完成的有没有完成、要不要人救火。验收与售后共用；不与吞吐合成一个分。

## 1. 用户会问什么

- 失败、取消都因为什么？  
- 人工介入有多频繁？  
- 有没有死锁或不可恢复卡住？

<div class="pipeline">
  <div class="pipeline-step"><div class="pipeline-sym">失败</div><div class="pipeline-lab">原因分布</div></div>
  <div class="pipeline-arrow">+</div>
  <div class="pipeline-step"><div class="pipeline-sym">介入</div><div class="pipeline-lab">人工次数</div></div>
  <div class="pipeline-arrow">+</div>
  <div class="pipeline-step"><div class="pipeline-sym">死锁</div><div class="pipeline-lab">硬失败</div></div>
  <div class="pipeline-arrow">=</div>
  <div class="pipeline-step"><div class="pipeline-sym">可靠性</div><div class="pipeline-lab">能不能稳跑</div></div>
</div>

## 2. 公式

\[
N_{\mathrm{fail}}=\#\{\text{failed/cancelled}\}
\qquad
\mathrm{FailRate}=\frac{N_{\mathrm{fail}}}{|\mathcal{T}|}
\qquad
\mathrm{FailShare}(c)=\frac{N_{\mathrm{fail}}(c)}{N_{\mathrm{fail}}+\varepsilon}
\]

\[
N_{\mathrm{intervene}}=\#\{\text{intervene}\}
\qquad
N_{\mathrm{deadlock}}=\#\{\text{deadlock}\}
\]

完成率可与看板 A 共用；本看板侧重异常结构。

## 3. 受众

甲方看「稳不稳」；交付用作售后与排障入口。

## 4. 数据依赖

失败/取消及原因、介入事件、死锁检测日志，见 [事实抽象](./09-fact.md)。

## 相关文档

- [看板 A](./10-board-a.md) · [看板 C](./12-board-c.md) · [甲方需求](./14-client.md) · [交付需求](./15-delivery.md)
