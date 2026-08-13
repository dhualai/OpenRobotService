"""日志编排器（Orchestrator）— discuss 的核心：循环指挥 Agent

设计（方案 X）：
  - LogSubAgent 降级为"执行单条 directive 的确认者"
  - 本编排器负责真正的多轮规划：
      ① Discovery 出聚光灯（纯程序）
      ② 编排 LLM 每轮看到【用户上下文 + 讨论历史 + Discovery + 前几轮证据】
          → 产出 investigate(directive) 或 conclude
      ③ investigate → LogSubAgent.analyze_directive 执行 → 追加证据 → 回归
      ④ 最多 N 轮，防死循环（directive 去重 + 轮数上限）

角色分工：
  - 编排 LLM: 理解上下文/评论，决定查什么、何时结案（LLM）
  - Discovery(triage): 信号/热窗口/实体（纯程序）
  - LogSubAgent: 执行单条 directive，返回结构化证据（程序）

对用户上下文的处理（决策 Q2）：直接把 task_context + discussion_history 全文给编排 LLM，
由 LLM 提炼（不硬解析）。
"""

import json
import re
import time as _time

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# 编排循环上限 / 软上限
MAX_ROUNDS = 3
SOFT_LIMIT = 2

# 供编排 LLM 阅读的每轮证据数 / 字符数
_EVIDENCE_CHARS = 800


class LogOrchestrator:
    """循环编排器。构造时传入可访问 llm_client 与日志工具的 agent（AiTaskAgent）。"""

    def __init__(self, agent, log_path: str, manual_dir: str = ""):
        self.agent = agent           # AiTaskAgent（提供 _llm_client / _add_trace 等）
        self.llm = getattr(agent, "_llm_client", None)
        self.log_path = log_path
        self.manual_dir = manual_dir

    # ── 入口 ──
    async def run(
        self,
        task_ctx: dict,
        discussion_history: str,
        query: str,
    ) -> dict:
        """执行循环编排，返回结构化结论。"""
        t0 = _time.perf_counter()

        # 保证客户端就绪
        if self.llm is None:
            await self.agent._ensure_clients()
            self.llm = self.agent._llm_client

        # ① Discovery 聚光灯 + 建索引（复用 LogSubAgent 的 index，只 build 一次）
        from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent
        from ai.agents.AiTaskPlatform.log_analyzer.triage import run_triage
        from ai.agents.AiTaskPlatform.handlers.diagnose_flow import _discovery_to_text

        sub = LogSubAgent(self.log_path)
        await sub._ensure_clients()
        triage = run_triage(self.log_path, user_question=query,
                            index=sub._index, manual_dir=self.manual_dir or None)
        discovery_text = _discovery_to_text(triage)

        evidence: list = []
        rounds: list = []      # 每轮 {directive, matched, summary}
        directives_seen = set()

        # ② 循环编排
        conclusion = ""
        fallback = False
        for round_num in range(1, MAX_ROUNDS + 1):
            cmd_text = await self._ask_reviewer(
                task_ctx, discussion_history, query, discovery_text, evidence, rounds, round_num
            )
            cmd = _parse_review_command(cmd_text)

            if not cmd:
                logger.warning(f"编排 R{round_num} 解析失败: {cmd_text[:200]}")
                # 轮数浅则重试一次；深则兜底
                if round_num >= SOFT_LIMIT:
                    conclusion = "编排输出解析失败，基于已收集证据给出初步结论"
                    fallback = True
                    break
                continue

            intent = cmd.get("intent", "conclude")

            if intent == "investigate":
                directive = cmd.get("directive") or {}
                if not directive or not isinstance(directive, dict):
                    directive = {}
                # directive 去重：以规范化 JSON 为键
                key = json.dumps(directive, sort_keys=True, ensure_ascii=False)
                if key in directives_seen:
                    logger.warning(f"编排 R{round_num}: 重复 directive，强制结案")
                    conclusion = cmd.get("conclusion") or "重复查询无新信息，基于现有证据结论"
                    fallback = True
                    break
                directives_seen.add(key)

                # ③ LogSubAgent 执行单条 directive
                try:
                    res = await sub.analyze_directive(
                        directive, evidence=evidence,
                        query_text=cmd.get("reasoning", "")[:80],
                    )
                    matches = []
                    for s in res.get("sample", [])[:6]:
                        matches.append(f"L{s['line']}| {s['summary'][:120]}")
                    rounds.append({
                        "round": round_num,
                        "reasoning": cmd.get("reasoning", ""),
                        "directive": directive,
                        "matched": res.get("matched", 0),
                        "sample": matches,
                    })
                    logger.info(f"编排 R{round_num}: directive {json.dumps(directive, ensure_ascii=False)} "
                                f"→ {res.get('matched', 0)} lines")
                except Exception as e:
                    logger.warning(f"编排 R{round_num} directive 执行失败: {e}")
                    rounds.append({"round": round_num, "reasoning": cmd.get("reasoning", ""),
                                   "directive": directive, "matched": 0, "error": str(e)[:100]})

                # 回归：若达到软上限且已有多轮证据，倾向结案
                if round_num >= SOFT_LIMIT and evidence:
                    conclusion = cmd.get("conclusion") or ""
                    break
            else:
                # conclude
                conclusion = cmd.get("conclusion", "") or cmd.get("reasoning", "")
                fallback = cmd.get("fallback", False)
                logger.info(f"编排 conclude at R{round_num}: {conclusion[:80]}")
                break

        # 结论：编排 LLM 未给出精炼 conclude 时，用结论撰写 LLM 生成精炼报告
        # （ERROR优先信号 + 具体证据行 → 简洁根因/证据/建议），失败才回退文本兜底。
        if not conclusion:
            conclusion = await self._compose_llm_conclusion(discovery_text, evidence)

        elapsed_ms = round((_time.perf_counter() - t0) * 1000)
        logger.info(f"LogOrchestrator done: rounds={len(rounds)}, evidence={len(evidence)}, "
                    f"elapsed={elapsed_ms}ms")

        return {
            "conclusion": conclusion,
            "evidence": evidence[:20],
            "rounds": rounds,
            "discovery": discovery_text,
            "fallback": fallback,
            "elapsed_ms": elapsed_ms,
        }

    # ── 编排 LLM：产出 investigate / conclude 命令 ──
    async def _ask_reviewer(
        self, task_ctx, discussion_history, query, discovery_text, evidence, rounds, round_num
    ) -> str:
        context_block = _build_review_context(
            task_ctx, discussion_history, query, discovery_text, evidence, rounds
        )
        system_prompt = _REVIEW_SYSTEM_PROMPT
        prompt = context_block + f"\n\n（第 {round_num}/{MAX_ROUNDS} 轮决策。请只输出一行JSON命令。）"
        try:
            return await self.llm.complete(
                prompt=prompt, system_prompt=system_prompt,
                max_tokens=600, temperature=0.0,
            )
        except Exception as e:
            logger.warning(f"编排 LLM 调用失败: {e}")
            return ""


    # ── 结论撰写：基于 Discovery + 证据，产出精炼结论（替代"堆原文"兜底）──
    async def _compose_llm_conclusion(self, discovery_text, evidence) -> str:
        try:
            parts = []
            if discovery_text:
                parts.append("## 日志 Discovery（错误优先信号）\n" + discovery_text)
            if evidence:
                evs = "\n".join(f"  L{e['line']}: {e['summary'][:150]}" for e in evidence[-15:])
                parts.append("## 已收集证据日志行\n" + evs)
            if not parts:
                return _compose_fallback_conclusion(discovery_text, evidence)
            prompt = "\n\n".join(parts) + "\n\n请输出精炼排查结论 (markdown)。"
            return (await self.llm.complete(
                prompt=prompt, system_prompt=_CONCLUSION_SYSTEM_PROMPT,
                max_tokens=500, temperature=0.0,
            )).strip()
        except Exception as e:
            logger.warning(f"结论撰写 LLM 失败，回退兜底: {e}")
            return _compose_fallback_conclusion(discovery_text, evidence)


# ════════════════════════════════════════════════════════════
# 编排 Reviewer Prompt
# ════════════════════════════════════════════════════════════

_REVIEW_SYSTEM_PROMPT = """你是AGV调度系统日志排查的总指挥。你只输出【一行JSON】，禁止输出JSON外的散文。

可用命令（每次恰好一个）：

1) 继续查证（证据不足时）:
{"intent":"investigate","reasoning":"为什么要查这一步","directive":{"time_start":"YYYY-MM-DD HH:MM","time_end":"YYYY-MM-DD HH:MM","robot_filter":"车型或空串","task_filter":"任务或空串","error_only":true,"keyword_filter":"关键词或空串","context_lines":2,"max_results":50}}

2) 下结论（证据足够）:
{"intent":"conclude","conclusion":"一句话根因+证据链+建议","fallback":false}

硬性规则:
- time_start/time_end/robot_filter/task_filter 必须来自下方「日志客观事实」或已给证据；绝不虚构日期/车型/ID。
- keyword_filter 应从 Discovery 的「Top 高频错误」里取真实短语（如 存在路径未被接收 / last_node_index跳变），用于精确锁定同类错误行；没有就留空串""。
- time_start 必填（用一个窄时间窗），error_only 默认 true。
- 每轮查证应选一个【尚未查过】的真实错误信号；勿重复已执行轮次的 keyword_filter（看「已执行的查询轮次」），否则视为重复查询。
- 优先查证最能解释用户/评论区症状的信号；若 R1 已命中大量同类错误，下一轮换另一个真实错误信号交叉验证。
- 最多3轮内必须结案；若连续两轮无新信息，直接 conclude。
- conclude 时：一句话根因 + 证据链 + 建议，并**引用能明确定位问题的具体日志行号（如 L126118）及其错误描述**。
- 每次只输出一个JSON命令，别写任何文字。"""


_CONCLUSION_SYSTEM_PROMPT = """你是AGV调度系统日志排查的结论撰写者。请基于给定的 Discovery 信号和已收集的证据日志行，输出一份【精炼的排查结论】markdown 文本。

输出结构（务必简洁，不要复制大段原文）：
1. **根因**：一句话定位根因。若存在 ERROR 根因（如 TimeoutError 超时），应优先作为主因；WARNING 仅为次生/伴随信号。
2. **关键证据行**：引用3-5条最能定位问题的具体日志行（如 `L126118 | [13:57:42] [WARNING] MSG=last_node_index跳变...`），说明它们如何支撑根因。
3. **建议**：1-2条可操作建议。

规则：
- 只能基于给定的 Discovery 和证据行，严禁虚构行号/时间/车型。
- 错误优先顺序：ERROR → WARNING。ERROR 存在时根因围绕 ERROR（如超时），WARNING 作佐证。
- 如果 ERROR 不存在，才以最高频 WARNING 信号为主。
- 控制在 120 字以内的精炼正文（不含行号引用），不要像兜底那样整段堆 Discovery 原文。"""


def _build_review_context(task_ctx, discussion_history, query, discovery_text, evidence, rounds) -> str:
    """组装编排 LLM 的输入：用户上下文 + 评论 + Discovery + 前几轮证据。"""
    parts = ["## 用户问题", query or "帮我看看日志", ""]
    parts.append("## 工单上下文（工程师上一手信息，第一手线索优先）")
    parts.append(_render_task_ctx(task_ctx))
    parts.append("")
    if discussion_history:
        parts.append("## 讨论历史")
        parts.append(discussion_history)
        parts.append("")
    if discovery_text:
        parts.append("## 日志 Discovery（客观信号/热窗口/实体）")
        parts.append(discovery_text)
        parts.append("")

    if evidence:
        parts.append("## 已收集证据（Key 日志行）")
        evs = []
        for e in evidence[-15:]:
            evs.append(f"  L{e['line']}: {e['summary'][:140]}")
        parts.append("\n".join(evs))
        parts.append("")

    if rounds:
        parts.append("## 已执行的查询轮次（避免重复）")
        rlines = []
        for r in rounds:
            if r.get("error"):
                rlines.append(f"  R{r['round']} ✗ {r['error']}")
            else:
                rlines.append(f"  R{r['round']} 命中{r.get('matched','?')}行: {json.dumps(r.get('directive', {}), ensure_ascii=False)}")
        parts.append("\n".join(rlines))
        parts.append("")
    return "\n".join(parts)


def _render_task_ctx(task_ctx) -> str:
    if not isinstance(task_ctx, dict):
        return str(task_ctx)
    lines = []
    for k in ("title", "description", "problem_summary"):
        if task_ctx.get(k):
            lines.append(f"- {k}: {str(task_ctx[k])[:150]}")
    if task_ctx.get("hypotheses"):
        lines.append("- 推测原因: " + "/".join(task_ctx["hypotheses"]))
    if task_ctx.get("ruled_out"):
        lines.append("- 已排除: " + "/".join(task_ctx["ruled_out"]))
    if task_ctx.get("fault_code"):
        lines.append(f"- 故障码: {task_ctx['fault_code']}")
    if task_ctx.get("robot_type"):
        lines.append(f"- 车型: {task_ctx['robot_type']}")
    if task_ctx.get("collected_info"):
        try:
            ci = json.dumps(task_ctx["collected_info"], ensure_ascii=False)
            lines.append(f"- 已收集信息: {ci[:200]}")
        except Exception:
            pass
    return "\n".join(lines) if lines else "（无）"


def _compose_fallback_conclusion(discovery_text, evidence) -> str:
    parts = ["未能精确定位根因，基于日志信号给出初步判断：", ""]
    if discovery_text:
        parts.append(discovery_text)
    if evidence:
        parts.append("关键日志行:")
        for e in evidence[-10:]:
            parts.append(f"  L{e['line']}: {e['summary'][:140]}")
    return "\n".join(parts)


# ════════════════════════════════════════════════════════════
# 健壮 JSON 命令解析（复用自 sub_agent 的思路）
# ════════════════════════════════════════════════════════════

def _parse_review_command(raw: str):
    """从编排 LLM 输出提取一条 investigate/conclude 命令。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"```(?:json)?\s*", "", text)
    # 提取最靠后且形状正确的 JSON 对象
    best = None
    for obj in _iter_json_objects(text):
        if not isinstance(obj, dict):
            continue
        intent = obj.get("intent")
        if intent == "investigate" and isinstance(obj.get("directive"), dict):
            best = obj
        elif intent == "conclude" and obj.get("conclusion"):
            best = obj
    return best


def _iter_json_objects(raw: str):
    text = re.sub(r"```(?:json)?\s*", "", raw)
    start = 0
    while True:
        start = text.find('{', start)
        if start < 0:
            break
        depth = 0
        in_str = False
        escape = False
        j = start
        while j < len(text):
            c = text[j]
            if escape:
                escape = False; j += 1; continue
            if c == '\\':
                escape = True; j += 1; continue
            if c == '"':
                in_str = not in_str; j += 1; continue
            if in_str:
                j += 1; continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        yield json.loads(text[start:j + 1])
                    except json.JSONDecodeError:
                        pass
                    start = j + 1
                    break
            j += 1
        else:
            break
