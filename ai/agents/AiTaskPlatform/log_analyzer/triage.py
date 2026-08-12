"""Triage（分诊）层 — 手册驱动的 Discovery Pipeline

设计哲学（2026-08-12，与楚白《日志分析指南》手册体系对齐）：

  不是"万能日志 Agent 无限 query"，而是"可编排的 Skill 流水线"：

    Skill 0 ManualGuide   : 读日志手册，动态学会"这份日志属于哪个模块、该查什么信号"
    Skill 1 ErrorDiscovery: 纯程序扫描异常信号 + 热窗口（零 LLM，快、稳、可追溯）
    Skill 2 EntityDiscovery: 从热窗口反推最活跃的车型/任务（搜索空间缩小 100 倍）
    Skill 3 ScenarioClassifier: 规则式场景分类（一致性/等待/重规划/超时）
    → 输出"聚光灯"给下游 LogSubAgent 做聚焦确认，而不是让它黑暗中乱猜

关键原则：
  - 手册是「数据」（会迭代，放服务器文件系统 or 知识库），Agent 是「策略」。
  - 信号关键词/场景规则**不写死**，而是从手册动态提取；无手册时才用内置通用启发式兜底。
  - 这样将来加"服务号日志分析"只需写好新模块手册，无需改本文件逻辑。

手册加载优先级（多产品 LOG_MANUALS 为唯一产品手册来源）:
  1. 多产品注册表 LOG_MANUALS：按日志路径命中产品 → 服务器优先/本地兜底（product_registry）
  2. 显式传入 manual_dir
  3. DOCS_PATH 下的 task_agent/ 目录
  4. 代码目录内置 log_manual/（离线兜底，非产品手册）
  （将来手册入库 Qdrant 后，可加一个 ManualSource 抽象插拔，见 ManualProvider）
"""

import re, os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

from ai.agents.AiTaskPlatform.log_analyzer.indexer import (
    LogIndex, LogQuery,
)


# ════════════════════════════════════════════════════════════
# Skill 0 — ManualGuide：手册理解（动态、不固化）
# ════════════════════════════════════════════════════════════

# 内置兜底信号（仅当手册里没有任何信号时才用，不声称是某个模块的权威语义）
_BUILTIN_GENERIC_SIGNALS: List[Dict] = [
    {"name": "ERROR_LEVEL", "keywords": [" - ERROR - "], "weight": 1},
    {"name": "WARNING_LEVEL", "keywords": [" - WARNING - "], "weight": 1},
    {"name": "ABORTED", "keywords": ["ABORTED"], "weight": 2},
    {"name": "CANCELED", "keywords": ["CANCELED"], "weight": 2},
    {"name": "TIMEOUT", "keywords": ["超时", "timeout", "TIMEOUT"], "weight": 2},
    {"name": "FAILED", "keywords": ["失败", "FAILED", "失败: "], "weight": 2},
    {"name": "REJECT", "keywords": ["拒收", "拒绝", "REJECT"], "weight": 1},
]


class ManualGuide:
    """从手册目录里，动态提取信号关键词表和故障场景。

    - 支持分目录/递归（如 平台服务/、算法模块/ 子目录）
    - 支持按日志路径路由：只加载与当前日志所属平台/模块相关的手册，避免信号污染
    - 手册是「数据」，本类只是解析，不固化任何模块的权威语义
    """

    # 非业务手册（跳过）：README / 生成提示词 / 总览
    _SKIP_FILES = ("readme", "生成提示词", "总览")
    # 总览文档文件名（若存在，用于提取路由映射）
    _OVERVIEW_FILE = "USP平台完整架构与日志分析总览.md"

    def __init__(self, manual_dir: Optional[str] = None, log_path: str = ""):
        self.log_path = log_path or ""
        self.manual_dir = self._resolve_dir(manual_dir, self.log_path)
        self.signals: List[Dict] = []      # [{name, keywords, weight, source}]
        self.scenarios: List[Dict] = []    # [{title, keywords, steps}]
        self.normal_timeline: List[str] = []  # 正常流程日志串
        self.loaded_files: List[str] = []
        self.routed = {}                   # 路由结果 {module, category, matched_files}
        if self.manual_dir:
            self._parse_and_route()

    # ── 目录解析（多产品注册表：服务器优先、本地兜底；再回退代码内置兜底）──
    @staticmethod
    def _resolve_dir(manual_dir: Optional[str], log_path: str = "") -> Optional[str]:
        # 0. 多产品注册表：按日志路径命中产品 → 服务器优先/本地兜底（唯一产品手册来源）
        from ai.agents.AiTaskPlatform.product_registry import pick_manual_dir
        _reg = pick_manual_dir(log_path)
        if _reg:
            return _reg

        candidates = []
        # 1. 显式传入
        if manual_dir:
            candidates.append(manual_dir)
        # 2. docs_path/task_agent
        from ai.config import get_ai_config
        _cfg = get_ai_config()
        if _cfg.docs_path:
            candidates.append(str(Path(_cfg.docs_path) / "task_agent"))
        # 3. 代码目录内置 log_manual（离线兜底，非产品手册）
        candidates.append(str(Path(__file__).parent / "log_manual"))

        for c in candidates:
            if c and Path(c).is_dir():
                return c
        return None

    # ── 路由 + 解析 ──
    def _parse_and_route(self):
        root = Path(self.manual_dir)

        # 收集所有 .md（递归子目录），排除非业务手册
        all_md = []
        for md in sorted(root.rglob("*.md")):
            rel = md.relative_to(root).as_posix()
            if any(k in rel.lower() for k in self._SKIP_FILES):
                continue
            all_md.append(md)

        # 根据日志路径路由：只选相关手册
        selected = self._route_files(all_md)
        self.routed = {"module": _detect_module(self.log_path),
                       "category": _detect_category(self.log_path),
                       "selected": [m.name for m in selected],
                       "total": len(all_md)}

        # 若路由没选出（路径未知），回退到全部（宁可多加载，不遗漏）
        if not selected:
            selected = all_md
            self.routed["selected"] = [m.name for m in selected]

        for md in selected:
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            self._extract_signals(text, md.name)
            self._extract_scenarios(text, md.name)
            self._extract_timeline(text, md.name)
            self.loaded_files.append(md.name)

        self.signals = _dedup_signals(self.signals)
        logger.info(f"ManualGuide: 加载 {len(self.loaded_files)}/{len(all_md)} 份手册"
                    f"(路由={self.routed.get('module',{}).get('name')}), "
                    f"信号 {len(self.signals)} 个, 场景 {len(self.scenarios)} 个, "
                    f"时间线 {len(self.normal_timeline)} 条")

    # ── 路由：根据日志路径筛选要加载的手册 ──
    def _route_files(self, all_md: List[Path]) -> List[Path]:
        mod = _detect_module(self.log_path)
        cat = _detect_category(self.log_path)
        if not mod.get("detected") and not cat.get("detected"):
            return []

        # 类别目录名匹配（算法模块/ 平台服务/）
        if cat.get("name") == "algorithm":
            candidates = [m for m in all_md if "算法模块" in m.as_posix()]
            return candidates if candidates else all_md
        if cat.get("name") == "platform":
            candidates = [m for m in all_md if "平台服务" in m.as_posix()]
            # 平台服务子类：按服务名关键词再收敛
            if mod.get("keywords"):
                narrowed = [m for m in candidates if any(k.lower() in m.name.lower() for k in mod["keywords"])]
                if narrowed:
                    return narrowed
            return candidates if candidates else all_md

        # 未分类到子目录：按模块关键词在文件名里匹配
        if mod.get("keywords"):
            hit = [m for m in all_md if any(k.lower() in m.name.lower() for k in mod["keywords"])]
            if hit:
                return hit
        return []

    # ── 信号提取（反引号/双引号里的日志字符串，剔除占位符）──
    def _extract_signals(self, text: str, src: str):
        # 表格第一列的 `...`（06 速查手册形态）
        found = set()
        for m in re.finditer(r"`([^`]+)`", text):
            s = _clean_signal(m.group(1))
            if s:
                found.add(s)
        # "..." 引号里的业务串（01 等文档的异常分支形态）
        for m in re.finditer(r'"([^"]{4,120})"', text):
            s = _clean_signal(m.group(1))
            if s:
                found.add(s)
        for s in found:
            self.signals.append({
                "name": s[:40],
                "keywords": [s],
                "weight": 2,
                "source": src,
            })

    # ── 故障场景提取（## 场景 / ### 场景 / 数字标题）──
    def _extract_scenarios(self, text: str, src: str):
        # 匹配 "## 场景 N：xxx" 或 "### 场景 N：xxx" 或 "## 场景 xxx"
        for m in re.finditer(r'#{1,3}\s*(场景\s*[0-9０-９]{0,2}[:：]?[^\n#]*)', text):
            title = m.group(1).strip()
            # 收集该章节后的步骤关键词
            end = text.find("\n##", m.end())
            seg = text[m.end():end if end > 0 else len(text)]
            kw = _collect_quoted(seg)
            self.scenarios.append({
                "title": title[:80],
                "keywords": kw,
                "steps": seg.strip()[:2000],
                "source": src,
            })

    # ── 正常流程时间线提取（[正常流程] 块里的日志串）──
    def _extract_timeline(self, text: str, src: str):
        for m in re.finditer(r"\[正常流程\](.*?)(?=\[异常\]|###|\n##|\Z)", text, re.S):
            seg = m.group(1)
            for line in seg.splitlines():
                line = line.strip()
                if line.startswith(('"', '`')):
                    self.normal_timeline.append(line[:120])
        # 也抓 "全链路" / "时间线" 段
        for m in re.finditer(r"(?:全链路|时间线)([^\n]{0,300})", text):
            self.normal_timeline.append(m.group(1)[:120])

    # ── 对外查询：根据用户问题/症状，挑出最相关信号 ──
    def relevant_signals(self, user_question: str = "", top_n: int = 20) -> List[Dict]:
        """基于用户问题关键词，把手册信号做一次粗排序（后续交给 Discovery 命中统计）。"""
        if not user_question:
            return self.signals[:top_n]
        words = [w for w in re.split(r"[\s,，。：:;；/\\|]+", user_question) if len(w) >= 2]
        scored = []
        for sig in self.signals:
            score = 0
            for w in words:
                if any(w in k or k in w for k in sig["keywords"]):
                    score += 1
                if w in sig["name"]:
                    score += 2
            if score:
                scored.append((score, sig))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:top_n]]


# 含这些词的信号视为"日志语义词"，即使无中文也不是纯字段名，必须保留
_LOG_SEMANTIC_KW = (
    "ERROR", "WARN", "FAIL", "FATAL", "EXCEPTION", "TIMEOUT", "ABORTED",
    "CANCELED", "MAPF", "WAIT", "REJECT", "拒绝", "超时", "一致性", "等待",
    "路径", "规划", "失败", "异常", "断开", "连接", "阻塞", "拥塞",
)


def _is_field_name_like(s: str) -> bool:
    """判断字符串是否形如'纯代码字段名'（无中文、纯标识符、无连字符/空格）。

    例如 workingState / envelope2d / PathStatus / taskNodeId —> True（应过滤）。
    而 MAPF-T / WAIT-T / 一致性超阈值 —> False（真信号）。
    """
    # 含中文 → 不是字段名
    if re.search(r"[\u4e00-\u9fa5]", s):
        return False
    # 含连字符/空格/冒号等 → 不是纯标识符字段名
    if re.search(r"[^a-zA-Z0-9_]", s):
        return False
    # 纯字母数字下划线构成的"标识符" → 视为字段名，过滤它
    return True


def _clean_signal(s: str) -> str:
    """清洗信号：去占位符 {}、去首尾空白、去无关符号；过滤纯代码字段名。"""
    s = s.strip()
    if not s or len(s) < 3:
        return ""
    # 去掉 {} 占位符
    s = re.sub(r"\{[^}]*\}", "", s).strip()
    # 去掉纯符号
    if not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", s):
        return ""
    # 过滤"纯代码字段名"（如 workingState/envelope2d），但保护日志语义信号词
    if _is_field_name_like(s) and not any(kw in s.upper() for kw in _LOG_SEMANTIC_KW):
        return ""
    return s


def _collect_quoted(seg: str) -> List[str]:
    kws = []
    for m in re.finditer(r'`([^`]+)`|"([^"]{4,120})"', seg):
        s = _clean_signal(m.group(1) or m.group(2))
        if s:
            kws.append(s)
    return kws


def _dedup_signals(signals: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for s in signals:
        key = s["name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# ════════════════════════════════════════════════════════════
# Skill 1 — ErrorDiscovery：纯程序信号扫描 + 热窗口
# ════════════════════════════════════════════════════════════

class ErrorDiscoverySkill:
    """扫描日志，把手册给的信号关键词在日志里的出现次数/分布统计出来。

    不依赖 LLM。信号来源优先手册，其次内置通用启发式。
    """

    def __init__(self, manual: ManualGuide):
        self.manual = manual

    def run(self, ctx: Dict) -> Dict:
        idx: LogIndex = ctx["index"]
        signals = self.manual.signals or _BUILTIN_GENERIC_SIGNALS

        # 1) 先看"有没有明确的错误输出"，这比稀有信号词更重要
        level_dist = _level_distribution(idx)
        top_errors = _top_errors(idx, top_n=10)

        signal_hits = _count_signals(idx, signals)
        hot_windows = _hot_windows(idx, top_n=ctx.get("hot_window_n", 5), bucket=ctx.get("hot_bucket", "min"))

        ctx["level_dist"] = level_dist
        ctx["signals"] = signal_hits
        ctx["hot_windows"] = hot_windows
        ctx["top_errors"] = top_errors
        return ctx


def _count_signals(idx: LogIndex, signals: List[Dict]) -> List[Dict]:
    """统计各信号关键词在 **错误/信号行** 里的命中次数。

    不重扫全文件：只在 _err_lines（错误/信号行）对应的内容里匹配。为控制成本，
    错误行可能很多，这里用倒排 + 按需读取。实际上 Discovery 只关心"有哪些信号、大概多少"，
    所以对命中行做抽样读即可（_SAMPLE 行内全读，够支撑排序）。
    """
    hits = []
    # 去重并取错误行号集合（去重后排序便于流式读取）
    err_lines = sorted(set(idx._err_lines))
    if not err_lines:
        return hits
    # 抽样：最多读 N 行做关键词命中统计（约足够估计 Top 信号）
    SAMPLE = err_lines[::max(1, len(err_lines) // 2000)][:2000]
    ln_set = set(SAMPLE)

    kw_cnt: Dict[str, int] = {}
    with open(idx.log_path, "r", encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            if n in ln_set:
                for sig in signals:
                    for kw in sig["keywords"]:
                        if kw in line:
                            kw_cnt.setdefault(sig["name"], 0)
                            kw_cnt[sig["name"]] += 1
                            break
    for sig in signals:
        c = kw_cnt.get(sig["name"], 0)
        if c:
            hits.append({"name": sig["name"], "count": c, "keywords": sig["keywords"]})
    hits.sort(key=lambda x: -x["count"])
    return hits


def _hot_windows(idx: LogIndex, top_n: int = 5, bucket: str = "min") -> List[Dict]:
    """找异常/信号最密集的分钟级时间桶。

    复用 build 时统计的 _err_minute（错误行分钟聚合）与 _signal_minute（信号行分钟聚合），
    两者合并后取 Top。零文件重扫。
    """
    agg: Dict[str, int] = {}
    for minute, c in idx._err_minute.items():
        agg[minute] = agg.get(minute, 0) + c
    for minute, c in idx._signal_minute.items():
        # 信号行可能和错误行重叠，这里以错误行为主，信号行仅补充
        agg[minute] = agg.get(minute, 0)
    ranked = sorted(agg.items(), key=lambda kv: -kv[1])[:top_n]
    return [{"start": m, "end": _minute_end(m), "count": c} for m, c in ranked]


def _minute_end(minute: str) -> str:
    from datetime import datetime, timedelta
    try:
        dt = datetime.strptime(minute, "%Y-%m-%d %H:%M")
        return (dt + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return minute


def _top_errors(idx: LogIndex, top_n: int = 8) -> List[Dict]:
    """自主发现的高频真实错误短语，**ERROR 优先于 WARNING**。

    方法论（用户要求）: 若日志存在 ERROR，优先呈现/定位 ERROR（含 Traceback 展开如
    TimeoutError）；仅当 ERROR 不存在或不足以定位时才以 WARNING 为主信号。

    返回: {"primary": [...], "warning": [...]}，primary 为 ERROR 或(WARNING 兜底)。
    """
    err_ph = idx._err_phrase_err
    warn_ph = idx._err_phrase_warn

    def _top(d, n):
        ranked = sorted(d.items(), key=lambda kv: -kv[1])[:n]
        return [{"code": c, "count": v} for c, v in ranked]

    err_top = _top(err_ph, top_n)
    warn_top = _top(warn_ph, top_n)
    if err_top:
        # 存在 ERROR：primary=ERROR，WARNING 作为补充
        return {"primary": err_top, "warning": warn_top, "has_error": True}
    # 无 ERROR：退而用 WARNING
    w = _top(warn_ph or idx._err_phrase, top_n)
    return {"primary": w, "warning": [], "has_error": False}


def _level_distribution(idx: LogIndex) -> Dict:
    """错误级别分布：先看日志有没有明确的 ERROR/WARN 输出。

    复用 build() 时统计的 _level_count（零重扫）。无 level 标注（如纯 INFO 前的空行）不计。
    """
    lc = idx._level_count
    total = sum(lc.values()) or 0
    err = lc.get("ERROR", 0)
    warn = lc.get("WARN", 0) + lc.get("WARNING", 0)
    fatal = lc.get("FATAL", 0)
    return {
        "total_tagged": total,
        "INFO": lc.get("INFO", 0),
        "ERROR": err,
        "WARNING": warn,
        "FATAL": fatal,
        "err_ratio": round(err / total * 100, 1) if total else 0.0,
        "has_error": (err + warn + fatal) > 0,
    }


# ════════════════════════════════════════════════════════════
# Skill 2 — EntityDiscovery：热窗口 → 活跃车型/任务
# ════════════════════════════════════════════════════════════

class EntityDiscoverySkill:
    """在热窗口内，统计哪些车型/任务最活跃（异常密集关联对象）。

    这一步把搜索空间缩小：告诉下游"这个时间窗里 XNA-169 有 23 条异常"。
    只读窗口内的错误/信号行（数量可控），不重扫全文件。
    """

    def run(self, ctx: Dict) -> Dict:
        idx: LogIndex = ctx["index"]
        window = ctx.get("hot_window")
        if not window:
            ctx["entities"] = []
            return ctx

        start, end = window.get("start"), window.get("end")
        robots, tasks = _count_entities_in_window(idx, start, end, top_n=6)
        ctx["entities"] = {
            "window": {"start": start, "end": end},
            "robots": robots,
            "tasks": tasks,
        }
        return ctx


def _count_entities_in_window(idx: LogIndex, start: str, end: str,
                              top_n: int = 6) -> Tuple[List, List]:
    """统计时间窗 [start,end] 内，各车型/任务的活跃度。

    方法：用 _ts_idx（时间→行号）定位窗口内所有行号，再反向统计这些行号里
    各 robot/task 出现的次数。为避免对超大 _robot_idx 集合求交，这里用
    _err_lines（错误/信号行）∩ 窗口行号 来做——因为 Discovery 关心的是"异常窗口里谁最活跃"。
    """
    # 窗口内所有行号
    window_lines = set()
    for ts, lines in idx._ts_idx.items():
        if start <= ts <= end:
            window_lines.update(lines)

    # 只取错误/信号行 ∩ 窗口行
    err_set = set(idx._err_lines)
    focus = window_lines & err_set if err_set else window_lines
    focus = sorted(focus)[:2000]  # 控制读取量

    rob_cnt: Dict[str, int] = {}
    task_cnt: Dict[str, int] = {}
    if focus:
        try:
            from ai.agents.AiTaskPlatform.log_analyzer.indexer import extract_fields
        except Exception:
            extract_fields = None
        with open(idx.log_path, "r", encoding="utf-8", errors="replace") as f:
            ln_set = set(focus)
            for n, line in enumerate(f, 1):
                if n in ln_set:
                    if extract_fields:
                        fld = extract_fields(line)
                        for r in fld.get("robots", []):
                            rob_cnt[r] = rob_cnt.get(r, 0) + 1
                        for t in fld.get("tasks", []):
                            task_cnt[t] = task_cnt.get(t, 0) + 1

    rob_top = sorted(rob_cnt.items(), key=lambda kv: -kv[1])[:top_n]
    task_top = sorted(task_cnt.items(), key=lambda kv: -kv[1])[:top_n]

    # 若窗口内没提取到实体，退回全局top（至少给个可点选的候选）
    if not rob_top:
        rob_top = sorted(idx._robot_idx.items(), key=lambda kv: -len(kv[1]))[:top_n]
        rob_top = [(k, len(v)) for k, v in rob_top]
    if not task_top:
        task_top = sorted(idx._task_idx.items(), key=lambda kv: -len(kv[1]))[:top_n]
        task_top = [(k, len(v)) for k, v in task_top]

    return ([{"id": k, "count": c} for k, c in rob_top],
            [{"id": k, "count": c} for k, c in task_top])


# ════════════════════════════════════════════════════════════
# Skill 3 — ScenarioClassifier：规则式场景分类（手册驱动）
# ════════════════════════════════════════════════════════════

class ScenarioClassifierSkill:
    """根据信号组合，用规则判场景。规则可被手册场景扩展。

    返回 {scenario, confidence, matched_signals, template_hint}。
    """

    def run(self, ctx: Dict) -> Dict:
        signals = {s["name"]: s["count"] for s in ctx.get("signals", [])}
        scenario, conf, hint = classify(signals)
        ctx["scenario"] = {"name": scenario, "confidence": conf,
                           "matched_signals": list(signals.keys())[:10],
                           "template_hint": hint}
        return ctx


def classify(signals: Dict[str, int]) -> Tuple[str, float, str]:
    """规则判断（可被手册场景覆盖）。"""
    def has(*kws):
        return any(any(k in name for k in kws) and cnt > 0
                   for name, cnt in signals.items())

    if has("一致性超阈值", "一致性超过update阈值"):
        return ("CONSISTENCY_REPLAN", 0.9,
                "一致性超阈值→路径截断→重新规划。模板: 车型+窄时间窗+error_only=false+context=3")
    if has("一致性校验失败", "一致性不满足", "current Task一致性"):
        return ("CONSISTENCY_FAIL", 0.8,
                "一致性校验失败→task_node_id 不匹配。模板: 车型+taskNodeId 对账")
    if has("等待时间超限", "WAIT-T", "wait_time", "等待时间"):
        return ("WAITING", 0.7,
                "等待超限→资源等待。模板: 查等待阈值配置 wait_time_check_interval/update_gap/auto_replan_gap")
    if has("MAPF-T", "规划耗时"):
        return ("REPLAN_SLOW", 0.5,
                "MAPF 规划耗时偏高→可能规划拥塞/图过大。模板: 观察 MAPF-T 分布+并发")
    if has("ABORTED"):
        return ("TASK_ABORTED", 0.6,
                "任务被终止。模板: 车型+task_id 追根因")
    if has("路径规划超时", "规划超时"):
        return ("PLAN_TIMEOUT", 0.8,
                "路径规划超时→srp_timeout/图过大/并发不足。模板: 查 DPP规划请求/结果 对账")
    return ("GENERAL", 0.2,
            "未匹配到明确场景，建议先呈现 Discovery 信号给工程师确认")


# ════════════════════════════════════════════════════════════
# 编排器 — 用到底层确认者（LogSubAgent）之前的前置流水线
# ════════════════════════════════════════════════════════════

def run_triage(log_path: str, user_question: str = "", manual_dir: Optional[str] = None,
               index: Optional[LogIndex] = None) -> Dict:
    """完整 Discovery 流水线（纯程序 + 规则，不调 LLM）。

    - index: 若传入已构建的 LogIndex 则复用（避免重复 build，尤其大日志），否则内部 build。
    输出: {facts, signals, hot_windows, entities, scenario, module, guide_summary}
    """
    t0 = _time()
    idx = index if index is not None else LogIndex(log_path).build()
    ctx: Dict = {"index": idx}

    # Skill 0：手册理解（按日志路径路由到相关手册）
    manual = ManualGuide(manual_dir, log_path=log_path)
    guide = {
        "dir": manual.manual_dir,
        "files": manual.loaded_files,
        "signal_count": len(manual.signals),
        "scenario_count": len(manual.scenarios),
        "routed": manual.routed,
    }

    # Skill 1：错误发现
    ctx = ErrorDiscoverySkill(manual).run(ctx)
    # Skill 2：实体发现（用第一个热窗口）
    if ctx["hot_windows"]:
        ctx["hot_window"] = ctx["hot_windows"][0]
        ctx = EntityDiscoverySkill().run(ctx)
    # Skill 3：场景分类
    ctx = ScenarioClassifierSkill().run(ctx)

    return {
        "module": _detect_module(log_path),
        "guide": guide,
        "facts": idx.discover_facts(top_n=8),
        "level_dist": ctx.get("level_dist", {}),
        "top_errors": ctx.get("top_errors", []),
        "signals": ctx.get("signals", []),
        "hot_windows": ctx.get("hot_windows", []),
        "entities": ctx.get("entities", {}),
        "scenario": ctx.get("scenario", {}),
        "elapsed_ms": round((_time() - t0) * 1000),
    }


def _detect_category(log_path: str) -> Dict:
    """判断日志属于哪个大类：算法模块 / 平台服务。

    依据：usp 算法日志路径含 USPA-LOGS-；平台服务日志路径含平台服务目录名。
    """
    p = Path(log_path).as_posix().upper()
    if "USPA-LOGS-" in p or "USP_ALGORITHM" in p or "ALGORITHM" in p:
        return {"name": "algorithm", "detected": True}
    # 平台服务常见日志路径关键词
    platform_kw = ["USP-BASE.LOG", "TASKFLOW", "DEVICE.LOG", "STORAGE.LOG",
                    "ROBOTADAPTER.LOG", "MAIN.LOG", "SIMULATOR.LOG",
                    "LOGS/TASKFLOW", "DEVICEPLATFORM", "STORAGEPLATFORM",
                    "MAPPLATFORM", "ROBOTADAPTER"]
    for k in platform_kw:
        if k in p:
            return {"name": "platform", "detected": True}
    return {"name": "", "detected": False}


def _detect_module(log_path: str) -> Dict:
    """从日志路径/目录名/文件名判断属于哪个模块，返回可路由的关键词。

    覆盖：算法层(DYNAMIC_MAP/TMS/TASK-MANAGER/AI_map/MapPreprocess) + 平台服务(Base/taskFlow/device/...)。
    关键词用于在手册文件名里进一步收敛。
    """
    p = Path(log_path).as_posix().upper()
    base = {
        "DYNAMIC_MAP": ("dynamic_map", ["dynamic"]),
        "TMS-": ("tms", ["tms"]),
        "TASK-MANAGER": ("task_manager", ["task-manager", "task_manager"]),
        "AI_MAP": ("ai_map", ["ai-map", "ai_map"]),
        "MAPPR": ("map_preprocess", ["map-preprocess", "mapper"]),
        "USP-BASE": ("base", ["base"]),
        "TASKFLOW": ("task_flow", ["taskflow", "task-flow"]),
        "DEVICE": ("device", ["device"]),
        "STORAGE": ("storage", ["storage"]),
        "ROBOTADAPTER": ("robot_adapter", ["robotadapter", "robot-adapter"]),
        "SIMULATOR": ("simulator", ["simulator"]),
        "MONITORPLATFORM": ("monitor", ["monitor"]),
        "MAPPLATFORM": ("map_platform", ["map-platform", "map"]),
    }
    for key, (name, kws) in base.items():
        if key in p:
            return {"name": name, "detected": True, "keywords": kws}
    return {"name": "unknown", "detected": False, "keywords": []}


def _time():
    import time
    return time.perf_counter()
