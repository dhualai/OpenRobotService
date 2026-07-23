"""日志子 Agent（LogSubAgent）— 独立的多轮推理能力单元

可以被多个入口调用：
  - diagnose() 出诊断报告时自动激活
  - discuss() @AI 讨论时单独提问日志问题
  - 直接 API: POST /api/ai/task/log/analyze

核心循环（最多 8 轮）:
  1. 先从知识库（docs/）查匹配的排查路径和日志含义
  2. LLM 根据知识库指引生成 LogQuery → LogIndex.query() 执行
  3. LLM 阅读结果 → 对比知识库的故障场景 → 判断是否需要继续
  4. 不够 → 调整参数 → 下一轮
  5. 所有 AI 查询无结果 → 兜底

双信息源:
  - 知识库 (docs/): 日志格式说明 + 故障排查树 + 常见日志含义
  - 日志索引 (LogIndex): 451MB 算法日志的毫秒级查询
"""

import json, re, os, time as _time
from pathlib import Path
from typing import Optional, Dict, List

from ai.config import get_ai_config
from ai.core import get_llm_client
from ai.agents.AiTaskPlatform.log_analyzer.indexer import (
    LogIndex, LogQuery, extract_fields, fields_summary,
)

# ── 知识库加载 ──────────────────────────────────────────────

_DOCS_DIR = Path(__file__).parent / "docs"


def _load_log_docs() -> str:
    """加载日志知识库的核心内容，压缩为 ≤4000 字注入 LLM Prompt。

    只取对 LLM 推理最有用的部分：架构概览 + 故障场景 + 关键日志含义。
    完整的速查手册和详细服务文档不注入（太多 token），只取精华。
    """
    if not _DOCS_DIR.is_dir():
        return "（日志知识库不可用）"

    parts = []

    # 1. 架构总览（前 100 行核心信息）
    arch = _DOCS_DIR / "00-架构总览.md"
    if arch.exists():
        text = arch.read_text(encoding="utf-8")
        # 取系统定位 + Actor 拓扑（前约 100 行）
        lines = text.split("\n")
        parts.append("## 系统架构")
        parts.extend(lines[:100])
        parts.append("")

    # 2. 常见故障场景（全量——这是最重要的指引）
    fault = _DOCS_DIR / "07-常见故障场景.md"
    if fault.exists():
        text = fault.read_text(encoding="utf-8")
        parts.append("## 常见故障场景排查路径")
        parts.append(text[:3000])
        parts.append("")

    # 3. 日志速查手册（截取前 80 行——含最常用的 A-D 类日志）
    quick = _DOCS_DIR / "06-日志速查手册.md"
    if quick.exists():
        text = quick.read_text(encoding="utf-8")
        parts.append("## 关键日志含义速查（节选）")
        parts.append(text[:2000])
        parts.append("")

    result = "\n".join(parts)
    # 压缩到 4000 字
    if len(result) > 4000:
        result = result[:4000] + "\n\n(知识库已截断，更多内容参见完整文档)"
    return result


# ── System Prompt ───────────────────────────────────────────

def _make_system_prompt() -> str:
    docs = _load_log_docs()
    return f"""你是 AGV 调度算法日志分析专家。你有两份信息源：

## 信息源 1: 知识库（日志格式和故障排查指引）
{docs}

## 信息源 2: 日志查询工具
可以通过 JSON 命令从实际算法日志中查询数据。

### 查询命令格式
{{"action": "query", "query": {{"time_start": "2026-07-21 11:01:40", "time_end": "2026-07-21 11:02:00", "robot_filter": "E-XQE-218", "task_filter": "", "path_filter": "", "error_only": false, "context_lines": 3, "max_results": 30}}, "analysis": "当前发现", "next_step": "下一轮要查什么"}}

### 查询参数说明
- time_start/end: 时间窗口（留空=不限）。格式: "YYYY-MM-DD HH:MM:SS"
- robot_filter: 车辆ID（如 "E-XQE-218"）。留空=不限
- task_filter: 任务ID。留空=不限
- path_filter: 路径ID。留空=不限
- error_only: true=只看ERROR/WARN行，false=看全部
- context_lines: 上下文行数（默认3）
- max_results: 最多匹配行数（默认30）

## 推理策略（先看知识库，再查日志）

1. **第一轮**：先看知识库的故障场景排查路径，找到匹配的症状 → 按知识库的 Step 指导写查询
2. **查日志含义**：看到日志行的关键词，先回忆知识库里的含义（比如 "PATH_PLANNING_SINGLE_AGENT_NO_SOLUTION" 意味着什么）
3. **追踪路径**：如果有车辆ID，追查它的路径分配历史和任务ID
4. **找根因**：不只是报"出现了什么错误"，而要按知识库的排查路径一步步往下挖
5. 如果匹配太多(>50行): 加 robot_filter 或缩小时间窗口
6. 如果匹配太少或无结果: 扩大时间窗口或去掉过滤条件

## 结束条件

当找到关键线索时:
{{"action": "conclude", "conclusion": "一句话总结日志发现，引用知识库的排查路径作为推理依据", "evidence_lines": ["L123: 具体发现", "L456: 具体发现"]}}

当所有尝试都无结果时:
{{"action": "fallback", "reason": "为什么没找到线索"}}

## 铁律
- 每轮只输出一个 JSON 对象，JSON 之前之后不输出任何文字
- 先对知识库：每条日志行报了什么错在知识库里查含义
- 每次 query 选最多 3 个过滤参数组合使用
- 如果已经 query 了 3 轮还没线索，必须 conclude 或 fallback"""


# ── 日志分析结论模型 ─────────────────────────────────────────

class LogAnalysisResult:
    """日志子 Agent 输出"""
    def __init__(self):
        self.conclusion = ""          # 一句话结论
        self.evidence = []            # [{"line": 390, "ts": "11:01:44", "summary": "..."}]
        self.queries_made = 0         # 执行了几轮查询
        self.fallback_used = False    # 是否兜底了

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
    """日志分析子 Agent：知识库指导 + 多轮 LLM 推理 → LogIndex 执行 → 返回结论

    Usage:
        agent = LogSubAgent(log_path)
        result = await agent.analyze(
            task_context={{...}},
            user_question="看看 E-XQE-217 为什么一直在等待",
        )
    """

    MAX_ROUNDS = 8
    SOFT_LIMIT = 5

    def __init__(self, log_path: str):
        self.log_path = log_path
        self._index: Optional[LogIndex] = None
        self._llm = None

    async def _ensure_clients(self):
        if self._llm is None:
            self._llm = await get_llm_client()
        if self._index is None:
            self._index = LogIndex(self.log_path).build()

    async def analyze(
        self,
        task_context: Dict,
        user_question: str = "",
    ) -> LogAnalysisResult:
        """主入口：多轮推理 → 返回分析结论。"""
        await self._ensure_clients()
        result = LogAnalysisResult()

        context_text = _build_context(task_context, user_question)
        system_prompt = _make_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_text},
        ]
        query_history = []

        for round_num in range(1, self.MAX_ROUNDS + 1):
            response = await self._llm.chat(
                messages=messages, max_tokens=500, temperature=0.2,
            )

            cmd = _parse_llm_command(response)
            if cmd is None:
                result.conclusion = "日志子Agent输出解析失败"
                result.fallback_used = True
                break

            action = cmd.get("action", "conclude")

            if action == "query" and round_num <= self.SOFT_LIMIT + 2:
                q = cmd.get("query", {})
                q_hash = json.dumps(q, sort_keys=True)
                if q_hash in query_history:
                    messages.append({"role": "user", "content": "重复的查询参数，请换一个查询方向"})
                    continue
                query_history.append(q_hash)

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

                analysis = cmd.get("analysis", "")
                feedback = f"查询第{round_num}轮结果：\n{query_result[:1500]}"
                if round_num >= self.SOFT_LIMIT:
                    feedback += f"\n\n(已查{round_num}轮，接近上限，请尽快conclude或fallback)"

                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": feedback})

                # 保存线索
                if "matched" in query_result and "matched 0 lines" not in query_result:
                    for line_match in re.findall(r"\* L(\d+)\| (.*)", query_result):
                        ln, sm = line_match
                        result.evidence.append({"line": int(ln), "summary": sm[:150]})
            else:
                if action == "conclude":
                    result.conclusion = cmd.get("conclusion", "")
                elif action == "fallback":
                    result.conclusion = cmd.get("reason", "无结论")
                    result.fallback_used = True
                break

        # 兜底
        if not result.conclusion and not result.fallback_used:
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
    parts = ["## 工单信息"]
    if task.get("title"): parts.append(f"标题: {task['title']}")
    if task.get("problem_summary"): parts.append(f"问题概述: {task['problem_summary']}")
    if task.get("description"): parts.append(f"描述: {task['description'][:200]}")
    if task.get("hypotheses"): parts.append(f"推测原因: {' / '.join(task['hypotheses'])}")
    if task.get("ruled_out"): parts.append(f"已排除: {' / '.join(task['ruled_out'])}")
    if task.get("robot_type"): parts.append(f"车型: {task['robot_type']}")
    if task.get("fault_code"): parts.append(f"故障码: {task['fault_code']}")
    if task.get("collected_info"):
        ci = task["collected_info"]
        if isinstance(ci, dict):
            for k, v in ci.items(): parts.append(f"{k}: {v}")
    if question: parts.append(f"\n## 用户问题\n{question}")
    parts.append("\n---")
    parts.append("请先查看知识库中的故障场景排查路径，找到匹配的症状后按 Step 指导开始查询。")
    return "\n".join(parts)


def _parse_llm_command(raw: str) -> Optional[Dict]:
    m = re.search(r'\{[^{}]*"action"[^{}]*\}', raw, re.DOTALL)
    if m:
        try: return json.loads(m.group())
        except json.JSONDecodeError: pass
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try: return json.loads(m.group())
        except json.JSONDecodeError: pass
    return None
