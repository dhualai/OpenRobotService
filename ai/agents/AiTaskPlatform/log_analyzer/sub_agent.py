"""日志子 Agent（LogSubAgent）— 知识库指导 + 客观事实锚定 + 多轮 LLM 推理

可被多个入口调用：
  - diagnose() 出诊断报告时自动激活
  - discuss() @AI 讨论时单独提问日志问题
  - 直接 API: POST /api/ai/task/log/analyze

核心循环（最多 MAX_ROUNDS 轮）:
  1. 先扫描日志索引 → 提取「客观事实」（真实时间范围/车型/任务ID/高频错误/错误密集时段）
  2. 把这些事实注入 Prompt，防止 LLM 凭空捏造日期、车型、任务ID
  3. LLM 根据知识库 + 事实生成 LogQuery → LogIndex.query() 执行
  4. 查询参数在落地前先做**可信校验**（车型/任务必须命中索引，时间窗夹紧到日志真实范围）
  5. LLM 阅读结果 → 对比知识库的故障场景 → 决定继续 / conclude / fallback
  6. 结尾兜底：LLM 输出解析失败时也不丢结论，保证一定有返回值

2026-08-12 v3.5 修复（真实故障：错误日期+伪造车型+超宽查询+结尾解析失败丢结论）：
  - grounding: 注入日志客观事实，杜绝幻构日期/车型/任务ID
  - 泛化 system prompt：去掉写死的"一致性超阈值/XNA-169/1098000"场景引导
  - 查询参数校验与夹紧：车型/任务须命中索引，时间窗夹到真实范围，拦截超宽查询
  - 健壮 JSON 解析：剥散文/代码块，提取最完整且形状正确的命令，conclude 不丢
  - token 收敛：每轮反馈限长，超宽查询自动加"请先缩小范围"提示
"""

import json, re, os, time as _time
from pathlib import Path
from typing import Optional, Dict, List

from ai.config import get_ai_config
from ai.core import get_llm_client
from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.log_analyzer.indexer import (
    LogIndex, LogQuery,
)

logger = get_logger("TASK_AGENT")

# ── 日志说明手册加载 ──────────────────────────────────────────────
_ai_config = get_ai_config()
if _ai_config.docs_path:
    _candidate = Path(_ai_config.docs_path) / "task_agent"
    _DOCS_DIR = _candidate if _candidate.is_dir() else Path(__file__).parent / "log_manual"
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


# ── 客观事实注入 ─────────────────────────────────────────────

def _facts_to_text(facts: Dict) -> str:
    """把 LogIndex.discover_facts() 的骨架转成给 LLM 看的客观事实文本。"""
    if not facts:
        return "（无法提取日志事实）"

    lines = ["## 日志客观事实（只能在这些真实值中选择过滤条件，禁止虚构）"]
    lines.append(f"- 日志总行数: {facts.get('lines', '?')}")
    lines.append(f"- 错误/警告行: {facts.get('errors', '?')}")

    ts = facts.get("time_start")
    te = facts.get("time_end")
    if ts and te:
        lines.append(f"- 日志时间范围: {ts} ~ {te}（查询 time_start/time_end 必须落在此区间内）")

    robots = facts.get("top_robots") or []
    if robots:
        lines.append(f"- 出现次数最多的车型(top{len(robots)}): {', '.join(robots)}")

    tasks = facts.get("top_tasks") or []
    if tasks:
        lines.append(f"- 出现次数最多的任务(top{len(tasks)}): {', '.join(tasks)}")

    errs = facts.get("top_errors") or []
    if errs:
        lines.append(f"- 高频 error_code: {', '.join(errs)}")

    hours = facts.get("error_hours") or []
    if hours:
        shown = ", ".join(f"{h}时({c}条)" for h, c in hours[:5])
        lines.append(f"- 错误最密集的时段: {shown}")

    return "\n".join(lines)


# ── System Prompt ───────────────────────────────────────────

def _make_system_prompt() -> str:
    """通用、不绑定任何具体场景的 system prompt。"""
    return """你是资深AGV调度系统日志分析专家。你只能输出【一行JSON】，禁止输出JSON以外的任何散文、解释、Markdown。

可用命令（每次输出恰好一个）:

1) 查询:
{"action":"query","analysis":"这一步想验证什么假设","query":{"time_start":"YYYY-MM-DD HH:MM","time_end":"YYYY-MM-DD HH:MM","robot_filter":"车型ID或空串","task_filter":"任务ID或空串","error_only":true,"max_results":50}}

2) 下结论（证据足够时用）:
{"action":"conclude","conclusion":"一句话根因(引用知识库故障场景编号)+证据链+解决方案","evidence_lines":["L数字: 关键内容","L数字: 关键内容"]}

3) 放弃（确实查不到）:
{"action":"fallback","reason":"为什么确定查不出有效线索"}

硬性规则:
- time_start/time_end、robot_filter、task_filter 只能从「日志客观事实」里选真实存在的值；不知道就填空串""。绝不虚构日期或ID。
- error_only=true 只回错误/警告行；要上下文时回 false 并配合窄时间窗。
- max_results 建议 30~100；时间窗越窄信息越准。
- 每轮只输出一个JSON命令，输出前不要有任何思考文字。"""


# ── 日志分析结论模型 ─────────────────────────────────────────


# ── 日志分析结论模型 ─────────────────────────────────────────

class LogAnalysisResult:
    """日志子 Agent 输出"""
    def __init__(self):
        self.conclusion = ""          # 一句话结论
        self.evidence = []            # [{"line": 390, "ts": "11:01:44", "summary": "..."}]
        self.queries_made = 0         # 执行了几轮查询
        self.fallback_used = False    # 是否兜底了
        self.parse_failures = 0       # LLM 输出解析失败次数

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
    """日志分析子 Agent：知识库指导 + 客观事实锚定 + 多轮 LLM 推理 → LogIndex 执行

    Usage:
        agent = LogSubAgent(log_path)
        result = await agent.analyze(
            task_context={{...}},
            user_question="看看某车型为什么一直在等待",
        )
    """

    MAX_ROUNDS = 6
    SOFT_LIMIT = 4

    def __init__(self, log_path: str):
        self.log_path = log_path
        self._index: Optional[LogIndex] = None
        self._facts: Dict = {}
        self._llm = None

    async def _ensure_clients(self):
        if self._llm is None:
            self._llm = await get_llm_client()
        if self._index is None:
            self._index = LogIndex(self.log_path).build()
            self._facts = self._index.discover_facts(top_n=8)

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

        log_date = self._facts.get("date", "unknown")

        context_text = _build_context(task_context, user_question)
        context_text += f"\n\n{_facts_to_text(self._facts)}"
        context_text += f"\n\n**日志日期**: {log_date}"

        system_prompt = _make_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_text},
        ]
        query_history: List[Dict] = []

        for round_num in range(1, self.MAX_ROUNDS + 1):
            response = await self._llm.chat(
                messages=messages, max_tokens=300, temperature=0.0,
            )

            cmd = _parse_llm_command(response)
            if cmd is None:
                result.parse_failures += 1
                logger.warning(f"LogSubAgent R{round_num} parse failed, raw[:200]={response[:200]}")
                if round_num >= self.SOFT_LIMIT:
                    # 轮数已深 → 抢救任何 conclude 结论，避免丢答案
                    salvage = _salvage_conclusion(response)
                    if salvage:
                        result.conclusion = salvage
                        logger.info(f"LogSubAgent R{round_num}: 从输出中抢救到结论")
                        break
                    result.conclusion = "日志子Agent输出解析失败"
                    result.fallback_used = True
                    break
                # 轮数还浅 → 提示重新严格输出一行 JSON 后重试
                messages.append({"role": "user",
                                 "content": "刚才的输出不是一行合法JSON命令，请重新输出一行JSON。"
                                             "查数据用 {\"action\":\"query\",...}，证据足够用 {\"action\":\"conclude\",...}。"})
                continue

            action = cmd.get("action", "conclude")

            if action == "query" and round_num <= self.SOFT_LIMIT:
                raw_q = cmd.get("query", {})
                q = _validate_query(raw_q, self._index, self._facts)

                if _is_near_duplicate(q, query_history):
                    messages.append({"role": "user",
                                     "content": "这次查询和之前一轮几乎一样（时间窗/过滤条件相同）。请换车型、换任务或改窄时间窗后重新查询。"})
                    continue
                query_history.append(q)

                logger.info(f"LogSubAgent R{round_num} query(norm): {json.dumps(q, ensure_ascii=False)}")
                log_query = LogQuery(
                    time_start=q.get("time_start") or None,
                    time_end=q.get("time_end") or None,
                    robot_filter=q.get("robot_filter") or None,
                    task_filter=q.get("task_filter") or None,
                    path_filter=q.get("path_filter") or None,
                    error_only=q.get("error_only", True),
                    context_before=int(q.get("context_lines", 2)),
                    context_after=int(q.get("context_lines", 2)),
                    max_results=int(q.get("max_results", 50)),
                )
                query_result = self._index.query(log_query)
                result.queries_made += 1

                matched = _matched_lines(query_result)
                logger.info(f"LogSubAgent R{round_num}: {cmd.get('analysis','?')[:60]} → {matched} lines")

                _collect_evidence(result, query_result, round_num)

                feedback = f"查询第{round_num}轮结果(命中{matched}行):\n{query_result[:_MAX_FEEDBACK_CHARS]}"
                if _feedback_is_too_broad(matched):
                    feedback += (f"\n\n⚠ 本轮命中 {matched} 行过多，说明过滤条件太宽。"
                                 "请从「日志客观事实」里挑一个真实车型/任务，或把时间窗缩窄到错误密集时段再查询；"
                                 "找不到合适过滤条件就 conclude 或 fallback。")
                if round_num >= self.SOFT_LIMIT:
                    feedback += f"\n\n(已查{round_num}轮，接近上限，请尽快conclude或fallback)"

                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": feedback})

            else:
                if action == "conclude":
                    result.conclusion = cmd.get("conclusion", "") or _salvage_conclusion(response)
                    _filter_evidence_by_citation(result, cmd.get("evidence_lines", []))
                    logger.info(f"LogSubAgent conclude at R{round_num}: {result.conclusion[:80]} (evidence={len(result.evidence)})")
                elif action == "fallback":
                    result.conclusion = cmd.get("reason", "无结论")
                    result.fallback_used = True
                    logger.info(f"LogSubAgent fallback at R{round_num}: {result.conclusion[:80]}")
                break

        # 兜底：没拿到结论 → 用最保守的错误样本
        if not result.conclusion and not result.fallback_used:
            result.fallback_used = True
            fallback_query = LogQuery(time_start=None, time_end=None,
                                      error_only=True, max_results=50,
                                      context_before=2, context_after=2)
            fallback_text = self._index.query(fallback_query)
            if "matched" in fallback_text and "matched 0 lines" not in fallback_text:
                _collect_evidence(result, fallback_text, round_num=0)
                result.conclusion = "未能精确定位根因，但日志中存在以下异常（请人工确认）"
            else:
                result.conclusion = "日志中未发现可用错误/警告信号"
            logger.info(f"LogSubAgent fallback: evidence={len(result.evidence)}")

        logger.info(f"LogSubAgent done: rounds={result.queries_made}, evidence={len(result.evidence)}, "
                    f"fallback={result.fallback_used}, parse_fail={result.parse_failures}, "
                    f"elapsed={(_time.perf_counter()-t0)*1000:.0f}ms")
        return result

    # ── 确认者接口：执行单条 directive，返回结构化证据（方案 X：编排集中在 orchestrator）──

    async def analyze_directive(
        self,
        directive: Dict,
        evidence: Optional[List] = None,
        query_text: Optional[str] = None,
    ) -> Dict:
        """执行 orchestrator 下发的一条 directive，返回该次聚焦查询的证据。

        Args:
            directive: 结构化查询意图，形如
                {"time_start":"2026-08-11 11:00","time_end":"2026-08-11 11:05",
                 "robot_filter":"XNA-169","task_filter":"","error_only":true,
                 "keyword_filter":"last_node_index",
                 "context_lines":3,"max_results":50}
            evidence: 可传入主流程已收集的证据列表，discovery 结果会追加进去
            query_text: 可选的人类可读描述（用于日志）

        Returns:
            {"matched": int, "text": str, "sample": [summary...],
             "evidence": [{"line":..,"summary":..}...]}  # 追加了本轮证据
        """
        await self._ensure_clients()

        # 结构化 directive 直接走校验/夹紧，不绕 LLM 翻译（忠实执行）
        q = _validate_query(dict(directive), self._index, self._facts)
        log_query = LogQuery(
            time_start=q.get("time_start") or None,
            time_end=q.get("time_end") or None,
            robot_filter=q.get("robot_filter") or None,
            task_filter=q.get("task_filter") or None,
            path_filter=q.get("path_filter") or None,
            error_only=q.get("error_only", True),
            keyword=q.get("keyword_filter") or None,
            context_before=int(q.get("context_lines", 2)),
            context_after=int(q.get("context_lines", 2)),
            max_results=int(q.get("max_results", 50)),
        )
        query_result = self._index.query(log_query)
        matched = _matched_lines(query_result)

        logger.info(f"LogSubAgent directive{(' '+query_text[:50]) if query_text else ''} "
                    f"→ {matched} lines | {json.dumps(q, ensure_ascii=False)}")

        # 收集证据（只收「命中」行，避免 INFO context 行污染；有 * 标记的才是真正命中）
        result_evidence = evidence if evidence is not None else []
        _collect_evidence_into(result_evidence, query_result, only_hit=True)

        # 抽样行摘要（供编排 LLM 阅读）：优先命中行，其次 context
        sample = []
        for line_match in re.findall(r"\* L(\d+)\| (.*)", query_result):
            ln, sm = line_match
            summary = sm.strip()
            if summary:
                sample.append({"line": int(ln), "summary": summary[:180]})
            if len(sample) >= 30:
                break
        # 命中行不足时补充 context 行
        if len(sample) < 10:
            for line_match in re.findall(r"  L(\d+)\| (.*)", query_result):
                ln, sm = line_match
                summary = sm.strip()
                if summary:
                    sample.append({"line": int(ln), "summary": summary[:180]})
                if len(sample) >= 15:
                    break

        return {
            "matched": matched,
            "text": query_result[:_MAX_FEEDBACK_CHARS],
            "sample": sample,
            "evidence": result_evidence,
        }


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


# ── 查询参数的可信校验与夹紧 ─────────────────────────────────

_MAX_FEEDBACK_CHARS = 1500      # 每轮反馈给 LLM 的最大字符数
_BROAD_WINDOW_HINT_LINES = 20_000  # 命中超过此数量视为"太宽"，提示 LLM 缩窄


def _validate_query(q: Dict, idx: LogIndex, facts: Dict) -> Dict:
    """落地前校验/夹紧 LLM 给的查询参数，返回规范化后的 dict。

    防三种坑（2026-08-12 真实故障复现）：
      - 错误日期（日志是08-11/12，LLM 查 08-10）→ 时间窗夹紧到真实范围
      - 伪造车型（robot_filter="100" 查不到）→ 未命中索引则置空
      - 超宽查询（整日全错误行 14万行）→ 提示 LLM 缩窄
    """
    q = dict(q)

    # 1) 时间窗夹紧到日志真实范围
    ts, te = facts.get("time_start"), facts.get("time_end")
    t_start = (q.get("time_start") or "").strip()
    t_end = (q.get("time_end") or "").strip()
    if ts and te:
        if not t_start:
            t_start = ts[:16]
        elif t_start < ts[:16]:
            t_start = ts[:16]
        if not t_end:
            t_end = te[:16]
        elif t_end > te[:16]:
            t_end = te[:16]
    q["time_start"] = t_start
    q["time_end"] = t_end

    # 2) 车型/任务过滤须命中索引，否则置空（防伪造 "100"）
    robot = (q.get("robot_filter") or "").strip()
    q["robot_filter"] = (idx.valid_robot(robot) or "") if robot else ""

    task = (q.get("task_filter") or "").strip()
    q["task_filter"] = (idx.valid_task(task) or "") if task else ""

    # 2.5) 关键词过滤：非空且为短词才保留（防超长噪声），空则置空
    kw = (q.get("keyword_filter") or "").strip()
    q["keyword_filter"] = kw[:60] if kw else ""

    # 3) max_results / context_lines 夹紧
    try:
        q["max_results"] = min(max(int(q.get("max_results", 50)), 10), 100)
    except (TypeError, ValueError):
        q["max_results"] = 50
    try:
        q["context_lines"] = min(max(int(q.get("context_lines", 2)), 0), 5)
    except (TypeError, ValueError):
        q["context_lines"] = 2

    q["error_only"] = bool(q.get("error_only", True))
    return q


def _feedback_is_too_broad(matched: int) -> bool:
    return matched > _BROAD_WINDOW_HINT_LINES


# ── 健壮 JSON 命令解析（修复结尾解析失败丢结论的根因）────────────────

def _iter_json_objects(raw: str):
    """从任意文本里逐个提取可解析的 JSON 对象（容忍前导/尾部散文、代码块）。"""
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
                escape = False
                j += 1
                continue
            if c == '\\':
                escape = True
                j += 1
                continue
            if c == '"':
                in_str = not in_str
                j += 1
                continue
            if in_str:
                j += 1
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    block = text[start:j+1]
                    try:
                        yield json.loads(block)
                    except json.JSONDecodeError:
                        pass
                    start = j + 1
                    break
            j += 1
        else:
            break


def _parse_llm_command(raw: str) -> Optional[Dict]:
    """从 LLM 输出中提取最可信的一条命令。
    优先 action ∈ {query, conclude, fallback} 且形状正确的 JSON；
    越靠后的完整命令越可能是最终意图。
    """
    best = None
    best_score = -1
    for obj in _iter_json_objects(raw):
        if not isinstance(obj, dict):
            continue
        action = obj.get("action")
        if action not in ("query", "conclude", "fallback"):
            continue
        score = 0
        if action == "query" and isinstance(obj.get("query"), dict):
            score = 3
        elif action == "conclude" and obj.get("conclusion"):
            score = 3
        elif action == "fallback" and obj.get("reason"):
            score = 2
        if score > best_score:
            best_score = score
            best = obj
    return best


def _salvage_conclusion(raw: str) -> str:
    """LLM 整体解析失败时，从原始输出里抢救带 conclude 的结论文本。"""
    m = re.search(r'"conclusion"\s*:\s*"', raw)
    if not m:
        return ""
    seg = raw[m.end():]
    out = []
    i = 0
    while i < len(seg):
        c = seg[i]
        if c == '\\':
            if i + 1 < len(seg):
                out.append(seg[i+1])
            i += 2
            continue
        if c == '"':
            break
        out.append(c)
        i += 1
    txt = "".join(out).strip()
    return txt[:400] if txt else ""


def _matched_lines(query_result: str) -> int:
    m = re.search(r"matched (\d+)", query_result)
    return int(m.group(1)) if m else 0


def _is_near_duplicate(q: Dict, history: List[Dict]) -> bool:
    """判断新查询是否与历史查询近重复：过滤条件一致 且 时间窗重叠。"""
    for h in history:
        if (q.get("robot_filter") == h.get("robot_filter")
                and q.get("task_filter") == h.get("task_filter")
                and q.get("error_only") == h.get("error_only")):
            if _windows_overlap(q.get("time_start", ""), q.get("time_end", ""),
                                h.get("time_start", ""), h.get("time_end", "")):
                return True
    return False


def _windows_overlap(a0, a1, b0, b1) -> bool:
    if not (a0 and a1 and b0 and b1):
        return False
    return not (a1 < b0 or b1 < a0)


def _collect_evidence(result: LogAnalysisResult, query_result: str, round_num: int):
    """从查询结果文本里收集候选证据行。"""
    for line_match in re.findall(r"\* L(\d+)\| (.*)", query_result):
        ln, sm = line_match
        summary = sm.strip()
        if not summary or summary.count("|") < 1:
            continue
        if any(e["line"] == int(ln) for e in result.evidence):
            continue
        result.evidence.append({"line": int(ln), "summary": sm[:150], "round": round_num})


def _collect_evidence_into(evidence: List[Dict], query_result: str, only_hit: bool = False) -> None:
    """把查询结果文本里的候选证据追加进已有 evidence 列表（供 analyze_directive 复用）。

    only_hit=True 时只收带 '*' 标记的真正命中行（keyword/error 命中行），
    避免无 * 的 INFO context 行污染证据。
    """
    seen = {e["line"] for e in evidence}
    pat = r"\* L(\d+)\| (.*)" if only_hit else r"[* ] L(\d+)\| (.*)"
    for line_match in re.findall(pat, query_result):
        ln, sm = line_match
        summary = sm.strip()
        if not summary:
            continue
        if int(ln) in seen:
            continue
        evidence.append({"line": int(ln), "summary": sm[:150]})
        seen.add(int(ln))


def _filter_evidence_by_citation(result: LogAnalysisResult, cited: List):
    """按 LLM 引用的行号过滤证据；没引用行号时保留含关键信号的证据。"""
    cited_lines = set()
    for ref in cited:
        m = re.search(r"L(\d+)", str(ref))
        if m:
            cited_lines.add(int(m.group(1)))
    if cited_lines:
        result.evidence = [e for e in result.evidence if e["line"] in cited_lines]
    else:
        _SIGNAL_KW = ("一致性", "MAPF-T", "ABORTED", "WARNING", "等待时间", "last_node",
                      "超时", "失败", "ERR=", "CANCELED", "拒绝")
        result.evidence = [
            e for e in result.evidence
            if any(kw in e["summary"] for kw in _SIGNAL_KW)
        ]
