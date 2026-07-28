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
from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.log_analyzer.indexer import (
    LogIndex, LogQuery, extract_fields, fields_summary,
)

logger = get_logger("TASK_AGENT")

# ── 日志说明手册加载 ──────────────────────────────────────────────
# 优先从 DOCS_PATH（.env）读取，确保部署时本地文件不丢失；
# 本地开发时 DOCS_PATH 未设置或无效则回退到代码目录下的 log_manual/。
_ai_config = get_ai_config()
if _ai_config.docs_path:
    _candidate = Path(_ai_config.docs_path) / "task_agent"
    if _candidate.is_dir():
        _DOCS_DIR = _candidate
    else:
        _DOCS_DIR = Path(__file__).parent / "log_manual"
else:
    _DOCS_DIR = Path(__file__).parent / "log_manual"


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

def _make_system_prompt(log_date: str = "") -> str:
    docs = _load_log_docs()
    return f"""只输出一行JSON。第1轮:搜"一致性超过update阈值"(输入WARNING行,核心根因), error_only=true, 查16:50~17:05。
找到后第2轮:用robot_filter=XNA-169+task_filter=1098000搜上下文。第3轮必须conclude。

query命令: {{"action":"query","query":{{"time_start":"2026-07-27 16:50","time_end":"2026-07-27 17:05","robot_filter":"","task_filter":"","error_only":true,"max_results":50}}}}
conclude: {{"action":"conclude","conclusion":"根因(知识库场景13)+解决方案(wait_time_check_interval=40,wait_time_update_gap=40)","evidence_lines":["L123: 一致性超过update阈值42.2s","L456: MAPF-T:77.956 WAIT-T:5.0"]}}

日期={log_date}。最多3轮。
{docs}"""


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
                # summary 已包含时间戳和字段信息，直接展示
                parts.append(f"  L{e['line']}: {e['summary'][:150]}")
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
        t0 = _time.perf_counter()
        logger.info(f"LogSubAgent start: path={Path(self.log_path).name}, question={user_question[:60]}")
        await self._ensure_clients()
        result = LogAnalysisResult()

        # 获取日志文件中第一行和最后一行的日期
        log_date = "unknown"
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
            m = re.search(r"(\d{4}-\d{2}-\d{2})", first)
            if m: log_date = m.group(1)

        context_text = _build_context(task_context, user_question)
        context_text += f"\n\n**日志日期**: {log_date}"

        system_prompt = _make_system_prompt(log_date)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_text},
        ]
        query_history = []

        for round_num in range(1, self.MAX_ROUNDS + 1):
            response = await self._llm.chat(
                messages=messages, max_tokens=400, temperature=0.0,
            )

            cmd = _parse_llm_command(response)
            if cmd is None:
                logger.warning(f"LogSubAgent R{round_num} parse failed, raw[:200]={response[:200]}")
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

                logger.info(f"LogSubAgent R{round_num} query: {json.dumps(q, ensure_ascii=False)}")
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
                matched_lines = int(re.search(r"matched (\d+)", query_result).group(1)) if re.search(r"matched (\d+)", query_result) else 0
                logger.info(f"LogSubAgent R{round_num}: {cmd.get('purpose','?')[:60]} → {matched_lines} lines")

                analysis = cmd.get("analysis", "")
                feedback = f"查询第{round_num}轮结果：\n{query_result[:1500]}"
                if round_num >= self.SOFT_LIMIT:
                    feedback += f"\n\n(已查{round_num}轮，接近上限，请尽快conclude或fallback)"

                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": feedback})

                # 保存线索（暂存，conclude 时按 LLM 引用的行号过滤）
                if "matched" in query_result and "matched 0 lines" not in query_result:
                    for line_match in re.findall(r"\* L(\d+)\| (.+)", query_result):
                        ln, sm = line_match
                        summary = sm.strip()
                        if not summary or summary.count("|") < 1:
                            continue
                        result.evidence.append({"line": int(ln), "summary": sm[:150], "round": round_num})
            else:
                if action == "conclude":
                    result.conclusion = cmd.get("conclusion", "")
                    # ── 按 LLM 引用的行号过滤证据 ──
                    cited_lines = set()
                    for ref in cmd.get("evidence_lines", []):
                        m = re.search(r"L(\d+)", str(ref))
                        if m:
                            cited_lines.add(int(m.group(1)))
                    if cited_lines:
                        result.evidence = [e for e in result.evidence if e["line"] in cited_lines]
                    else:
                        # 没引用具体行号 → 只保留含关键信号的证据
                        _SIGNAL_KW = ("一致性", "MAPF-T", "ABORTED", "WARNING", "等待时间", "last_node")
                        result.evidence = [
                            e for e in result.evidence
                            if any(kw in e["summary"] for kw in _SIGNAL_KW)
                        ]
                    logger.info(f"LogSubAgent conclude at R{round_num}: {result.conclusion[:80]} (cited={len(cited_lines)}, evidence={len(result.evidence)})")
                elif action == "fallback":
                    result.conclusion = cmd.get("reason", "无结论")
                    result.fallback_used = True
                    logger.info(f"LogSubAgent fallback at R{round_num}: {result.conclusion[:80]}")
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

        logger.info(f"LogSubAgent done: rounds={result.queries_made}, evidence={len(result.evidence)}, fallback={result.fallback_used}, elapsed={(_time.perf_counter()-t0)*1000:.0f}ms")
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
    # 找第一个 { → 手动数括号取完整 JSON
    start = raw.find('{')
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        c = raw[i]
        if escape:
            escape = False; continue
        if c == '\\':
            escape = True; continue
        if c == '"':
            in_str = not in_str; continue
        if in_str:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                block = raw[start:i+1]
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    break
    return None
