# L1 · 评价方法

> **一句话**：先固定「在什么条件下跑」，再固定「比什么」，最后只允许一次改动一个旋钮——这样比较才叫科学，而不是观感。

## 1. 评测流水线长什么样

<div class="pipeline">
  <div class="pipeline-step"><div class="pipeline-sym">S</div><div class="pipeline-lab">场景包·种子</div></div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step"><div class="pipeline-sym">θ</div><div class="pipeline-lab">Assign+MAPF 配置</div></div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step"><div class="pipeline-sym">仿真</div><div class="pipeline-lab">执行并打日志</div></div>
  <div class="pipeline-arrow">⟶</div>
  <div class="pipeline-step"><div class="pipeline-sym">指标</div><div class="pipeline-lab">L2 + Joint + Δ</div></div>
</div>

没有冻结的 \(S\) 与种子，任何「提升」都不可复现。

## 2. 三原则

### 2.1 可复现

一次运行由下式标识：

\[
\mathrm{RunID} = \mathrm{hash}\big(S,\; \mathrm{seed},\; \theta,\; \mathrm{codeRev}\big)
\]

其中 \(\theta\) 至少包含：业务分配策略 ID、休息策略版本、充电策略版本、MAPF 策略 ID、规划时限、**冲突定义版本**（是否含跟随冲突）、执行模型（promise / ADG·TPG）。

### 2.2 分层读数

\[
\text{结论} \;=\;
\underbrace{f_{\mathrm{assign}}(\mathrm{Log})}_{\text{L-Assign}}
\;+\;
\underbrace{f_{\mathrm{mapf}}(\mathrm{Log})}_{\text{L-MAPF}}
\;+\;
\underbrace{f_{\mathrm{joint}}(\mathrm{Log})}_{\text{Joint}}
\]

禁止只用一个总分掩盖：分配变好、路径变坏却被总分「平均掉」。

### 2.3 低成本消融

一次实验声明三元组即可：

\[
\big(\;\underbrace{S,\mathrm{seed}}_{\text{运行条件}},\;
\underbrace{(J_{\mathrm{main}},\,J_{\mathrm{guard}})}_{\text{测试目标}},\;
\underbrace{(\theta_0,\,\theta_1,\,\ell)}_{\text{对照：baseline / variant / 改动层}}\;\big)
\]

- \(J_{\mathrm{main}}\)：主目标，本阶段取**吞吐**（越高越好）  
- \(J_{\mathrm{guard}}\)：护栏，取**完成时延高分位（如 \(C_{\mathrm{P95}}\)）**；有显式截止时再加超时率（越低越好，设阈值）  
- \(\ell\in\{\mathrm{Assign},\mathrm{MAPF},\mathrm{Joint}\}\)：本轮允许改动的层

**合入规则（建议写成门禁）**

\[
\begin{aligned}
&J_{\mathrm{main}}(\theta_1)\;\ge\;J_{\mathrm{main}}(\theta_0)-\varepsilon_{\mathrm{main}} \\
&J_{\mathrm{guard}}(\theta_1)\;\le\;\Gamma_{\mathrm{guard}}
\end{aligned}
\]

（\(\varepsilon_{\mathrm{main}}\) 为允许的吞吐回撤；\(\Gamma_{\mathrm{guard}}\) 为时效护栏上限，默认绑 \(C_{\mathrm{P95}}\)。）

## 3. 场景设计最小集

用拓扑 + 多车把「难在哪」说清楚：

| 场景 | 拓扑直觉 | 主要压力 | 优先观察 |
|------|----------|----------|----------|
| S1 稀疏 | 开阔连通 | 基线正确性 | 完成率、SoC |
| S2 窄通道 | 单点瓶颈对向 | MAPF 协调 | 冲突、等待、死锁 |
| S3 任务突发 | 队列陡增 | 分配策略 | 分配等待、超时 |
| S4 目标冲突密 | 多车涌向同区 | 两层耦合 | 吞吐 + 超时底线 |
| S5 电量与空闲待命 | 低电、充电位有限；无任务回休息点 | 充电策略；休息规则（早期简单即可） | MidTaskChgRate、\(N_{\mathrm{chg}}\)、充电总时间；RestReturnRate |

## 4. 报告应长什么样

| 字段 | 含义 |
|------|------|
| RunID / 场景 / 种子 | 复现钥匙 |
| \(\ell\) 与 \(\theta\) 差分 | 改了什么 |
| L-Assign 表 | 业务分配；休息回点；充电少打断·少次数·少占时… |
| L-MAPF 表 | Makespan、**吞吐量**、SoC、冲突、耗时… |
| Joint | 吞吐、均值/P95 完成时延；有截止时再加超时率 |
| \(\Delta\) vs baseline | 相对变化 |
| 失败用例列表 | 可回放 |

## 5. 常见错误

1. **场景不固定**就比算法；  
2. **同时改分配和 MAPF**还声称「证明了某某」；  
3. **只报平均**不报超时长尾；  
4. 用估计代价 \(c(i,\tau)\) 的离线最优，代替联合仿真结论。

## 6. 向下 / 相关

- [L-MAPF 指标](./05-l-mapf.md)
- [L-Assign 指标](./06-l-assign.md)
- [理论顶层](./01-theory.md)
- 衔接后的业务口径见后续 L3+
