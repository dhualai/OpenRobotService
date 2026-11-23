# L6 · 甲方需求

> 用业务语言说清运力效率好不好：产出、时效、长尾，以及是否信得过调度表现。

## 1. 甲方关心什么

<div class="fig">
<svg viewBox="0 0 600 150" width="100%" style="max-width:600px;height:auto" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="甲方关注点">
  <rect x="20" y="40" width="130" height="70" rx="8" fill="#dafbe1" stroke="#1a7f37" stroke-width="2"/>
  <text x="85" y="70" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14" fill="#111">产出</text>
  <text x="85" y="92" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">够不够 · 积压</text>
  <rect x="165" y="40" width="130" height="70" rx="8" fill="#fff8c5" stroke="#9a6700" stroke-width="2"/>
  <text x="230" y="70" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14" fill="#111">时效</text>
  <text x="230" y="92" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">快不快 · 拖多久</text>
  <rect x="310" y="40" width="130" height="70" rx="8" fill="#ddf4ff" stroke="#0969da" stroke-width="2"/>
  <text x="375" y="70" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14" fill="#111">稳健</text>
  <text x="375" y="92" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">稳不稳 · 要人盯吗</text>
  <rect x="455" y="40" width="130" height="70" rx="8" fill="#eaeef2" stroke="#6e7781" stroke-width="2"/>
  <text x="520" y="70" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14" fill="#111">感官</text>
  <text x="520" y="92" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="#555">看着顺不顺眼</text>
</svg>
<div class="fig-cap">主叙事用业务词；感官单独说，不和吞吐搅成一个分。</div>
</div>

| 诉求 | 看板 | 角色 |
|------|------|------|
| 做了多少 | [看板 A](./10-board-a.md) | 主读数 |
| 做得多快 | [看板 B](./11-board-b.md) | 底线（含长尾） |
| 能不能稳跑 | [看板 D](./13-board-d.md) | 验收辅读 |
| 看起来是否合理 | [感官看板](./18-sense.md) | 信任与解释；不替代吞吐 |

## 2. 成功长什么样

在约定场景与服务水平下：

\[
\mathrm{Throughput}\ \text{达到或优于约定基线}
\qquad\text{且}\qquad
C_{\mathrm{P95}}\le\Gamma_{\mathrm{p95}}
\quad\text{（有显式截止时再要求}\ \mathrm{LateRate}\le\Gamma_{\mathrm{late}}\text{）}
\]

用多看板展示，不做单一综合分。

## 3. 沟通约束

1. 产出、时效、可靠分开说；运力发挥（看板 C）多由交付解读；感官另页，不和「好不好」打成一锅。  
2. 时效以完成时延分布说话：均值看中心，**必须同时报高分位（如 P95）**；禁止只报平均。  
3. 有截止时间才谈超时率；验收前后冻结截止规则（若有）与考察时间段定义（[事实抽象](./09-fact.md)）。  
4. 策略变更可追溯到版本。  
5. 「绕远、舍近求远」要用业务语言解释，不能只说「算法算出来的」。

## 4. 和交付怎么分工

| 甲方 | 交付 |
|------|------|
| 定业务目标与时效底线 | 把问题落到等待类型或算法层 |
| 看 A/B/D 判断好不好 | 看 C/D 解释为什么；用感官看板区分真差与反直觉 |
| 验收签字 | 排障、售后与现场说明 |

详见 [交付需求](./15-delivery.md)。

## 相关文档

- [商业需求](./16-biz.md) · [观测层](./07-bridge.md) · [L-Joint](./08-l-joint.md)
