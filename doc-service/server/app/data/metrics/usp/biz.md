# L7 · 商业需求：运力效率

> 在约定场景与服务水平下，把已有运力尽量变成有效产出：做得多、拖得少、潜力吃得透，并且跑得稳。

## 1. 总题怎么拆

不做单一综合分，拆成四块看板：

<div class="fig">
<svg viewBox="0 0 640 220" width="100%" style="max-width:640px;height:auto;display:block" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="运力效率分解为四看板">
  <rect x="200" y="10" width="240" height="44" rx="8" fill="#ddf4ff" stroke="#0969da" stroke-width="2"/>
  <text x="320" y="38" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14" fill="#111">运力效率</text>
  <path d="M320,54 V78" stroke="#8b949e" stroke-width="2"/>
  <path d="M100,78 H540" stroke="#8b949e" stroke-width="2"/>
  <path d="M100,78 V95" stroke="#8b949e"/><path d="M260,78 V95" stroke="#8b949e"/>
  <path d="M380,78 V95" stroke="#8b949e"/><path d="M540,78 V95" stroke="#8b949e"/>
  <rect x="30" y="95" width="140" height="70" rx="6" fill="#dafbe1" stroke="#1a7f37"/>
  <text x="100" y="125" text-anchor="middle" font-size="13" font-family="system-ui,sans-serif" fill="#111">A 做了多少</text>
  <text x="100" y="145" text-anchor="middle" font-size="11" font-family="system-ui,sans-serif" fill="#555">吞吐 / 积压</text>
  <rect x="190" y="95" width="140" height="70" rx="6" fill="#fff8c5" stroke="#9a6700"/>
  <text x="260" y="125" text-anchor="middle" font-size="13" font-family="system-ui,sans-serif" fill="#111">B 做得多快</text>
  <text x="260" y="145" text-anchor="middle" font-size="11" font-family="system-ui,sans-serif" fill="#555">时延 / 超时长尾</text>
  <rect x="350" y="95" width="140" height="70" rx="6" fill="#ffebe9" stroke="#cf222e"/>
  <text x="420" y="125" text-anchor="middle" font-size="13" font-family="system-ui,sans-serif" fill="#111">C 运力发挥</text>
  <text x="420" y="145" text-anchor="middle" font-size="11" font-family="system-ui,sans-serif" fill="#555">使用率 / 结构拆解</text>
  <rect x="510" y="95" width="120" height="70" rx="6" fill="#eaeef2" stroke="#6e7781"/>
  <text x="570" y="125" text-anchor="middle" font-size="13" font-family="system-ui,sans-serif" fill="#111">D 可靠</text>
  <text x="570" y="145" text-anchor="middle" font-size="11" font-family="system-ui,sans-serif" fill="#555">失败 / 介入</text>
  <text x="320" y="200" text-anchor="middle" font-size="12" font-family="system-ui,sans-serif" fill="#656d76">甲方偏 A/B（+D）· 交付偏 C/D · 约 60% / 40%</text>
</svg>
<div class="fig-cap">四块须由同一套事实与定义导出，禁止另起一套数。</div>
</div>

| 分解 | 在问什么 | 看板 |
|------|----------|------|
| 产出 | 做了多少？ | [A](./10-board-a.md) |
| 时效 | 做得多快？拖期可接受吗？ | [B](./11-board-b.md) |
| 运力发挥 | 已有运力用起来了吗？潜力吃透了多少？ | [C](./12-board-c.md) |
| 可靠 | 稳不稳？要不要人盯？ | [D](./13-board-d.md) |

看板 C 的商业提法是**运力使用率**（潜力发挥比例），不是「浪费了多少车时」。**空驶率**以及等待、充/休占时，是解释「为何没吃满」的结构，主要给交付用；空驶率在看板 C 上应作为具名指标出现。

## 2. 干系人

<div class="pipeline">
  <div class="pipeline-step"><div class="pipeline-sym">~60%</div><div class="pipeline-lab">甲方 · 好不好</div></div>
  <div class="pipeline-arrow">+</div>
  <div class="pipeline-step"><div class="pipeline-sym">~40%</div><div class="pipeline-lab">交付 · 为什么</div></div>
  <div class="pipeline-arrow">=</div>
  <div class="pipeline-step"><div class="pipeline-sym">运力效率</div><div class="pipeline-lab">可验收 · 可迭代</div></div>
</div>

- [甲方需求](./14-client.md)  
- [交付需求](./15-delivery.md)

## 3. 商业目标与算法闸门

主目标：选定时间段下的吞吐量。底线：完成时延高分位（及死锁等）；有显式截止时再加超时率。运力使用率作为并列观察，公式见 [看板 C](./12-board-c.md)。

\[
\max\ \mathrm{Throughput}
\quad\text{s.t.}\quad
C_{\mathrm{P95}}\le\Gamma_{\mathrm{p95}},\;
N_{\mathrm{deadlock}}=0\ \text{（或项目约定上限）}
\quad\text{（可选）}\ \mathrm{LateRate}\le\Gamma_{\mathrm{late}}
\]

与 [观测层](./07-bridge.md)、[L-Joint](./08-l-joint.md) 一致：商业语言和研发门禁说同一件事。

## 4. 前提

1. 事实可回放、定义无歧义（[事实抽象](./09-fact.md)）；  
2. 分配与路径可独立替换对比，端到端用联合结果验收；  
3. 仿真按指标清单补齐，而不是先盲扩能力。

## 5. 阅读路径

| 方向 | 路径 |
|------|------|
| 理论向下 | L0 → L1 → L2 → **L3 观测层** |
| 商业向上 | **L7** → L6 → L5 → L4 → **L3 观测层** |

两边在 L3 交汇。
