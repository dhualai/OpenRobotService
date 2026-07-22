"""算法日志解析器 — AGV 调度算法日志的结构化索引与查询

算法日志特点:
    28万行+，单行可达6MB(嵌入Python repr对象序列化)
    有效信息(时间戳/车辆/路径/错误码) <1%

工程师排查流程: 定位时间窗口 → 过滤车辆/任务 → 追踪路径

策略:
    1. 结构化提取: ts/robot/task/path/error/node/pos/index
    2. 索引: 流式扫描→时间/车辆/任务/路径索引，首次30秒，后续毫秒查询
    3. 按查询条件读取候选行→人类可读摘要(≤200字/行)
"""

import re, os, time as _time
from typing import Optional, Dict, List


# ── 字段提取 ──

_RE_TS = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
_RE_LVL = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - (\w+) - ")
_RE_ROBOT = re.compile(r"(?:robot_id[=:]\s*'?)([A-Z]+[-_][A-Z]*[-_]?\d+)", re.IGNORECASE)
_RE_ROBOT2 = re.compile(r"Robot:([A-Z]+[-_][A-Z]*[-_]?\d+)", re.IGNORECASE)
_RE_TASK = re.compile(r"(?:task_id[=:]\s*'?)(\d{10,})", re.IGNORECASE)
_RE_TASK2 = re.compile(r"Task Node Id:(\d+)")
_RE_TASK3 = re.compile(r"taskNodeId:(\d+)")
_RE_ERROR = re.compile(r"error_code[=:]\s*'([^']+)'")
_RE_PATH = re.compile(r"Path:([A-Z]+[-_]\w+_\d+_\d+_\d+)", re.IGNORECASE)
_RE_NODE = re.compile(r"Node:(\d+)")
_RE_POS = re.compile(r"Pos:\[([^\]]+)\]")
_RE_IDX = re.compile(r"Index:(\d+)")
_RE_DESC = re.compile(r"description[=:]\s*'([^']+)'")
_RE_NUMNODES = re.compile(r"Num Of Node:(\d+)")


def extract_fields(line: str) -> Dict:
    fld = {}
    m = _RE_TS.search(line)
    if m: fld["ts"] = m.group(1)
    m = _RE_LVL.search(line)
    if m: fld["level"] = m.group(1)

    robots = set()
    for m in _RE_ROBOT.finditer(line): robots.add(m.group(1))
    for m in _RE_ROBOT2.finditer(line): robots.add(m.group(1))
    if robots: fld["robots"] = sorted(robots)

    tasks = set()
    for m in _RE_TASK.finditer(line): tasks.add(m.group(1))
    for m in _RE_TASK2.finditer(line): tasks.add(m.group(1))
    for m in _RE_TASK3.finditer(line): tasks.add(m.group(1))
    if tasks: fld["tasks"] = sorted(tasks)

    m = _RE_ERROR.search(line)
    if m: fld["error"] = m.group(1)

    paths = set()
    for m in _RE_PATH.finditer(line): paths.add(m.group(1))
    if paths: fld["paths"] = sorted(paths)

    for k, p in [("node", _RE_NODE), ("pos", _RE_POS), ("idx", _RE_IDX),
                  ("numnodes", _RE_NUMNODES)]:
        m = p.search(line)
        if m: fld[k] = m.group(1)

    m = _RE_DESC.search(line)
    if m: fld["desc"] = m.group(1)[:100]
    return fld


def fields_summary(fld: Dict) -> str:
    parts = []
    ts = fld.get("ts", "")
    if ts: parts.append("[{}]".format(ts.split(".")[0][-8:]))
    lv = fld.get("level", "")
    if lv: parts.append("[{}]".format(lv))
    for k, pfx in [("robots","R"), ("tasks","T"), ("paths","P")]:
        if k in fld:
            for v in fld[k]:
                parts.append("{}={}".format(pfx, v[-24:] if k=="paths" else v[-16:]))
    if "error" in fld: parts.append("ERR={}".format(fld["error"]))
    extra = []
    if "desc" in fld: extra.append(fld["desc"][:80])
    if "node" in fld: extra.append("N={}".format(fld["node"]))
    if "pos" in fld: extra.append("Pos=[{}]".format(fld["pos"]))
    if "idx" in fld: extra.append("#{}".format(fld["idx"]))
    if "numnodes" in fld: extra.append("(/{} nodes)".format(fld["numnodes"]))
    if extra: parts.append("| "+" ".join(extra))
    return " ".join(parts)[:200]


# ── 查询 + 索引 ──

class LogQuery:
    def __init__(self, time_start=None, time_end=None, robot_filter=None,
                 task_filter=None, path_filter=None, error_only=False,
                 context_before=2, context_after=2, max_results=200):
        self.time_start = time_start
        self.time_end = time_end
        self.robot_filter = robot_filter
        self.task_filter = task_filter
        self.path_filter = path_filter
        self.error_only = error_only
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
        self._err_lines = []    # error/warn line numbers
        self._total = 0
        self._built = False

    def build(self) -> "LogIndex":
        t0 = _time.perf_counter()
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                self._total = n
                fld = extract_fields(line)
                ts = fld.get("ts", "")
                if ts: self._ts_idx.setdefault(ts[:19], []).append(n)
                for r in fld.get("robots", []): self._robot_idx.setdefault(r, []).append(n)
                for t in fld.get("tasks", []): self._task_idx.setdefault(t, []).append(n)
                for p in fld.get("paths", []): self._path_idx.setdefault(p, []).append(n)
                if fld.get("level") in ("ERROR","WARN","FATAL") or "error" in fld:
                    self._err_lines.append(n)
                if n % 50000 == 0:
                    print("  [idx] {:,} lines ({:.0f}s)".format(n, _time.perf_counter()-t0))
        elapsed = _time.perf_counter() - t0
        print("  [idx] {:,} lines | {} ts | {} robots | {} tasks | {} errors ({:.0f}s)"
              .format(self._total, len(self._ts_idx), len(self._robot_idx),
                      len(self._task_idx), len(self._err_lines), elapsed))
        self._built = True
        return self

    def query(self, q: LogQuery) -> str:
        if not self._built: self.build()

        filters = []
        if q.time_start and q.time_end:
            s = set()
            for ts, lines in self._ts_idx.items():
                if q.time_start <= ts <= q.time_end:
                    s.update(lines)
            filters.append(s)
        for fval, idx in [(q.robot_filter, self._robot_idx),
                          (q.task_filter, self._task_idx),
                          (q.path_filter, self._path_idx)]:
            if fval:
                s = set(idx.get(fval, []))
                if s: filters.append(s)
        if q.error_only: filters.append(set(self._err_lines))

        if filters:
            cand = filters[0]
            for s in filters[1:]: cand = cand & s
        else:
            cand = set(self._err_lines[:q.max_results])

        if not cand:
            return "(no match: time={}~{} robot={} task={})".format(
                q.time_start, q.time_end, q.robot_filter, q.task_filter)

        ctx = set(cand)
        for ln in list(cand)[:q.max_results]:
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
                    sm = fields_summary(fld)
                    pf = "* " if cur in cand else "  "
                    results.append("{}L{}| {}".format(pf, cur, sm))
                    if len(results) >= 200:
                        break

        hdr = "log: {} | matched {} lines".format(os.path.basename(self.log_path), len(cand))
        if q.robot_filter: hdr += " | robot: {}".format(q.robot_filter)
        if q.task_filter: hdr += " | task: {}".format(q.task_filter)
        if q.time_start: hdr += " | time: {}~{}".format(q.time_start, q.time_end)
        return hdr + "\n\n" + "\n".join(results)


# ── Task Agent pipeline 集成入口 ──

def parse_log_for_diagnosis(log_path, time_start=None, time_end=None,
                            robot_filter=None, task_filter=None,
                            path_filter=None, error_only=True,
                            context_lines=2, max_results=100) -> str:
    """解析算法日志，返回 ≤2000 字诊断文本。
    首次调用建索引(30秒)，后续查询毫秒级。
    """
    q = LogQuery(
        time_start=time_start, time_end=time_end,
        robot_filter=robot_filter, task_filter=task_filter,
        path_filter=path_filter, error_only=error_only,
        context_before=context_lines, context_after=context_lines,
        max_results=max_results,
    )
    idx = LogIndex(log_path).build()
    result = idx.query(q)
    return result[:2000]
