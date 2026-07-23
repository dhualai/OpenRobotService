"""日志子 Agent（LogSubAgent）— 独立的多轮推理能力单元

可以被多个入口调用：
  - diagnose() 出诊断报告时自动激活
  - discuss() @AI 讨论时单独提问日志问题
  - 直接 API: POST /api/ai/task/log/analyze

核心循环（最多 8 轮）:
  1. LLM 生成 LogQuery → LogIndex.query() 执行
  2. LLM 阅读结果 → 判断是否需要继续
  3. 不够 → 调整参数（扩大时间/换车辆/查路径/查错误）→ 下一轮
  4. 所有 AI 查询无结果 → 兜底: parse_log_for_diagnosis(error_only=True, max_results=50)

每轮 LLM 调用使用独立的 system prompt，输出 JSON 控制流:
  {"action": "query"|"conclude"|"fallback",
   "query": {...LogQuery params...},
   "analysis": "当前发现...",
   "next_step": "下一轮要查什么"}

设计原则:
  - 不做排查树诊断（提单 Agent 已完成），只做日志分析
  - 每轮把之前所有查询结果累积，不丢失上下文
  - 输出 ≤2000 字的分析结论，供上层 Agent 消费
"""

import json, re, os, time as _time
from typing import Optional, Dict, List, Callable

from ai.config import get_ai_config
from ai.core import get_llm_client
from ai.agents.AiTaskPlatform.log_analyzer.indexer import (
    LogIndex, LogQuery, extract_fields, fields_summary,
)

# ── Prompt ──────────────────────────────────────────────────

LOG_SUB_AGENT_SYSTEM = """你是 AGV 调度算法日志分析专家。你的任务是根据工单上下文，通过多轮查询从算法日志中定位问题。

## 工具

你可以通过 JSON 命令调用日志查询:

{"action": "query", "query": {"time_start": "2026-07-21 11:01:40", "time_end": "2026-07-21 11:02:00", "robot_filter": "E-XQE-218", "task_filter": "", "path_filter": "", "error_only": false, "context_lines": 3, "max_results": 30}, "analysis": "当前发现", "next_step": "下一轮要查什么"}

### 查询参数说明
- time_start/end: 时间窗口（留空=不限）。格式: "YYYY-MM-DD HH:MM:SS"
- robot_filter: 车辆ID（如 "E-XQE-218"）。留空=不限
- task_filter: 任务ID（如 "8462010583900"）。留空=不限
- path_filter: 路径ID。留空=不限
- error_only: true=只看ERROR/WARN行，false=看全部
- context_lines: 上下文行数（默认3）
- max_results: 最多匹配行数（默认30）

### 查询策略（重要）
1. 第一轮: 用工单上下文中推测的故障时间、车型、故障码来缩小范围
2. 如果有时间戳: 先用时间窗口 + 车型过滤
3. 如果有任务号: 直接查任务号
4. 如果时间不明确: 先用 error_only=true 看全量ERROR分布 → 锁定时间区间
5. 如果匹配太多(>50行): 加 robot_filter 或缩小时间窗口
6. 如果匹配太少或无结果: 扩大时间窗口或去掉过滤条件

## 结束条件

当找到关键线索时，输出:
{"action": "conclude", "conclusion": "一句话总结日志发现", "evidence_lines": ["L123: 具体发现", "L456: 具体发现"]}

当所有尝试都无结果时，输出:
{"action": "fallback", "reason": "为什么没找到线索"}

## 铁律
- 每轮只输出一个 JSON 对象，JSON 之前之后不输出任何文字
- 每次 query 只能选最多 3 个过滤参数组合使用（防止交集过滤过度）
- 如果已经 query 了 3 轮还没线索，必须 conclude 或 fallback"""


# ── 日志分析结论模型 ─────────────────────────────────────────

class LogAnalysisResult:
    """日志子 Agent 输出"""
    def __init__(self):
        self.conclusion = ""          # 一句话结论
        self.evidence = []            # [{"line": 390, "ts": "11:01:44", "summary": "..."}]
        self.queries_made = 0         # 执行了几轮查询
        self.fallback_used = False    # 是否兜底了
        self.raw_log = ""             # 全文（供上层 Agent 注入 Prompt）

    def to_dict(self):
        return {
            "conclusion": self.conclusion,
            "evidence": self.evidence[:10],
            "queries": self.queries_made,
            "fallback": self.fallback_used,
        }

    def to_prompt_text(self) -> str:
        """生成供诊断 Prompt 注入的文本"""
        parts = []
        if self.conclusion:
            parts.append(f"日志分析结论: {self.conclusion}")
        if self.evidence:
            parts.append("关键日志行:")
            for e in self.evidence[:8]:
                parts.append(f"  L{e['line']} [{e.get('ts','?')}] {e['summary'][:120]}")
        if self.queries_made:
            parts.append(f"共查询 {self.queries_made} 轮")
        return "\n".join(parts)


# ── 子 Agent 主循环 ─────────────────────────────────────────

class LogSubAgent:
    """日志分析子 Agent：多轮 LLM 推理 → LogIndex 执行 → 返回结论

    Usage:
        agent = LogSubAgent(log_path)
        result = await agent.analyze(
            task_context={
                "title": "避让后车不动",
                "problem_summary": "路径起点=终点",
                "hypotheses": ["路径规划死锁"],
                "robot_type": "潜伏车",
                "fault_code": "PATH_PLANNING_SINGLE_AGENT_NO_SOLUTION",
            },
            user_question="看看这个算法日志有没有路径规划的问题",
        )
    """

    MAX_ROUNDS = 8   # 硬上限
    SOFT_LIMIT = 5   # 超过此轮数后倾向 conclude

    def __init__(self, log_path: str):
        self.log_path = log_path
        self._index: Optional[LogIndex] = None
        self._llm = None

    async def _ensure_clients(self):
        if self._llm is None:
            self._llm = await get_llm_client()
        if self._index is None:
            self._index = LogIndex(self.log_path).build()

    # ── 对外入口 ──────────────────────────────────────────────

    async def analyze(
        self,
        task_context: Dict,
        user_question: str = "",
    ) -> LogAnalysisResult:
        """主入口：多轮推理 → 返回分析结论。

        Args:
            task_context: 工单上下文（title/description/problem_summary/hypotheses/
                          robot_type/fault_code/collected_info）
            user_question: 用户额外问题（discuss @AI 场景，可选）

        Returns:
            LogAnalysisResult: 包含 conclusion + evidence + 可注入 Prompt 的文本
        """
        await self._ensure_clients()
        result = LogAnalysisResult()

        # 构建初始上下文
        context_text = _build_context(task_context, user_question)
        messages = [
            {"role": "system", "content": LOG_SUB_AGENT_SYSTEM},
            {"role": "user", "content": context_text},
        ]
        query_history = []  # 已经用过的查询参数（用于避免重复）

        for round_num in range(1, self.MAX_ROUNDS + 1):
            # 1. LLM 决定下一步
            response = await self._llm.chat(
                messages=messages,
                max_tokens=500,
                temperature=0.2,
            )

            # 2. 解析 LLM 输出
            cmd = _parse_llm_command(response)
            if cmd is None:
                result.conclusion = "日志子Agent输出解析失败"
                result.fallback_used = True
                break

            action = cmd.get("action", "conclude")

            # 3. 执行查询 or 结束
            if action == "query" and round_num <= self.SOFT_LIMIT + 2:
                q = cmd.get("query", {})
                # 防重复查询
                q_hash = json.dumps(q, sort_keys=True)
                if q_hash in query_history:
                    # 跳过重复查询，让 LLM 换方向
                    messages.append({"role": "user", "content": "重复的查询参数，请换一个查询方向"})
                    continue
                query_history.append(q_hash)

                # 执行查询
                log_query = LogQuery(
                    time_start=q.get("time_start") or None,
                    time_end=q.get("time_end") or None,
                    robot_filter=q.get("robot_filter") or None,
                    task_filter=q.get("task_filter") or None,
                    path_filter=q.get("path_filter") or None,
                    error_only=q.get("error_only", False),
                    context_before=int(q.get("context_lines", 3)),
                    context_after=int(q.get("context_lines", 3)),
                    max_results=int(q.get("max_results", 30)),
                )
                query_result = self._index.query(log_query)
                result.queries_made += 1

                # 把查询结果注入对话
                analysis = cmd.get("analysis", "")
                next_step = cmd.get("next_step", "")
                feedback = f"查询第{round_num}轮结果：\n{query_result[:1500]}"

                if round_num >= self.SOFT_LIMIT:
                    feedback += "\n\n(已查{0}轮，接近上限，请尽快conclude或fallback)".format(round_num)

                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": feedback})

                # 保存线索
                if "matched" in query_result and "matched 0 lines" not in query_result:
                    for line_match in re.findall(r"\* L(\d+)\| (.*)", query_result):
                        ln, sm = line_match
                        result.evidence.append({"line": int(ln), "summary": sm[:150]})

            else:
                # action == "conclude" or "fallback" or 超轮数
                if action == "conclude":
                    result.conclusion = cmd.get("conclusion", "")
                elif action == "fallback":
                    result.conclusion = cmd.get("reason", "无结论")
                    result.fallback_used = True
                break

        # 如果循环结束但没有 conclude/fallback → 兜底
        if not result.conclusion and not result.fallback_used:
            # 兜底: error_only 直接查
            fallback_query = LogQuery(error_only=True, max_results=50)
            fallback_text = self._index.query(fallback_query)
            if "matched" in fallback_text and "matched 0 lines" not in fallback_text:
                result.conclusion = "未能精确定位，但日志中存在以下异常（请人工确认）"
                for line_match in re.findall(r"\* L(\d+)\| (.*)", fallback_text)[:10]:
                    ln, sm = line_match
                    result.evidence.append({"line": int(ln), "summary": sm[:150]})
                result.fallback_used = True
            result.queries_made += 1

        return result


# ── 辅助函数 ────────────────────────────────────────────────

def _build_context(task: Dict, question: str) -> str:
    """构建初始 LLM 上下文"""
    parts = ["## 工单信息"]
    if task.get("title"):
        parts.append(f"标题: {task['title']}")
    if task.get("problem_summary"):
        parts.append(f"问题概述: {task['problem_summary']}")
    if task.get("description"):
        parts.append(f"描述: {task['description'][:200]}")
    if task.get("hypotheses"):
        parts.append(f"推测原因: {' / '.join(task['hypotheses'])}")
    if task.get("ruled_out"):
        parts.append(f"已排除: {' / '.join(task['ruled_out'])}")
    if task.get("robot_type"):
        parts.append(f"车型: {task['robot_type']}")
    if task.get("fault_code"):
        parts.append(f"故障码: {task['fault_code']}")
    if task.get("collected_info"):
        ci = task["collected_info"]
        if isinstance(ci, dict):
            for k, v in ci.items():
                parts.append(f"{k}: {v}")

    if question:
        parts.append(f"\n## 用户问题\n{question}")

    parts.append("\n---")
    parts.append("请开始第一轮查询。优先用推测的故障时间和车型来缩小范围。")
    return "\n".join(parts)


def _parse_llm_command(raw: str) -> Optional[Dict]:
    """从 LLM 输出中提取 JSON 命令"""
    m = re.search(r'\{[^{}]*"action"[^{}]*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # 兜底: 匹配更大的 JSON
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None
