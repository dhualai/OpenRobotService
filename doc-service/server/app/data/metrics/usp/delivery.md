# L6 · 交付实施需求

> 可解释、可验收、可排障：现场慢、堵或要人介入时，能落到等待类型或分配/路径层，而不是只能说「调度不太行」。

## 1. 交付关心什么

<div class="pipeline">
  <div class="pipeline-step"><div class="pipeline-sym">复现</div><div class="pipeline-lab">场景·种子·版本</div></div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step"><div class="pipeline-sym">归因</div><div class="pipeline-lab">分配 vs 路径</div></div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step"><div class="pipeline-sym">动作</div><div class="pipeline-lab">改配置 / 提缺陷</div></div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step"><div class="pipeline-sym">验收</div><div class="pipeline-lab">对照 A/B</div></div>
</div>

| 诉求 | 看板 |
|------|------|
| 运力发挥得怎么样 | [看板 C](./12-board-c.md) |
| 异常与救火 | [看板 D](./13-board-d.md) |
| 对照客户体感 | [看板 A](./10-board-a.md) / [B](./11-board-b.md) |
| 看着别扭怎么解释 | [感官看板](./18-sense.md) |

## 2. 排障决策树

现场偏慢时，按原因码分流：

\[
\begin{aligned}
&W^{\mathrm{asn}}\ \text{偏高} \;\Rightarrow\; \text{查分配 / 车数 / 优先级} \\
&W^{\mathrm{path}}\ \text{偏高} \;\Rightarrow\; \text{查路径、瓶颈路段、冲突} \\
&N_{\mathrm{deadlock}}\!>\!0 \;\Rightarrow\; \text{硬失败，优先路径协同与锁区} \\
&N_{\mathrm{intervene}}\ \text{高} \;\Rightarrow\; \text{流程或策略缺口，记入可靠性}
\end{aligned}
\]

层内读数：[L-Assign](./06-l-assign.md) · [L-MAPF](./05-l-mapf.md)。  
若抱怨是「开得不像话 / 派得不合理」，先走 [感官看板](./18-sense.md)：有原因与对照证据的按反直觉解释，说不清或抖动明显的当缺陷跟。

## 3. 对上游的要求

1. 原因码至少区分分配等待与路径等待，并尽量覆盖绕行、预留、优先级等（[事实抽象](./09-fact.md)）。  
2. 策略版本可追溯，场景可复现。  
3. 仿真与回放可用（[评价方法](./04-method.md)）。  
4. 验收表上的吞吐、超时与看板 A/B 一致。  
5. 高绕行、舍近求远等标记能回放到当时轨迹与分配上下文。

## 4. 验收检查清单

| 项 | 通过标准示例 |
|----|--------------|
| 场景包 | 覆盖客户关键路径与高峰 |
| 基线 | 对照方案跑通并留运行编号 |
| 底线 | \(C_{\mathrm{P95}}\)（及均值）在约定范围内；有截止时再看超时率 |
| 异常 | 死锁为 0 或可解释；介入可统计 |
| 文档 | 指标定义与原因码表已交付 |

## 相关文档

- [甲方需求](./14-client.md) · [商业需求](./16-biz.md)  
- [观测层](./07-bridge.md) · [看板 C](./12-board-c.md) / [D](./13-board-d.md) · [感官看板](./18-sense.md)
