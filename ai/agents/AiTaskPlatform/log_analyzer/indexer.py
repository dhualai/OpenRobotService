"""算法日志解析器 — AGV 调度算法日志的结构化索引与查询

算法日志特点:
    28万行+，单行可达6MB(嵌入Python repr对象序列化)
    有效信息(时间戳/车辆/路径/错误码) <1%

工程师排查流程: 定位时间窗口 → 过滤车辆/任务 → 追踪路径

策略:
    1. 结构化提取: ts/robot/task/path/error/node/pos/index
    2. 索引: 流式扫描→时间/车辆/任务/路径索引，首次30秒，后续毫秒查询
    3. 按查询条件读取候选行→人类可读摘要（超长行只取行头 + 关键词窗口，禁止整行正则）
"""

import re, os, time as _time
from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

from typing import Optional, Dict, List


# ── 字段提取 ──

_RE_TS = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
_RE_LVL = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - (\w+) - ")
_RE_ROBOT = re.compile(r"(?:robot_id[=:]\s*'?)([A-Z]+[-_][A-Z]*[-_]?\d+)", re.IGNORECASE)
_RE_ROBOT2 = re.compile(r"Robot:\s*([A-Z]+[-_]\w+)", re.IGNORECASE)
_RE_ROBOT3 = re.compile(r"'id':\s*'([A-Z]+[-_]\w+)'", re.IGNORECASE)
_RE_TASK = re.compile(r"(?:task_id[=:]\s*'?)(\d{10,})", re.IGNORECASE)
_RE_TASK2 = re.compile(r"Task Node Id:(\d+)")
_RE_TASK3 = re.compile(r"taskNodeId:(\d+)")
_RE_TASK4 = re.compile(r"'taskId':\s*'([^']+)'", re.IGNORECASE)  # I|xx 格式
_RE_ERROR = re.compile(r"error_code[=:]\s*'([^']+)'")
_RE_PATH = re.compile(r"Path:([A-Z]+[-_]\w+_\d+_\d+_\d+)", re.IGNORECASE)
_RE_NODE = re.compile(r"Node:(\d+)")
_RE_POS = re.compile(r"Pos:\[([^\]]+)\]")
_RE_IDX = re.compile(r"Index:(\d+)")
_RE_DESC = re.compile(r"description[=:]\s*'([^']+)'")
_RE_NUMNODES = re.compile(r"Num Of Node:(\d+)")
_RE_MAPF_T = re.compile(r"MAPF-T:([\d.]+)")    # MAPF 规划耗时
_RE_WAIT_T = re.compile(r"WAIT-T:([\d.]+)")     # 等待耗时

# 从 ERROR/WARNING 消息里提取"中文错误短语"作归类键（不依赖 error_code= 格式）
# 匹配如: last_node_index校验失败 / 当前位置与last_node_index不匹配 / 路径规划超时 ...
_RE_ERR_PHRASE = re.compile(r"([\u4e00-\u9fa5A-Za-z_]{1,40}?(?:校验失败|失败|异常|不匹配|拒绝|超时|错误|为空|不存在|无法|失效))")

# 级别标记只在行首附近；单行可达 6MB，禁止用 (.*)$ / 全行正则 / 全行 lower()。
_RE_LVL_MARK = re.compile(r":\d{2}:\d{2},\d{3} - [A-Z]+ - ")
_RE_AGV_ID = re.compile(r"agv-\d+", re.IGNORECASE)
_RE_NUM = re.compile(r"\d+")
_LVL_HEAD = 4096            # 时间戳+级别一定落在行头
_FIELD_SCAN = 16384         # 建索引时结构化字段只扫行头
_SIGNAL_SCAN = 262144       # 建索引时信号词探测上限（256KB）
_MSG_HEAD = 180             # 正文开头（事件名通常在这）
_MSG_LIMIT = 2000           # 回给 LLM 的单行正文上限（聚焦时间窗内仍需多看一段原文）
_SUMMARY_LIMIT = 2400
_WIN_BEFORE = 40
_WIN_AFTER = 80
# 锁区/回调/状态机/可达性等关键词常埋在数 MB 的 repr 尾部，必须用 find 定位
_KEEP_WORDS = (
    "锁区", "占用", "释放", "回调", "状态机", "强制完成", "取货", "送货",
    "lock_zone", "lockzone", "occupy", "Occupy", "OCCUPY",
    "release", "Release", "callback", "Callback",
    "state_machine", "StateMachine", "ABORTED", "CANCELED", "MAPF",
    # 可达性 / 路径求解（常埋在超长 UPDATE 之后）
    "不可达", "可达", "无解", "求解", "拓扑", "topo", "TOPO",
    "unreachable", "Unreachable", "UNREACHABLE",
    "no solution", "NO_SOLUTION", "no_path", "NO_PATH", "NoSolutionFound",
    "路径规划", "path plan", "PathPlan", "planning fail",
    "DPP规划请求", "DPP规划结果", "路径规划开始", "路径规划结束",
    "路径规划失败", "路径规划超时", "路径规划异常", "路径规划结果为空",
    "LOCATE FAILED", "LOCATE ERROR", "LOCATE SUCCESS",
    "目标点", "goal", "Goal", "mapId", "map_id", "地图层",
    "不在当前所有地图层上", "前置点", "路径拒收", "DMAP已接收路径",
    "新任务-----", "求解成功", "非强连通",
)

# 行头像「刷状态/更新」的 INFO 大行：体内常嵌历史 error_code，不能当 ERROR 行
_BULK_UPDATE_MARKERS = (
    "更新Robots", "Update Robots", "update robots", "UPDATE ROBOTS",
    "更新休息点", "更新充电", "更新任务池", "更新车辆",
    "更新 Robots", "sync robots", "Sync Robots",
)


def _norm_ts_bound(raw: Optional[str], *, end: bool = False) -> str:
    """把查询时间界规范到与 ``_ts_idx`` 键（``YYYY-MM-DD HH:MM:SS``）可比较。

    LLM 常给分钟精度 ``YYYY-MM-DD HH:MM``。若 end 直接用该串做 ``<=``，
    会把同分钟内 ``:01``~``:59`` 的行全部排除（故障秒常在末尾），导致假 0 命中。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if len(s) >= 19:
        return s[:19]
    if len(s) == 16 and s[10] == " ":
        return s + (":59" if end else ":00")
    return s


def _clip(line: str, n: int) -> str:
    return line if len(line) <= n else line[:n]


def _body_start(line: str) -> int:
    m = _RE_LVL_MARK.search(line[:_LVL_HEAD] if len(line) > _LVL_HEAD else line)
    return m.end() if m else 0


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_level_body(line: str) -> str:
    """取级别标记后的正文片段（调用方自行再截断）。"""
    return line[_body_start(line):]


def evidence_body(line: str, limit: int = _MSG_LIMIT) -> str:
    """给 LLM 看的正文：保留原词原数字；超长行只切窗口，不压缩/不正则整行。

    聚类用的数字→N 只允许出现在 normalize_msg_phrase。
    """
    start = _body_start(line)
    n = len(line)
    if n - start <= limit:
        return _squash(line[start:n])
    windows = [(start, min(start + _MSG_HEAD, n))]
    for w in _KEEP_WORDS:
        i = line.find(w, start)
        if i < 0:
            continue
        windows.append((max(start, i - _WIN_BEFORE), min(n, i + len(w) + _WIN_AFTER)))
    windows.sort()
    merged = []
    for a, b in windows:
        if merged and a <= merged[-1][1] + 12:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    snippet = " … ".join(_squash(line[a:b]) for a, b in merged)
    return snippet[:limit]


def normalize_msg_phrase(line: str) -> str:
    """从 ERROR/WARNING 行提取可聚合的归一化短语，供 Discovery 自主发现高频错误。

    仅用于聚类计数，不是给 LLM 看的证据。
    """
    start = _body_start(line)
    body = line[start:start + 2000].strip()
    body = re.sub(r"id='[^']*'", "id=*", body)
    body = re.sub(r"id=\"[^\"]*\"", "id=*", body)
    body = re.sub(r"'agv-[\w\d-]*'", "agvN", body)
    body = _RE_AGV_ID.sub("agvN", body)
    body = re.sub(r"[A-Z]+[-_]\d+[-_]+\d+[-_]+\d+[-_]+\d+", "pathN", body)
    body = _RE_NUM.sub("N", body)
    body = re.sub(r"[:\s]{2,}", ":", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:60]


def _is_bulk_update_line(line: str) -> bool:
    """行头是否像车辆/休息点状态刷屏（超长 UPDATE，体内常嵌历史 error_code）。"""
    head = _clip(line, 480)
    return any(m in head for m in _BULK_UPDATE_MARKERS)


def extract_fields(line: str) -> Dict:
    """结构化字段只扫行头。超长 repr 里的词留给 evidence_body 用 find 取窗口。"""
    head = _clip(line, _FIELD_SCAN)
    fld = {}
    m = _RE_TS.search(head)
    if m: fld["ts"] = m.group(1)
    m = _RE_LVL.search(head)
    if m: fld["level"] = m.group(1)

    robots = set()
    for m in _RE_ROBOT.finditer(head): robots.add(m.group(1))
    for m in _RE_ROBOT2.finditer(head): robots.add(m.group(1))
    for m in _RE_ROBOT3.finditer(head): robots.add(m.group(1))
    for m in _RE_AGV_ID.finditer(head): robots.add(m.group(0))
    if robots: fld["robots"] = sorted(robots)

    tasks = set()
    for m in _RE_TASK.finditer(head): tasks.add(m.group(1))
    for m in _RE_TASK2.finditer(head): tasks.add(m.group(1))
    for m in _RE_TASK3.finditer(head): tasks.add(m.group(1))
    for m in _RE_TASK4.finditer(head): tasks.add(m.group(1))
    if tasks: fld["tasks"] = sorted(tasks)

    # error_code 只认行头短窗口，避免 UPDATE 大包体内嵌历史 error_code 污染
    early = _clip(line, 768)
    m = _RE_ERROR.search(early)
    if m and not _is_bulk_update_line(line):
        fld["error"] = m.group(1)

    paths = set()
    for m in _RE_PATH.finditer(head): paths.add(m.group(1))
    if paths: fld["paths"] = sorted(paths)

    for k, p in [("node", _RE_NODE), ("pos", _RE_POS), ("idx", _RE_IDX),
                  ("numnodes", _RE_NUMNODES)]:
        m = p.search(head)
        if m: fld[k] = m.group(1)

    m = _RE_DESC.search(head)
    if m: fld["desc"] = m.group(1)[:100]

    m = _RE_MAPF_T.search(head)
    if m: fld["mapf_t"] = float(m.group(1))
    m = _RE_WAIT_T.search(head)
    if m: fld["wait_t"] = float(m.group(1))
    # 可达性/一致性信号：扫更深，但不因此把 INFO 更新行标成 ERROR
    probe = _clip(line, _SIGNAL_SCAN)
    reach_hits = []
    for w in (
        "不可达", "unreachable", "Unreachable", "无解", "NO_SOLUTION",
        "no solution", "NoSolutionFound", "不在当前所有地图层上",
        "前置点不可达", "求解失败", "路径规划失败", "路径规划超时",
        "路径规划异常", "LOCATE FAILED", "LOCATE ERROR", "DPP规划",
        "路径拒收", "非强连通",
    ):
        if w in probe:
            reach_hits.append(w)
    if reach_hits:
        fld["reach_signal"] = ",".join(reach_hits[:4])
    if not _is_bulk_update_line(line):
        if "一致性超过update阈值" in probe:
            fld["error"] = (fld.get("error") or "") + " 一致性超阈值-路径截断"
        elif "一致性不满足" in probe or "current Task一致性" in probe:
            fld["error"] = (fld.get("error") or "") + " 一致性校验失败"
    return fld


def fields_summary(fld: Dict, line: str = "") -> str:
    """人类可读摘要。line 传入时用原文证据片段，数字不被替换成 N。"""
    parts = []
    ts = fld.get("ts", "")
    if ts:
        ts_short = ts[-12:] if len(ts) > 12 else ts
        parts.append("[{}]".format(ts_short))
    lv = fld.get("level", "")
    if lv: parts.append("[{}]".format(lv))
    for k, pfx in [("robots","R"), ("tasks","T"), ("paths","P")]:
        if k in fld:
            for v in fld[k]:
                parts.append("{}={}".format(pfx, v[-24:] if k=="paths" else v[-16:]))
    if "error" in fld: parts.append("ERR={}".format(fld["error"]))
    if fld.get("reach_signal"):
        parts.append("REACH={}".format(fld["reach_signal"]))
    extra = []
    body = evidence_body(line) if line else (fld.get("msg") or "")
    if body:
        extra.append("MSG={}".format(body))
    if "desc" in fld: extra.append(fld["desc"][:80])
    if "node" in fld: extra.append("node={}".format(fld["node"]))
    if "pos" in fld: extra.append("Pos=[{}]".format(fld["pos"]))
    if "idx" in fld: extra.append("#{}".format(fld["idx"]))
    if "numnodes" in fld: extra.append("(/{} nodes)".format(fld["numnodes"]))
    if "mapf_t" in fld: extra.append("MAPF-T={:.1f}s".format(fld["mapf_t"]))
    if "wait_t" in fld: extra.append("WAIT-T={:.1f}s".format(fld["wait_t"]))
    if extra: parts.append("| "+" ".join(extra))
    return " ".join(parts)[:_SUMMARY_LIMIT]


# ── 查询 + 索引 ──

class LogQuery:
    def __init__(self, time_start=None, time_end=None, robot_filter=None,
                 task_filter=None, path_filter=None, error_only=False,
                 keyword=None,
                 context_before=2, context_after=2, max_results=200):
        self.time_start = time_start
        self.time_end = time_end
        self.robot_filter = robot_filter
        self.task_filter = task_filter
        self.path_filter = path_filter
        self.error_only = error_only
        self.keyword = (keyword or "").strip()
        self.context_before = context_before
        self.context_after = context_after
        self.max_results = max_results


class LogIndex:
    def __init__(self, log_path: str):
        self.log_path = log_path
        self._ts_idx = {}       # ts[:19] -> [line_numbers]
        self._robot_idx = {}    # robot_id -> [line_numbers]
        self._task_idx = {}     # task_id -> [line_numbers]
        self._path_idx = {}     # path_id -> [line_numbers]
        self._err_lines = []    # error/warn line numbers（可能含重复，同行多信号）
        self._err_idx = {}      # error_code -> [line_numbers]
        self._err_hour = {}     # "YYYY-MM-DD HH" -> count（错误最密集时段）
        self._err_minute = {}   # "YYYY-MM-DD HH:MM" -> 错误/警告行数（Discovery 热窗口，零重扫）
        self._total = 0
        self._built = False
        self._signal_lines = []  # 含关键信号的行号（一致性/MAPF-T/ABORTED等）
        self._signal_minute = {}  # "YYYY-MM-DD HH:MM" -> 信号行数（口径与错误行一致）
        self._level_count = {}    # level -> 行数（INFO/ERROR/WARNING/...）
        self._err_phrase = {}     # WARNING/ERROR 归一化短语 -> 行数（Discovery 候选）
        self._err_phrase_err = {}  # ERROR/FATAL 级归一化短语（Traceback 展开后）-> 行数
        self._err_phrase_warn = {} # WARNING 级归一化短语 -> 行数

    def build(self) -> "LogIndex":
        t0 = _time.perf_counter()
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
            n = 0
            while True:
                line = f.readline()
                if not line:
                    break
                n += 1
                self._total = n
                fld = extract_fields(line)
                ts = fld.get("ts", "")
                lv = fld.get("level")
                if lv:
                    self._level_count[lv] = self._level_count.get(lv, 0) + 1
                if ts: self._ts_idx.setdefault(ts[:19], []).append(n)
                for r in fld.get("robots", []): self._robot_idx.setdefault(r, []).append(n)
                for t in fld.get("tasks", []): self._task_idx.setdefault(t, []).append(n)
                for p in fld.get("paths", []): self._path_idx.setdefault(p, []).append(n)
                # 真实 ERROR/WARN 级别才进错误索引。
                # 禁止：INFO「更新Robots」大包体内嵌历史 error_code → 假 5000+ 错误淹没求解信号。
                lv_up = (lv or "").upper()
                is_err = lv_up in ("ERROR", "WARN", "WARNING", "FATAL")
                if fld.get("reach_signal"):
                    self._signal_lines.append(n)
                    if ts:
                        minute = ts[:16]
                        self._signal_minute[minute] = self._signal_minute.get(minute, 0) + 1
                if is_err:
                    self._err_lines.append(n)
                    _lvl = fld.get("level", "")
                    _is_fatal = _lvl in ("ERROR", "FATAL")
                    # 展开 ERROR/FATAL 的 Traceback：读后续行找异常 message（如 TimeoutError: timed out）
                    if _is_fatal and "Traceback" in _clip(line, _FIELD_SCAN):
                        _tb_exc = ""
                        for _ in range(1, 40):
                            _bl = f.readline()
                            if not _bl:
                                break
                            n += 1
                            _s = _bl.strip()
                            # 异常 message 通常顶格(无前导空格)，且非 File/^ 行；
                            # 有缩进的源码行(如 "response = ...")跳过，继续找真正的异常
                            if _s and not _s.startswith("File") and "^" not in _s:
                                if _bl[0].isspace() and _s[0] != "Traceback":
                                    continue  # 缩进的源码行，不是异常 message
                                _tb_exc = _s[:80]
                                break
                        if _tb_exc:
                            fld["error"] = fld.get("error", "") + f" [Traceback] {_tb_exc}"
                    # 自主发现真实错误/警告短语（ERROR 与 WARNING 分开，ERROR 优先）
                    if _is_fatal and "Traceback" in _clip(line, _FIELD_SCAN):
                        # ERROR Traceback：用展开的异常作为短语键
                        _exc = (fld.get("error") or "").strip()
                        if _exc:
                            self._err_phrase_err.setdefault(_exc[:60], 0)
                            self._err_phrase_err[_exc[:60]] += 1
                    else:
                        ph = normalize_msg_phrase(line)
                        if ph and not ph.startswith("Traceback"):
                            (self._err_phrase_err if _is_fatal else self._err_phrase_warn)[ph] = \
                                (self._err_phrase_err if _is_fatal else self._err_phrase_warn).get(ph, 0) + 1
                            self._err_phrase[ph] = self._err_phrase.get(ph, 0) + 1
                    _err = fld.get("error")
                    if _err:
                        code = _err.strip()
                        self._err_idx.setdefault(code, []).append(n)
                    else:
                        # 无 error_code= 时，从消息提取中文错误短语作归类键（如 "校验失败"/"不匹配"）
                        _ph = _RE_ERR_PHRASE.search(_clip(line, _FIELD_SCAN))
                        if _ph:
                            ph = _ph.group(1).strip()
                            self._err_idx.setdefault(ph, []).append(n)
                    if ts:
                        hour = ts[:13]
                        self._err_hour[hour] = self._err_hour.get(hour, 0) + 1
                        minute = ts[:16]
                        self._err_minute[minute] = self._err_minute.get(minute, 0) + 1
                probe = _clip(line, _SIGNAL_SCAN)
                # 路径状态异常也是错误信号（跳过 UPDATE 刷屏体内的历史状态串）
                if not _is_bulk_update_line(line):
                    if "ABORTED" in probe or "CANCELED" in probe:
                        self._err_lines.append(n)
                        if ts:
                            minute = ts[:16]
                            self._err_minute[minute] = self._err_minute.get(minute, 0) + 1
                    # 一致性校验失败 / 路径截断 / MAPF耗时
                    _SIGNAL_KW = ("一致性超过update阈值", "一致性不满足", "MAPF-T:", "WAIT-T:", "等待时间超限")
                    if any(kw in probe for kw in _SIGNAL_KW):
                        self._err_lines.append(n)
                        self._signal_lines.append(n)
                        if ts:
                            minute = ts[:16]
                            self._signal_minute[minute] = self._signal_minute.get(minute, 0) + 1
                if n % 50000 == 0:
                    logger.info("{:,} lines indexed ({:.0f}s)".format(n, _time.perf_counter()-t0))
        elapsed = _time.perf_counter() - t0
        logger.info(
            "{:,} lines | {} ts | {} robots | {} tasks | {} errors | {} reach_signals ({:.0f}s)"
            .format(
                self._total, len(self._ts_idx), len(self._robot_idx),
                len(self._task_idx), len(self._err_lines),
                len(self._signal_lines), elapsed,
            )
        )
        self._built = True
        return self

    # ── 事实发现：把日志里的客观事实喂给 LLM，防止它凭空捏造日期/车型/任务ID ──
    def discover_facts(self, top_n: int = 8) -> Dict:
        """返回日志客观事实骨架，供 sub_agent 注入 Prompt。

        返回示例:
        {
          "lines": 609397,
          "errors": 296387,
          "time_start": "2026-08-11 11:16",
          "time_end": "2026-08-12 10:16",
          "date": "2026-08-11",
          "top_robots": ["XNA-169", ...],
          "top_tasks": ["I|1098000", ...],
          "top_errors": ["xxx", ...],   # 高频 error_code
          "error_hours": [("2026-08-11 11", 1234), ...],  # 错误最多的时段
        }
        """
        if not self._built:
            self.build()

        ts_keys = sorted(self._ts_idx.keys())
        time_start = ts_keys[0][:16] if ts_keys else None
        time_end = ts_keys[-1][:16] if ts_keys else None

        def _top(idx, n=top_n):
            ranked = sorted(idx.items(), key=lambda kv: -len(kv[1]))
            return [k for k, _ in ranked[:n]]

        # 错误最密集的时段（build 时已按小时聚合）
        error_hours = sorted(self._err_hour.items(), key=lambda kv: -kv[1])[:top_n]

        # 高频 error_code（从 error 字段聚合并修剪）
        err_cnt: Dict[str, int] = {}
        _err_field_idx = self._err_idx  # {error_code: [lines]}
        for code, lines in _err_field_idx.items():
            if code:
                err_cnt[code] = len(lines)
        top_errors = [c for c, _ in sorted(err_cnt.items(), key=lambda kv: -kv[1])[:top_n]]

        facts = {
            "lines": self._total,
            "errors": len(set(self._err_lines)),
            "reach_signals": len(set(self._signal_lines)),
            "time_start": time_start,
            "time_end": time_end,
            "top_robots": _top(self._robot_idx),
            "top_tasks": _top(self._task_idx),
            "top_errors": top_errors,
            "error_hours": error_hours,
        }
        if time_start and len(time_start) >= 10:
            facts["date"] = time_start[:10]
        return facts

    # ── 校验工具：查询参数是否命中有效数据（供 sub_agent 拦截伪造的过滤条件）──
    def valid_robot(self, fval: str) -> Optional[str]:
        """机器人过滤是否命中任何索引 key。

        - 车型 ID 形如 XNA-169 / USP-A，必须含字母前缀；纯数字（如 "100"）
          不是有效车型，直接判无效，防止 LLM 拿无意义数字空跑。
        - 命中返回真实 key，否则 None。
        """
        if not fval:
            return None
        if not re.search(r"[A-Za-z]", fval):
            return None
        for key in self._robot_idx:
            if fval in key:
                return key
        return None

    def valid_task(self, fval: str) -> Optional[str]:
        if not fval:
            return None
        for key in self._task_idx:
            if fval in key:
                return key
        return None

    def count_in_window(self, time_start: str, time_end: str) -> int:
        """统计时间窗口 [time_start, time_end] 内索引到的行数（用于太宽查询拦截）。"""
        if not (time_start and time_end):
            return 0
        t0 = _norm_ts_bound(time_start, end=False)
        t1 = _norm_ts_bound(time_end, end=True)
        cnt = 0
        for ts, lines in self._ts_idx.items():
            if t0 <= ts <= t1:
                cnt += len(lines)
        return cnt

    def query(self, q: LogQuery) -> str:
        if not self._built: self.build()

        filters = []
        t0 = _norm_ts_bound(q.time_start, end=False)
        t1 = _norm_ts_bound(q.time_end, end=True)
        if t0 and t1:
            s = set()
            for ts, lines in self._ts_idx.items():
                if t0 <= ts <= t1:
                    s.update(lines)
            filters.append(s)
        for fval, idx in [(q.robot_filter, self._robot_idx),
                          (q.task_filter, self._task_idx),
                          (q.path_filter, self._path_idx)]:
            if fval:
                s = set()
                for key, lines in idx.items():
                    if fval in key:
                        s.update(lines)
                filters.append(s)
        if q.error_only: filters.append(set(self._err_lines))

        if filters:
            cand = filters[0]
            for s in filters[1:]: cand = cand & s
        else:
            cand = set(self._err_lines[:q.max_results])

        if not cand:
            # 车号过滤器本身是否命中索引（与「车号∩任务∩错误∩时间」交集为空区分开）
            robot_keys = []
            if q.robot_filter:
                robot_keys = [k for k in self._robot_idx if q.robot_filter in k]
            if q.robot_filter and not robot_keys:
                known = ",".join(list(self._robot_idx)[:8]) or "none"
                return "(no match: robot={} 不在本日志; known={})".format(q.robot_filter, known)
            # 交集为空：放宽为时间窗内该车（或全窗）行，避免假报「车不在日志」
            if t0 and t1:
                for ts, lines in self._ts_idx.items():
                    if t0 <= ts <= t1:
                        cand.update(lines)
                if robot_keys:
                    rob_lines = set()
                    for k in robot_keys:
                        rob_lines.update(self._robot_idx.get(k) or [])
                    narrowed = cand & rob_lines
                    if narrowed:
                        cand = narrowed
            if not cand:
                cand = set(range(1, min(self._total + 1, 200)))
            if not cand:
                return "(no match: time={}~{} robot={} task={})".format(
                    q.time_start, q.time_end, q.robot_filter, q.task_filter)

        # 关键词过滤：优先在 cand 范围内找命中行；若被时间窗挤掉(交集空)但全集有命中，
        # 则退化为"error_only 全集命中"，避免编排 LLM 误判"查不到该错误"。
        if q.keyword:
            kw_lines = set()
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                for cur, line in enumerate(fh, 1):
                    if cur in cand and q.keyword in line:
                        kw_lines.add(cur)
            if not kw_lines:
                # 退化：在整个错误/信号范围扫同关键词（时间窗可能不是该错误的密集时段）
                scope = set(self._err_lines) if q.error_only else None
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                    for cur, line in enumerate(fh, 1):
                        if (scope is None or cur in scope) and q.keyword in line:
                            kw_lines.add(cur)
                        if len(kw_lines) >= 3000:
                            break
            cand = kw_lines
            if not cand:
                return "(no match: keyword={} robot={} task={} time={}~{})".format(
                    q.keyword, q.robot_filter, q.task_filter,
                    q.time_start or "-", q.time_end or "-")

        # 优先取信号行 + 尾行，再加上下文
        all_cand = sorted(cand)
        priority = [ln for ln in all_cand if ln in self._signal_lines]
        rest = [ln for ln in all_cand if ln not in priority]
        half = q.max_results // 2
        selected = priority[:half] + rest[:half] + (rest[-half:] if len(rest) > half else [])

        ctx = set(selected[:q.max_results * 2])
        for ln in list(ctx):
            for d in range(1, q.context_before+1): ctx.add(max(1, ln-d))
            for d in range(1, q.context_after+1): ctx.add(min(self._total, ln+d))

        sorted_ln = sorted(ctx)
        targets = set(sorted_ln[:300])

        results = []
        cur = 0
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                cur += 1
                if cur in targets:
                    fld = extract_fields(line)
                    sm = fields_summary(fld, line)
                    pf = "* " if cur in cand else "  "
                    results.append("{}L{}| {}".format(pf, cur, sm))
                    if len(results) >= 200:
                        break

        signal_cnt = sum(1 for ln in cand if ln in self._signal_lines)
        hdr = "log: {} | matched {} lines ({} 含关键信号:一致性/MAPF-T/ABORTED)".format(
            os.path.basename(self.log_path), len(cand), signal_cnt)
        if q.robot_filter: hdr += " | robot: {}".format(q.robot_filter)
        if q.task_filter: hdr += " | task: {}".format(q.task_filter)
        if q.time_start: hdr += " | time: {}~{}".format(q.time_start, q.time_end)
        if q.keyword: hdr += " | keyword: {}".format(q.keyword)
        return hdr + "\n\n" + "\n".join(results)


