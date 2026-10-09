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
  - token 收敛：查询回灌放得进窗口就用原文，顶到窗口才压缩；超宽查询仍提示缩窄
"""

import json, re, os, time as _time
import asyncio
from pathlib import Path
from typing import Optional, Dict, List

from ai.config import get_ai_config
from ai.core import get_llm_client
from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.log_analyzer.indexer import (
    LogIndex, LogQuery,
)

logger = get_logger("TASK_AGENT")

# ── 算法日志手册：路由总表 + 按当前文件分流注入 ──────────────────
_ALGO_MANUAL_FALLBACKS = (
    Path(r"D:/CodeHub/Algorithm/help_manuals/USP日志分析指南"),
    Path("/data/apps/OpenRobotService_Data/help_manuals/USP日志分析指南"),
)

# 手册原文关键串（usp-services 场景 6.1 / 正常全链路）——预检索与对账用
_HANDBOOK_NO_PATH_SEEDS = (
    "Locate Robot", "[LOCATE SUCCESS]", "[LOCATE FAILED]", "[LOCATE ERROR]",
    "新任务-----", "路径规划开始", "路径规划结束",
    "求解成功", "DMAP已接收路径", "路径拒收",
    "接收到TMS路径", "机器人不在当前所有地图层上",
    "更新机器人数据---", "动态地图Actor获取失败",
)

# 症状 → 先查哪份算法日志（始终注入，方便 AI 定位）
_ALGO_LOG_ROUTER = """## 算法日志先查哪份（usp-services；文件名即 Actor，切勿混用）

| 日志文件前缀 | 负责什么 | 症状优先查这里 | 不负责 |
|---|---|---|---|
| `DYNAMIC_MAP-USPA-LOGS-` | 车态刷新、上轨 locate、收 TMS 路径 / 拒收 | 车不动、路径规划中、没有路径下发、上轨失败、路径拒收 | 不算路、不分配任务 |
| `TMS-MAP-{map_id}-USPA-LOGS-` | **该地图**上的新任务感知、路径规划开始/结束、MAPF 求解、发给 DMAP | 有任务无路径、规划失败/超时、无解、求解成功对账 | 不含其它地图规划原文 |
| `TMS-USPA-LOGS-`（无 MAP-） | 通用 TMS 进程心跳 / SERVICES-ALIVE | 仅证明进程活着 | **几乎没有** `新任务-----` / `路径规划开始`；路径规划中勿当主证据 |
| `TASK-MANAGER-USPA-LOGS-` | 任务池、候选车、分配/充电/休息 | 任务分配不出去、池空、无可用车辆 | 不含 MAPF 求解 |
| `MapPreprocess-USPA-LOGS-` | 地图预处理缓存 | 地图加载慢、预处理卡住 | — |
| `AI_map-USPA-LOGS-` | AI 生成/障碍物 | AI 地图功能异常 | — |

**选文件规则**：先按上表选第一份；本文件只验证本 Actor 环节。证据不够 → `conclude.need_feed` 写**下一份真实文件名或前缀**（如 `TMS-MAP-DK`），禁止在错误文件里硬猜。
切文件 / need_feed / 二次拉取一律写服务名 `TMS-MAP-{map_id}`（`TMS-MAP-` + 地图取值，如 `TMS-MAP-DK`）。

**地图字段怎么叫（不要混用）**

| 场景 | 用这个名字 |
|---|---|
| 程序变量、need_feed、日志目录、Ray Actor | `map_id`；服务名 = `TMS-MAP-` + 该值 |
| DMAP 原文 `更新机器人数据---`、后端 JSON `position` | 只搜 `'mapId'`（驼峰，报文原字段） |

从原文抽出的取值赋给内部 `map_id`，再拼 `TMS-MAP-{map_id}`。禁止 need_feed 写 `mapId`。
dynamic-map **算法库**几乎无结构化日志；上轨/刷新/收路径都看 `DYNAMIC_MAP` 服务日志。现行规划以 **MAPF**（`求解成功---`）为主。

### 任务 ID 怎么查（必读，禁止只搜业务单号）
同一业务任务在日志里会对应**多种 ID**，必须全部纳入 keyword/grep，缺一种就换词再搜，禁止「搜业务号 0 命中」就断定无规划日志：

| 名称 | 常见出处 | 日志里怎么出现 |
|---|---|---|
| 业务任务号 | 界面/工单/用户说的「任务 125502」 | 可能出现在任务池、外设回传；**算法规划行里经常没有** |
| 原子任务 / 子任务 / task_node_id | 调度拆出来的节点任务 | `新任务：{task_node_id}`、`新任务-----车:{task_node_id}`、`Task Node Id:` |
| 车号 | agv-0014 | 与上两项组合搜：车号 + 时间窗 + 规划关键词 |
| path_id | 路径实例 | 收路径 / 拒收 / 释放相关 |

**推荐搜法**：① 先用业务号捞「任务池/分配」看有没有原子任务号；② 用原子任务号 + 车号追 `新任务-----` / `路径规划开始` / `求解成功`；③ 业务号搜空时**不得**写「三份都没有该任务规划」，应写清「业务号未命中，需原子任务号或已用车号+时间对账结果」。

### 两种「新任务」必须分清（禁止混搜、禁止一律切 TMS-MAP）

| 日志原文 | 文件 | 含义 |
|---|---|---|
| `Robot:{车}----新任务：{task_node_id}---` | **DYNAMIC_MAP** | 车态刷新时 `task_node_id` 变化。现场**指定车直接下发**时，任务挂在车上，**会打这行，但不会进 TASK-MANAGER 分配，也不必然进 TMS-MAP** |
| `新任务-----{车}:{task_node_id}` | **TMS-MAP-{map_id}** | TMS 规划器感知到「尚未算路过的 task_node」（且非 EVADE）。只有这条才表示该任务进入了地图规划循环 |
| `新移动单：{path_id}` | DYNAMIC_MAP | 车上已有 pathId，不是「还在等规划」 |

现场直接指定车辆下发移动任务：任务**不进任务池 / 不经 TASK-MANAGER 选车**，TMS-MAP 可能根本没有 `新任务-----`。此时主证据在 DYNAMIC_MAP：`新任务：` + `currentTaskState`（pathId / taskNodeId / 任务类型）。不得因为 TMS-MAP 搜空就写成「规划器没收到任务」。

先看工单/对话是否写了「指定车 / 直接下发 / 给 agv-xxxx 下任务」。有则先核 DMAP 的 `新任务：` 是什么（业务移动 / EVADE / 充电休息），再决定要不要切 TMS-MAP。

### 正常全链路（缺哪环停哪环）
`[DMAP] 收到机器人数据 → 更新机器人数据--- → Locate → [LOCATE SUCCESS]`
指定车直发：`[DMAP] Robot:车----新任务：task_node---`（此时 **不必** 有 TMS `新任务-----`）
池选车/需算法规划：`[TMS] 新任务-----车:任务节点 → 路径规划开始 → 求解成功--- → 路径规划结束 → DMAP已接收路径`
`[DMAP] 接收到TMS路径 → 路径校验通过---可更新`
"""

# 按当前日志模块注入的深挖指引（只服务本文件）
_MODULE_PLAYBOOKS = {
    "dynamic_map": """### 本文件：DYNAMIC_MAP — 只查刷新 / 上轨 / 收路径 / 车上新任务
「路径规划中」时程序已跑缺环表；请以「缺环判定」为结论起点，用摘录核对，勿另起炉灶扫无关词。
本轮 keyword 优先：`更新机器人数据---` `Robot:{车}----新任务：` `新移动单：` `Locate Robot: {车}` `[LOCATE SUCCESS]` `[LOCATE FAILED]` `[LOCATE ERROR]` `机器人不在当前所有地图层上` `接收到TMS路径` `路径校验通过---可更新`。
1) 上轨 FAILED/ERROR / 不在地图层 → 停在此，勿跳规划。
2) 先核 `新任务：` 与 `新任务-----` 不是同一句话：前者只说明车上挂了 task_node。现场指定车直发时**只有前者**，不得因后者缺失就切 TMS-MAP。
3) 已见 `新任务：` 且 pathId=None：看任务类型（EVADE/充电/休息 vs 业务移动）。仅业务移动且工单不是指定车直发时，才 need_feed **TMS-MAP-{map_id}**。
4) 禁止在本文件搜任务池/匈牙利分配（那是 TASK-MANAGER）。指定车直发也不进 TASK-MANAGER。
""",
    "tms": """### 本文件：通用 TMS（非 TMS-MAP）— 多为心跳壳
本文件名是 `TMS-USPA-LOGS-`（中间没有 `MAP-`），通常只有 SERVICES-ALIVE，**没有**地图侧规划原文。
「路径规划中」时：若缺环判定已标心跳壳 → need_feed 写 **TMS-MAP-{map_id}**（有 map_id 必带，即服务名 `TMS-MAP-`+map_id），不要在本文件硬搜 `路径规划开始`。
禁止把心跳当「没规划」的证据；禁止在本文件做 locate 深挖。
""",
    "tms_map": """### 本文件：TMS-MAP-{map_id} — 只查该图规划器感知的新任务 / 规划 / 求解 / 下发回传
「路径规划中」时程序已跑缺环表；以「缺环判定」为起点。
本轮 keyword 优先：`新任务-----{车}` `路径规划开始-----` `求解成功---` `路径规划无解` `路径规划结束----` `DMAP已接收路径` `路径拒收---` `动态地图Actor获取失败`。
1) 无 `新任务-----`：先区分是不是「指定车直发」（任务未进规划器属预期）。改搜车号仍无时，回 DYNAMIC_MAP 核 `新任务：` 与任务类型，**不要默认 TASK-MANAGER**（指定车也不进任务池）。
2) 有 `新任务-----` 无 `路径规划开始` → 触发条件不满足（无终点 / path 未绑 task_node / 车辆更新中 / EVADE 被过滤）。
3) 求解成功后 `路径拒收` → need_feed **DYNAMIC_MAP**。
4) 禁止在本文件做 locate 深挖（上轨在 DYNAMIC_MAP）。
5) 若文件名地图与快照 map_id 不一致 → need_feed 正确的 `TMS-MAP-{map_id}`。
""",
    "task_manager": """### 本文件：TASK-MANAGER — 只查任务池 / 候选车 / 分配
「路径规划中」时程序已跑缺环表；心跳刷屏不是根因。
本轮 keyword 优先：`任务池信息更新` `开始任务分配` `任务分配结果` `任务分配成功` `无可用车辆` `状态异常，不可分配`。
1) 现场指定车直接下发：**不会进本文件**。本文件搜空是预期，回 DYNAMIC_MAP 看 `新任务：`。
2) 无可用车辆/状态异常 → 停在分配，need_feed **DYNAMIC_MAP** 核上轨。
3) 有分配结果 → need_feed **TMS-MAP-{map_id}** 查 `新任务-----`（规划器感知，不是 DMAP 的 `新任务：`）。
4) 仅休息/充电心跳 → need_feed DYNAMIC_MAP，勿用心跳编根因。
""",
    "map_preprocess": """### 本文件：MapPreprocess — 只查预处理
keyword：`预处理开始` `开始基础预处理` `基础预处理完成` `地图无需预处理` `预处理失败`。
卡住/失败停在本文件；与「路径规划中」无直接关系时 need_feed DYNAMIC_MAP/TMS。
""",
    "ai_map": """### 本文件：AI_map — 只查 AI 生成链路
按手册查进度/异常；与调度路径规划中无关时 need_feed DYNAMIC_MAP/TMS。
""",
}

_MODULE_MANUAL_EXCERPT = {
    "dynamic_map": (
        "算法模块/usp-services日志分析指南.md",
        ("### 场景 6.1", "## 六、故障场景", "## 二、核心控制流：一次机器人状态刷新"),
    ),
    "tms": (
        "算法模块/usp-services日志分析指南.md",
        ("## 三、TMS 规划循环", "### 场景 6.1", "## 六、故障场景"),
    ),
    "tms_map": (
        "算法模块/usp-services日志分析指南.md",
        ("## 三、TMS 规划循环", "### 场景 6.1", "## 六、故障场景"),
    ),
    "task_manager": (
        "算法模块/usp-algorithm-task-manager日志分析指南.md",
        ("### 场景 5.1", "## 五、故障场景", "## 二、核心控制流"),
    ),
    "map_preprocess": (
        "算法模块/usp-services日志分析指南.md",
        ("### 场景 6.3", "## 六、故障场景"),
    ),
}


_MODULE_ROLE_CN = {
    "dynamic_map": "车态刷新 / 上轨 / 收路径拒收（不算路）",
    "tms_map": "该地图路径规划原文（新任务 / 规划开始 / MAPF 求解）",
    "tms": "通用 TMS 心跳壳（通常无规划原文，勿当主证据）",
    "task_manager": "任务池 / 候选车 / 分配（不含求解）",
    "map_preprocess": "地图预处理",
    "ai_map": "AI 地图 / 障碍物",
    "unknown": "未能从文件名或正文识别 Actor",
}

# 上传 zip 常叫 debug_logs.log：路径认不出时用正文指纹
_SNIFF_MARKERS: Dict[str, tuple] = {
    "dynamic_map": (
        "更新机器人数据---", "Locate Robot", "[LOCATE SUCCESS]",
        "接收到TMS路径", "路径校验通过---可更新", "新任务：",
    ),
    "tms_map": (
        "路径规划开始-----", "求解成功---", "新任务-----",
        "路径规划结束----", "DMAP已接收路径",
    ),
    "task_manager": (
        "任务池信息更新", "开始任务分配", "任务分配结果",
        "无可用车辆", "状态异常，不可分配",
    ),
    "map_preprocess": (
        "开始基础预处理", "基础预处理完成", "地图无需预处理", "预处理失败",
    ),
    "ai_map": (
        "AIMap", "AI_map", "障碍物生成",
    ),
}
_SNIFF_STRONG = {
    "dynamic_map": ("[LOCATE SUCCESS]", "接收到TMS路径", "更新机器人数据---"),
    "tms_map": ("路径规划开始-----", "求解成功---", "新任务-----"),
    "task_manager": ("任务池信息更新", "开始任务分配"),
    "map_preprocess": ("开始基础预处理", "基础预处理完成"),
}

_PLANNING_FILE_ORDER = (
    "dynamic_map", "tms_map", "tms", "task_manager",
    "map_preprocess", "ai_map", "unknown",
)


def _detect_algo_log_module_from_path(log_path: str, name_hint: str = "") -> str:
    """只看路径/文件名。完整路径里的 TMS-MAP- 优先于通用 TMS。"""
    blob = " ".join([(log_path or ""), (name_hint or "")]).replace("\\", "/").upper()
    if "DYNAMIC_MAP" in blob:
        return "dynamic_map"
    if "TASK-MANAGER" in blob or "TASK_MANAGER" in blob:
        return "task_manager"
    if "TMS-MAP-" in blob or "TMS_MAP_" in blob:
        return "tms_map"
    if "TMS" in blob:
        return "tms"
    if "MAPPR" in blob or "PREPROCESS" in blob or "MAP_PREPROCESS" in blob:
        return "map_preprocess"
    if "AI_MAP" in blob or "AI-MAP" in blob:
        return "ai_map"
    return "unknown"


def _read_log_sniff_sample(log_path: str, limit: int = 400_000) -> str:
    p = Path(log_path or "")
    if not p.is_file():
        return ""
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            head = f.read(min(limit, size))
            tail = b""
            if size > limit + 80_000:
                f.seek(max(0, size - 80_000))
                tail = f.read(80_000)
        text = head.decode("utf-8", errors="replace")
        if tail:
            text += "\n" + tail.decode("utf-8", errors="replace")
        return text
    except Exception:
        return ""


def _sniff_algo_log_module(log_path: str) -> str:
    """用正文指纹认 Actor；上传 zip 内 debug_logs.log 无服务名前缀时用这个。"""
    text = _read_log_sniff_sample(log_path)
    if not text:
        return "unknown"
    scores: Dict[str, int] = {}
    for mod, markers in _SNIFF_MARKERS.items():
        n = sum(1 for m in markers if m in text)
        if any(s in text for s in _SNIFF_STRONG.get(mod, ())):
            n += 3
        scores[mod] = n
    if "新任务-----" not in text and scores.get("tms_map", 0):
        if "路径规划开始-----" not in text and "求解成功---" not in text:
            scores["tms_map"] = 0
    best_mod, best = "unknown", 0
    for mod, n in scores.items():
        if n > best:
            best, best_mod = n, mod
    if best <= 0:
        if "SERVICES-ALIVE" in text and "路径规划开始-----" not in text:
            return "tms"
        return "unknown"
    if best_mod == "dynamic_map" and scores.get("tms_map", 0) >= scores["dynamic_map"]:
        return "tms_map"
    return best_mod


def _detect_algo_log_module(log_path: str, name_hint: str = "") -> str:
    """判定算法 Actor：路径/文件名优先，认不出再扫正文。"""
    by_name = _detect_algo_log_module_from_path(log_path, name_hint)
    if by_name != "unknown":
        return by_name
    return _sniff_algo_log_module(log_path)


def order_logs_for_analysis(
    log_files: Optional[List[str]],
    query: str = "",
    prefer_map_id: str = "",
) -> List[str]:
    """上传包 / 拉包共用：按问题 + Actor 排序，写入 runtime_ctx['log_paths']。"""
    files = [p for p in (log_files or []) if p and os.path.isfile(p)]
    if not files:
        return []
    try:
        from ai.agents.AiTaskPlatform.server_pull.usp_log_puller import select_log_for_query
        files = select_log_for_query(files, query or "", prefer_map_id=prefer_map_id or "")
    except Exception:
        files = list(files)
    idx = {m: i for i, m in enumerate(_PLANNING_FILE_ORDER)}
    q = query or ""
    stuck = any(
        m in q
        for m in (
            "路径规划中", "规划中", "卡在规划", "一直规划", "规划卡住",
            "没有路径下发", "机器人不动", "车不动",
        )
    )
    if stuck:
        files = sorted(
            files,
            key=lambda p: (idx.get(_detect_algo_log_module(p), 9), Path(p).name),
        )
    return files


def describe_log_file_role(log_path: str) -> str:
    """一句话说明该日志文件负责什么。"""
    module = _detect_algo_log_module(log_path)
    p = Path(log_path or "")
    name = p.name
    parent = p.parent.name
    display = f"{parent}/{name}" if name.lower() in ("debug_logs.log", "debug.log", "log.txt") and parent else name
    role = _MODULE_ROLE_CN.get(module, _MODULE_ROLE_CN["unknown"])
    return f"`{display}` → [{module}] {role}"


def format_available_log_catalog(log_paths: Optional[List[str]]) -> str:
    """把本轮已拉到的日志列成角色目录，供切换时对照。"""
    paths = [p for p in (log_paths or []) if p]
    if not paths:
        return ""
    lines = ["## 本轮可用的算法日志（按角色；need_feed 必须从这里选或写同前缀）"]
    for p in paths:
        lines.append(f"- {describe_log_file_role(p)}")
    lines.append(
        "切换规则：当前文件只验证本 Actor；缺环时 need_feed 写目录中下一份的文件名/前缀，"
        "程序会按 need_feed 自动换文件。压缩包内同名 debug_logs.log 以目录名或正文指纹区分。"
        "包里没有 TMS-MAP-{map_id} 且非指定车直发时，会再拉一次该文件。"
    )
    return "\n".join(lines)


def match_log_path_for_need_feed(
    need_feed: str,
    candidates: List[str],
    *,
    exclude: Optional[set] = None,
) -> Optional[str]:
    """按 need_feed 文案从候选路径里挑下一份最匹配的日志。"""
    hint = (need_feed or "").strip().upper()
    if not hint or not candidates:
        return None
    skip = exclude or set()

    def _norm_key(p: str) -> str:
        return os.path.normcase(os.path.abspath(p)) if os.path.isabs(p) else os.path.normcase(p)

    remaining = [p for p in candidates if _norm_key(p) not in skip and os.path.isfile(p)]
    if not remaining:
        return None

    # 精确 / 前缀命中（TMS-MAP-DK 优先于笼统 TMS）
    scored: List[tuple] = []
    for p in remaining:
        blob = p.replace("\\", "/").upper()
        name = Path(p).name.upper()
        module = _detect_algo_log_module(p)
        score = 0
        if name in hint or hint in name or hint in blob:
            score += 100
        for token in (
            "TMS-MAP-", "DYNAMIC_MAP", "TASK-MANAGER", "MAPPREPROCESS",
            "AI_MAP", "TMS-",
        ):
            if token in hint and token in blob:
                score += 40
                break
        if "TMS-MAP" in hint and module == "tms_map":
            score += 50
            m = re.search(r"TMS-MAP-([A-Z0-9_\-]+?)(?:-USPA|$|\s|/)", hint)
            if m and f"TMS-MAP-{m.group(1)}" in blob:
                score += 80
        if "DYNAMIC_MAP" in hint and module == "dynamic_map":
            score += 50
        if "TASK-MANAGER" in hint and module == "task_manager":
            score += 50
        if re.search(r"\bTMS\b", hint) and "TMS-MAP" not in hint:
            if module == "tms_map":
                score += 35
            elif module == "tms":
                score += 5
        if score > 0:
            scored.append((score, p))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], Path(x[1]).name))
    return scored[0][1]


def tms_map_need_feed_needle(need_feed: str) -> Optional[str]:
    """need_feed 若在要 TMS-MAP 规划原文，返回检索针 `TMS-MAP-DK` 或 `TMS-MAP-`。

    不含 TMS-MAP 则返回 None（不触发二次拉取）。
    """
    hint = (need_feed or "").strip().upper()
    if "TMS-MAP" not in hint:
        return None
    if "{MAP_ID}" in hint or re.search(r"TMS-MAP-\*|TMS-MAP-\s*$", hint):
        return "TMS-MAP-"
    m = re.search(r"TMS-MAP-([A-Z0-9]+(?:-[A-Z0-9]+)*?)-USPA", hint)
    if m:
        return f"TMS-MAP-{m.group(1)}"
    m = re.search(r"TMS-MAP-([A-Z0-9]+)\b", hint)
    if m:
        return f"TMS-MAP-{m.group(1)}"
    return "TMS-MAP-"


def _resolve_usp_manual_root() -> Optional[Path]:
    """解析 USP 日志手册根目录：LOG_MANUALS 注册表 → 本地 Algorithm 路径。"""
    try:
        from ai.agents.AiTaskPlatform.product_registry import pick_manual_dir, resolve_product_dir
        d = pick_manual_dir("DYNAMIC_MAP-USPA-LOGS-")
        if d and Path(d).is_dir():
            return Path(d)
        products = get_ai_config().log_manuals or {}
        if "USP" in products:
            d2 = resolve_product_dir("USP", products)
            if d2 and Path(d2).is_dir():
                return Path(d2)
    except Exception:
        pass
    for p in _ALGO_MANUAL_FALLBACKS:
        if p.is_dir():
            return p
    return None


def _excerpt_manual(root: Path, rel: str, markers: tuple, limit: int = 1400) -> str:
    fp = root / rel
    if not fp.is_file():
        return ""
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    for marker in markers:
        i = text.find(marker)
        if i >= 0:
            return text[i:i + limit].strip()
    return text[:800].strip()


def _load_log_docs(log_path: str = "") -> str:
    """注入：①算法日志路由总表 ②当前文件对应模块 playbook ③手册摘录。

    不同日志文件注入不同深挖指引，避免 DYNAMIC_MAP 轮灌 TMS/分配细节。
    """
    root = _resolve_usp_manual_root()
    module = _detect_algo_log_module(log_path)
    parts: List[str] = [_ALGO_LOG_ROUTER, ""]

    playbook = _MODULE_PLAYBOOKS.get(module)
    if playbook:
        parts.append(f"## 当前模块深挖（{module}）")
        parts.append(playbook)
    else:
        parts.append("## 当前模块深挖")
        parts.append(
            f"未能从文件名或正文识别 Actor（path=`{Path(log_path).name}`）。"
            "先对照上方路由表判断应查哪份；本文件只取与现象直接相关的原文，"
            "不够则 need_feed 换 DYNAMIC_MAP / TMS-MAP / TMS / TASK-MANAGER。"
        )
        parts.append("")

    if root and module in _MODULE_MANUAL_EXCERPT:
        rel, markers = _MODULE_MANUAL_EXCERPT[module]
        cut = _excerpt_manual(root, rel, markers)
        if cut:
            parts.append(f"### 手册摘录 · {Path(rel).name}")
            parts.append(cut)
            parts.append("")

    result = "\n".join(parts)
    if len(result) > 4200:
        result = result[:4200] + "\n\n(算法手册已截断)"
    if not root:
        result += (
            "\n\n(未找到 USP 手册目录；请配置 LOG_MANUALS 或放置到"
            " D:/CodeHub/Algorithm/help_manuals/USP日志分析指南)"
        )
    return result


# ── 客观事实注入 ─────────────────────────────────────────────

def _facts_to_text(facts: Dict) -> str:
    """把 LogIndex.discover_facts() 的骨架转成给 LLM 看的客观事实文本。"""
    if not facts:
        return "（无法提取日志事实）"

    lines = ["## 日志客观事实（只能在这些真实值中选择过滤条件，禁止虚构）"]
    lines.append(f"- 日志总行数: {facts.get('lines', '?')}")
    lines.append(f"- 真实 ERROR/WARN 行: {facts.get('errors', '?')}（不含「更新Robots」刷屏体内嵌的历史 error_code）")
    rs = facts.get("reach_signals")
    if rs:
        lines.append(f"- 可达性/求解相关信号行: {rs}（优先用 keyword 查 不可达/topo/求解/目标点）")

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


_RE_ROBOT_ID = re.compile(r"\b([A-Za-z]{2,6}[-_]\d{1,4}|agv-\d+)\b", re.I)
_RE_WHEN = re.compile(
    r"(?:(20\d{2})[-/])?(\d{1,2})[-/](\d{1,2})[ T日]*(\d{1,2}:\d{2})"
)
_RE_GOAL_NODE = re.compile(r"\b(\d{5,6})\b")
_SEED_REACH_WORDS = (
    # 手册：dynamic-map / MAPF / 可达性原文关键串
    "不可达", "可达", "求解", "无解", "topo", "拓扑", "路径规划",
    "unreachable", "NO_SOLUTION", "no solution", "NoSolutionFound",
    "原始地图单车路径无解", "TRAFFIC_LOCK", "blocked_edges", "障碍物",
    "MAPF", "MAPF-T", "下发路径", "路径下发",
    "路径规划失败", "路径规划超时", "路径规划异常", "路径规划结果为空",
    "LOCATE FAILED", "LOCATE ERROR", "LOCATE SUCCESS",
    "不在当前所有地图层上", "路径拒收", "DMAP已接收路径",
    "新任务-----", "求解成功", "非强连通",
)

# 现象命中「目标点不可达 / 路径无解」时，即使用户问题里没写这些词，也强制预扫
_REACH_PHENOMENON_MARKERS = (
    "不可达", "无解", "NO_SOLUTION", "路径规划失败", "路径无解",
    "目标点", "走不了", "无路径", "规划失败",
)
_REACH_ALWAYS_SEEDS = (
    "原始地图单车路径无解", "NO_SOLUTION", "TRAFFIC_LOCK", "blocked_edges",
    "MAPF", "路径规划失败", "障碍物",
)

# 「路径规划中 / 不动 / 无路径下发」：手册 usp-services 场景 6.1
_PLANNING_STUCK_MARKERS = (
    "路径规划中", "规划中", "卡在规划", "一直规划", "规划卡住",
    "没有路径", "无指引", "监控没有路径", "没有路径下发", "机器人不动",
    "车不动", "不下发",
)
_PLANNING_STUCK_SEEDS = _HANDBOOK_NO_PATH_SEEDS + (
    "MAPF", "MAPF-T", "求解成功", "NO_SOLUTION",
)

# 路径规划中 · 缺环对账（仅此类现象启用；不可达等其它问题不走这张表）
# 每项: (步骤名, 关键字模板里可用 {robot}, 信号类型 ok|bad|gate)
# gate=应有；ok=正向；bad=负向。程序按序扫，输出「停在哪一环」。
_PLANNING_CHAIN_STEPS: Dict[str, tuple] = {
    "dynamic_map": (
        ("车态刷新", "更新机器人数据---", "gate"),
        ("上轨调用", "Locate Robot: {robot}", "gate"),
        ("上轨成功", "[LOCATE SUCCESS]", "ok"),
        ("上轨失败", "[LOCATE FAILED]", "bad"),
        ("上轨异常", "[LOCATE ERROR]", "bad"),
        ("不在地图层", "机器人不在当前所有地图层上", "bad"),
        ("车上新任务", "新任务：", "gate"),
        ("新移动单", "新移动单：", "ok"),
        ("收到TMS路径", "接收到TMS路径", "ok"),
        ("路径校验通过", "路径校验通过---可更新", "ok"),
        ("路径校验失败", "路径校验失败", "bad"),
        ("更新拒绝", "本次更新拒绝", "bad"),
    ),
    "tms": (
        ("无定位点", "robot:{robot}---no cur node", "bad"),
        ("新任务感知", "新任务-----{robot}", "gate"),
        ("规划开始", "路径规划开始-----", "gate"),
        ("求解成功", "求解成功---", "ok"),
        ("规划无解", "路径规划无解", "bad"),
        ("规划结束", "路径规划结束----", "ok"),
        ("DMAP已接收", "DMAP已接收路径", "ok"),
        ("路径拒收", "路径拒收---", "bad"),
        ("Actor失败", "动态地图Actor获取失败", "bad"),
    ),
    "task_manager": (
        ("任务池更新", "任务池信息更新", "gate"),
        ("开始分配", "开始任务分配", "gate"),
        ("分配结果", "任务分配结果", "ok"),
        ("分配成功", "任务分配成功", "ok"),
        ("无可用车辆", "无可用车辆", "bad"),
        ("状态不可分配", "状态异常，不可分配", "bad"),
    ),
}
# 地图专属 TMS 与通用 TMS 共用缺环表；模块名不同以便选文件 / need_feed
_PLANNING_CHAIN_STEPS["tms_map"] = _PLANNING_CHAIN_STEPS["tms"]


def _is_planning_stuck(question: str = "", task: Optional[Dict] = None) -> bool:
    blob = " ".join(
        filter(
            None,
            [
                question or "",
                (task or {}).get("title") or "",
                (task or {}).get("problem_summary") or "",
                (task or {}).get("description") or "",
            ],
        )
    )
    low = blob.lower()
    return any(m.lower() in low or m in blob for m in _PLANNING_STUCK_MARKERS)


def _extract_robot_ids(question: str = "", task: Optional[Dict] = None) -> List[str]:
    blob = " ".join(
        filter(
            None,
            [
                question or "",
                (task or {}).get("title") or "",
                (task or {}).get("problem_summary") or "",
                (task or {}).get("description") or "",
            ],
        )
    )
    out: List[str] = []
    seen = set()
    for m in re.finditer(r"agv-\d+", blob, re.I):
        r = m.group(0).lower()
        if r not in seen:
            seen.add(r)
            out.append(r)
    for m in _RE_ROBOT_ID.finditer(blob):
        r = m.group(1)
        if r.lower() not in seen:
            seen.add(r.lower())
            out.append(r)
    return out


_SPECIFIED_ROBOT_MARKERS = (
    "指定车", "指定车辆", "直接下发", "现场下发", "给车下",
    "指定机器人", "点名", "绑车", "指定 agv", "指定agv",
)


def _is_specified_robot_dispatch(question: str = "", task: Optional[Dict] = None) -> bool:
    """现场指定车辆直接下发：任务不进 TASK-MANAGER，也不必然进 TMS-MAP。"""
    blob = " ".join(
        filter(
            None,
            [
                question or "",
                (task or {}).get("title") or "",
                (task or {}).get("problem_summary") or "",
                (task or {}).get("description") or "",
            ],
        )
    )
    low = blob.lower()
    return any(m.lower() in low or m in blob for m in _SPECIFIED_ROBOT_MARKERS)


def _verdict_planning_chain(
    module: str,
    hits: Dict[str, int],
    robot: str = "",
    extras: Optional[Dict] = None,
) -> tuple:
    """根据缺环命中表给出 (停在哪环, need_feed建议)。"""
    h = hits or {}
    extras = extras or {}

    def n(name: str) -> int:
        return int(h.get(name) or 0)

    if module == "dynamic_map":
        map_id = (extras.get("map_id") or "").strip()
        node_id = (extras.get("task_node_id") or "").strip()
        task_id = (extras.get("task_id") or "").strip()
        path_none = bool(extras.get("path_id_none"))
        live = bool(extras.get("from_live_probe"))
        tms_map_hint = f"TMS-MAP-{map_id}" if map_id else "TMS-MAP-{map_id}"
        live_tag = "【现场 Ray 快照】" if live else "DYNAMIC_MAP 日志"
        specified = bool(extras.get("specified_robot"))
        dmap_new = n("车上新任务") > 0
        has_move = n("新移动单") > 0
        task_type = (extras.get("task_type") or "").strip()

        if has_move and not path_none:
            return (
                f"{live_tag}已见 `新移动单`（车上已有 pathId），不是「等 TMS 规划」；"
                "若监控仍显示路径规划中，核对下发到车 / 收路径校验，不要切 TMS-MAP 找新任务-----",
                "",
            )

        if path_none and (task_id or node_id or dmap_new):
            detail = []
            if task_id:
                detail.append(f"业务任务={task_id}")
            if node_id:
                detail.append(f"task_node_id={node_id}")
            if map_id:
                detail.append(f"map_id={map_id}")
            if task_type:
                detail.append(f"任务类型={task_type}")
            if specified or dmap_new:
                extra = (
                    "现场指定车直发或 DMAP 已见 `新任务：`：任务已挂在车上，"
                    "**不会进 TASK-MANAGER**；TMS-MAP 的 `新任务-----` 可能本来就不会出现。"
                    "先核本文件 `新任务：` 后的 task_node 是什么（业务移动 vs EVADE/充电/休息），"
                    "不要因为 TMS-MAP 搜空就写「规划器没收到」。"
                )
                need = "" if specified else tms_map_hint
                if task_type.upper() in ("EVADE", "CHARGE", "REST", "CHARGING"):
                    need = ""
                    extra += f" 类型 `{task_type}` 规划器会过滤或走别的环，不必查 TMS-MAP 业务规划。"
                elif not specified:
                    extra += f" 若确认是业务移动且非指定车，再查 {tms_map_hint} 是否有 `新任务-----`。"
                    need = tms_map_hint
                return (
                    f"{live_tag}车上已挂任务但 pathId=None"
                    f"（{'; '.join(detail) or '有任务无路径'}）。{extra}",
                    need,
                )
            return (
                f"{live_tag}已见任务挂到车上，但 currentTaskState.pathId 一直为 None"
                f"（{'; '.join(detail) or '有任务无路径'}）——"
                "卡在「等规划下发」。先分清是指定车直发还是池分配；"
                f"仅后者才必须查 {tms_map_hint} 的 `新任务-----`（不是 DMAP 的 `新任务：`）",
                tms_map_hint,
            )
        if n("上轨失败") or n("上轨异常") or n("不在地图层"):
            return (
                "停在 DYNAMIC_MAP 上轨/定位（FAILED/ERROR/不在地图层）——规划器不会给无 cur_node 的车算路",
                "",
            )
        if n("车态刷新") and not n("上轨成功") and not n("上轨调用"):
            return (
                "有车态刷新，但未见 Locate 调用/成功；先从「更新机器人数据---」摘录核对"
                " tasks/currentTaskState.pathId/taskNodeId，以及原文 position.mapId（内部记为 map_id），再决定是否换 TMS-MAP-{map_id}",
                tms_map_hint,
            )
        if n("上轨调用") and not n("上轨成功") and not (n("上轨失败") or n("上轨异常")):
            return (
                "有 Locate 调用但未见 SUCCESS/FAILED 收口，继续在本文件核对上轨结果原文",
                "",
            )
        if n("上轨成功") and not n("收到TMS路径") and not n("路径校验通过"):
            if specified or dmap_new:
                return (
                    "上轨成功且车上已有 `新任务：`，未见接收到TMS路径。"
                    "指定车直发时 TMS-MAP 可能没有 `新任务-----`；先核本文件新任务类型与 pathId，"
                    "确认是业务移动且需要算法规划后再切 TMS-MAP",
                    "" if specified else tms_map_hint,
                )
            return (
                "上轨成功，但未见接收到TMS路径——本文件缺下发环；"
                "若非指定车直发，再换地图侧 TMS-MAP 查 `新任务-----`（规划器感知）",
                tms_map_hint,
            )
        if n("收到TMS路径") and (n("路径校验失败") or n("更新拒绝")):
            return (
                "停在 DYNAMIC_MAP 收路径校验（校验失败或更新拒绝）",
                "",
            )
        if n("路径校验通过") or n("收到TMS路径"):
            return (
                "DYNAMIC_MAP 已见收路径/校验通过；若监控仍显示路径规划中，核对是否下发到车或周期刷屏掩盖",
                "",
            )
        if not n("车态刷新") and not n("上轨调用"):
            # 有现场快照且 pathId=None 时，即使本文件命中少也优先信快照
            if live and path_none and (task_id or node_id):
                return (
                    f"{live_tag}确认有任务无路径；本日志文件时间窗命中偏少，"
                    f"仍应换 {tms_map_hint} 查规划原文",
                    tms_map_hint,
                )
            return (
                "本文件时间窗内几乎无该车刷新/上轨；核对时间窗与车号，或换 TASK-MANAGER/TMS-MAP",
                tms_map_hint,
            )
        return (
            "DYNAMIC_MAP 缺环对账未形成完整上轨→收路径链，需换 TMS-MAP 继续",
            tms_map_hint,
        )

    if module in ("tms", "tms_map"):
        map_id = (extras.get("map_id") or "").strip()
        hint = f"TMS-MAP-{map_id}" if map_id else "TMS-MAP-{map_id}"
        # 通用 TMS（无 MAP-）或心跳壳：规划原文不在此文件
        if module == "tms" or extras.get("tms_heartbeat_only"):
            if extras.get("specified_robot"):
                return (
                    "当前文件是通用 TMS 心跳壳。指定车直接下发不经本文件、也不必然经 TMS-MAP；"
                    "回 DYNAMIC_MAP 核 `新任务：` 与车上任务类型",
                    "DYNAMIC_MAP-USPA-LOGS",
                )
            return (
                "当前文件是通用 TMS（非 TMS-MAP），规划原文不在这里；"
                f"请换地图侧 `{hint}` 查 新任务-----/路径规划开始/求解成功，"
                "不要在心跳壳里硬搜规划词",
                hint,
            )
        if n("Actor失败"):
            return ("停在 TMS：动态地图 Actor 获取失败，无法规划", "")
        if n("无定位点") and not n("新任务感知"):
            return (
                "TMS 见该车 no cur node（无定位点），规划不会纳入；回 DYNAMIC_MAP 查 Locate",
                "DYNAMIC_MAP-USPA-LOGS",
            )
        if not n("新任务感知"):
            specified = bool(extras.get("specified_robot"))
            if specified:
                return (
                    "本文件已是 TMS-MAP，未见 `新任务-----{车}:task_node_id`。"
                    "工单为指定车直接下发时这是预期：任务不进规划器。"
                    "回 DYNAMIC_MAP 核 `Robot:车----新任务：` 后的 task_node 是什么，不要切 TASK-MANAGER",
                    "DYNAMIC_MAP-USPA-LOGS",
                )
            return (
                "本文件已是 TMS-MAP，仍未见 `新任务-----{车}:task_node_id`——"
                "先确认不是把 DMAP 的 `新任务：` 当成规划器感知；改搜车号。"
                "仍无则：指定车直发不进本文件属预期；池分配才换 TASK-MANAGER",
                "DYNAMIC_MAP-USPA-LOGS",
            )
        if n("新任务感知") and not n("规划开始"):
            return (
                "已感知新任务，但未见路径规划开始——触发条件未满足"
                "（无终点/path 未绑 task_node/车辆更新中等）",
                "",
            )
        if n("规划开始") and n("规划无解") and not n("求解成功"):
            return ("停在 TMS 求解：路径规划无解", "")
        if n("规划开始") and not n("求解成功") and not n("规划无解"):
            return (
                "有路径规划开始，未见求解成功/无解收口——可能超时、异常中断或仍在算",
                "",
            )
        if n("求解成功") and n("路径拒收"):
            return (
                "求解成功但路径拒收——换 DYNAMIC_MAP 查拒收原因（过时/取消/index）",
                "DYNAMIC_MAP-USPA-LOGS",
            )
        if n("求解成功") and n("DMAP已接收"):
            return (
                "TMS 侧已求解并 DMAP已接收；若仍显示规划中，查 DYNAMIC_MAP 校验/下发或业务状态",
                "DYNAMIC_MAP-USPA-LOGS",
            )
        if n("求解成功") and not n("DMAP已接收") and not n("路径拒收"):
            return (
                "求解成功但未见 DMAP已接收/拒收回传，核对 send_paths 是否发出",
                "DYNAMIC_MAP-USPA-LOGS",
            )
        return ("TMS 缺环对账未收口，按新任务→规划开始→求解→下发继续查原文", "")

    if module == "task_manager":
        if extras.get("specified_robot"):
            return (
                "指定车直接下发不进 TASK-MANAGER；本文件搜空是预期。"
                "回 DYNAMIC_MAP 看 `Robot:车----新任务：` 后挂的是什么任务，不要在任务池里找根因",
                "DYNAMIC_MAP-USPA-LOGS",
            )
        if n("无可用车辆") or n("状态不可分配"):
            return (
                "停在 TASK-MANAGER 分配（无可用车辆/状态异常不可分配）",
                "DYNAMIC_MAP-USPA-LOGS",
            )
        if n("分配成功") or n("分配结果"):
            return (
                "TASK-MANAGER 似有分配结果；若车仍规划中，换 TMS-MAP 查是否出现 新任务-----",
                "TMS-MAP-{map_id}",
            )
        if n("开始分配") and not (n("分配成功") or n("分配结果")):
            return ("有开始分配，未见成功/结果收口，核对分配失败原文", "")
        if not n("任务池更新") and not n("开始分配"):
            return (
                "本文件多为心跳或与该任务无关；路径规划中请换 DYNAMIC_MAP/TMS-MAP，勿用心跳编根因",
                "DYNAMIC_MAP-USPA-LOGS",
            )
        return ("TASK-MANAGER 未见明确分配到该车，换 TMS-MAP-{map_id} / DYNAMIC_MAP 对账", "TMS-MAP-{map_id}")

    return ("当前模块无缺环表，按通用预检索继续", "")


# 按 Actor 过滤预检索词：对象 ID（车号/目标点/任务号）始终保留；
# 手册关键串只留本文件会有的，避免在 DMAP 上空扫「新任务-----」「MAPF」等 TMS 词。
_MODULE_SEED_ALLOW: Dict[str, frozenset] = {
    "dynamic_map": frozenset({
        "Locate Robot", "[LOCATE SUCCESS]", "[LOCATE FAILED]", "[LOCATE ERROR]",
        "接收到TMS路径", "路径校验通过", "路径拒收", "DMAP已接收路径",
        "机器人不在当前所有地图层上", "更新机器人数据---",
        "路径规划失败", "障碍物", "新任务：", "新移动单：",
    }),
    "tms": frozenset({
        "新任务-----", "路径规划开始", "路径规划结束", "求解成功",
        "DMAP已接收路径", "路径拒收", "动态地图Actor获取失败",
        "MAPF", "MAPF-T", "NO_SOLUTION", "原始地图单车路径无解",
        "TRAFFIC_LOCK", "blocked_edges", "路径规划失败", "障碍物",
        "LOCATE FAILED", "LOCATE ERROR", "LOCATE SUCCESS",
        "路径规划无解",
    }),
    "task_manager": frozenset({
        "任务池信息更新", "当前任务：", "各地图单元候选车辆", "无可用车辆",
        "tasks assignment", "任务分配成功", "任务分配结果发送失败",
        "开始任务分配", "任务分配结果", "状态异常，不可分配",
    }),
}
_MODULE_SEED_ALLOW["tms_map"] = _MODULE_SEED_ALLOW["tms"]

# 极易刷屏、单独搜无诊断价值的词：有车号时改用 robot_filter 收敛
_NOISY_SEEDS = frozenset({"更新机器人数据---"})


def _filter_seeds_by_module(kws: List[str], module: str) -> List[str]:
    """对象 ID 全留；手册词按模块白名单裁剪。unknown 不裁。"""
    allow = _MODULE_SEED_ALLOW.get(module)
    if not allow:
        return kws
    handbookish = (
        set(_HANDBOOK_NO_PATH_SEEDS)
        | set(_PLANNING_STUCK_SEEDS)
        | set(_REACH_ALWAYS_SEEDS)
        | set(_SEED_REACH_WORDS)
    )
    out: List[str] = []
    for w in kws:
        if w in handbookish and w not in allow:
            continue
        out.append(w)
    return out[:16]


def _extract_seed_keywords(question: str, task: Optional[Dict] = None) -> List[str]:
    """从用户问题/工单抽出应先扫的关键词（目标点、车号、可达性词）。"""
    blob = " ".join(
        filter(
            None,
            [
                question or "",
                (task or {}).get("title") or "",
                (task or {}).get("problem_summary") or "",
                (task or {}).get("description") or "",
            ],
        )
    )
    out: List[str] = []
    seen = set()

    def _add(s: str) -> None:
        s = (s or "").strip()
        if not s or s.lower() in seen:
            return
        seen.add(s.lower())
        out.append(s)

    # 对象/目标点优先（短、易命中）
    for m in _RE_ROBOT_ID.finditer(blob):
        _add(m.group(1))
    for m in re.finditer(r"agv-\d+", blob, re.I):
        _add(m.group(0))
    # 「目标点100045」中文紧贴数字时 \b 失效，单独抽
    for m in re.finditer(r"(?:目标点|终点|goal)\s*[:=：]?\s*(\d{5,6})", blob, re.I):
        _add(m.group(1))
    for m in _RE_GOAL_NODE.finditer(blob):
        # 避开年月日里的数字段：只要独立 5～6 位目标点
        _add(m.group(1))
    low = blob.lower()
    stuck = any(m.lower() in low or m in blob for m in _PLANNING_STUCK_MARKERS)
    reach = any(m.lower() in low or m in blob for m in _REACH_PHENOMENON_MARKERS)
    # 「路径规划中」优先：对账词必须排在前面，勿被 NO_SOLUTION 词表挤掉
    if stuck:
        for w in _PLANNING_STUCK_SEEDS:
            _add(w)
    # 可达性类现象：强制手册关键串（勿等用户写 TRAFFIC_LOCK）
    if reach:
        for w in _REACH_ALWAYS_SEEDS:
            _add(w)
    for w in _SEED_REACH_WORDS:
        if w.lower() in low or w in blob:
            _add(w)
    # 业务任务 / 原子任务 / task_node：多种说法都进预检索
    for m in re.finditer(
        r"(?:业务)?任务\s*(?:号|ID|id)?\s*[:=：#]?\s*(\d{2,8})",
        blob,
    ):
        _add(m.group(1))
    for m in re.finditer(
        r"(?:原子任务|子任务|task_node(?:_id)?|Task\s*Node\s*Id)\s*[:=：#]?\s*([A-Za-z0-9_-]{2,32})",
        blob,
        re.I,
    ):
        _add(m.group(1))
    for m in re.finditer(r"新任务-----[^:\s]+:([A-Za-z0-9_-]+)", blob):
        _add(m.group(1))
    return out[:20]


def _anchor_spotlight(facts: Dict, task: Dict, question: str) -> str:
    """首轮程序化锚定：现象/时间窗/对象先算好，禁止 LLM 扫全量日志。

    用户问题里的车号/时刻优先于「错误最密集时段 / top 车型」，避免把 08-24 17:58
    的单查到 08-22 13 时、把 XTD-96 查成日志里出现最多的 XTD-92。
    """
    title = (task.get("title") or "").strip()
    summary = (task.get("problem_summary") or "").strip()
    phenomenon = question or summary or title or "（工单未写明现象）"
    blob = " ".join(x for x in (question or "", title, summary) if x)
    robot = (task.get("robot_type") or "").strip()
    m_bot = _RE_ROBOT_ID.search(question or "") or _RE_ROBOT_ID.search(blob)
    robots = facts.get("top_robots") or []
    obj = robot or (m_bot.group(1).upper() if m_bot else "") or (
        robots[0] if robots else "（从客观事实里的车型选）"
    )
    m_when = _RE_WHEN.search(question or "") or _RE_WHEN.search(blob)
    hours = facts.get("error_hours") or []
    if m_when:
        year, mon, day, hm = m_when.group(1), m_when.group(2), m_when.group(3), m_when.group(4)
        if not year:
            year = (facts.get("date") or "")[:4] or "2026"
        stamp = f"{year}-{int(mon):02d}-{int(day):02d} {hm}"
        window_hint = (
            f"优先查用户给出的故障时刻 {stamp} 前后各约 10 分钟，"
            "不要改用错误密集时段"
        )
    elif hours:
        window_hint = f"优先查错误密集时段 {hours[0][0]} 时附近（前后各 15 分钟），不要扫整天"
    else:
        ts, te = facts.get("time_start"), facts.get("time_end")
        window_hint = (
            f"日志范围 {ts} ~ {te}，先缩到可疑 10～20 分钟再查" if ts and te
            else "先缩窄时间窗再查"
        )
    reach = any(m in blob for m in _REACH_PHENOMENON_MARKERS)
    stuck = any(m in blob for m in _PLANNING_STUCK_MARKERS)
    method = (
        "先锚定 → 分层（现象模块 vs 根因模块）→ 用 query 验证一个假设 → "
        "正常时段对照 → 证据不足就 conclude 并写清要补充哪份日志，禁止硬猜、禁止全量扫描。"
    )
    if stuck:
        method = (
            "【程序已做缺环对账】以「缺环判定」段落为结论起点，用命中摘录核对，禁止另起炉灶。"
            "「路径规划中」不是算法日志原文。链路："
            "DMAP 刷新/Locate → TMS 新任务-----车:task_node_id → 路径规划开始 → 求解成功/无解 → "
            "DMAP已接收/拒收 → DMAP 接收到TMS路径。"
            "缺哪环停哪环；need_feed 非空必须换文件。禁止只搜业务号；禁止搜「路径规划中」字符串。"
        )
    elif reach:
        method = (
            "可达性因果链（必须按序，缺一环不得 conclude 根因）："
            "① keyword 找「原始地图单车路径无解 / NO_SOLUTION / 路径规划失败」原文；"
            "② 同分钟查 TRAFFIC_LOCK / blocked_edges / 障碍物变化，记下被封拓扑边；"
            "③ 对照故障前数分钟同目标点是否仍「求解成功」。"
            "mapId 不一致、不在当前地图层 = 旁证，只能写进 need_feed，不能当根因。"
        )
    # 抽出用户已给出的任务相关 ID，写进锚定，避免只抱业务号搜
    id_hits: List[str] = []
    for m in re.finditer(
        r"(?:业务)?任务\s*(?:号|ID|id)?\s*[:=：#]?\s*(\d{2,8})",
        blob,
    ):
        id_hits.append(f"业务号候选={m.group(1)}")
    for m in re.finditer(
        r"(?:原子任务|子任务|task_node(?:_id)?|Task\s*Node\s*Id)\s*[:=：#]?\s*([A-Za-z0-9_-]{2,32})",
        blob,
        re.I,
    ):
        id_hits.append(f"原子/节点={m.group(1)}")
    id_line = (
        "- 任务ID: " + "；".join(dict.fromkeys(id_hits))
        + "（业务号与原子任务都要搜；业务号空再按车号追 `新任务-----`）\n"
        if id_hits
        else "- 任务ID: 问题未写清原子任务号时，先用车号+时间窗搜 `新任务-----`，记下冒号后的 task_node_id 再追规划\n"
    )
    return (
        "## 首轮锚定（程序已给出，日期/对象禁止改写）\n"
        f"- 现象: {phenomenon[:120]}\n"
        f"- 对象: {obj}\n"
        f"- 时间窗: {window_hint}\n"
        f"{id_line}"
        f"- 方法: {method}\n"
    )


# ── System Prompt ───────────────────────────────────────────

def _make_system_prompt() -> str:
    """通用 7 步方法论：不绑定具体故障场景。"""
    return """你是资深AGV/AMR日志分析专家。你只能输出【一行JSON】，禁止输出JSON以外的任何散文、解释、Markdown。

按 7 步思考，但每轮只输出一个命令：
①锚定现象/时间/对象 ②建模依赖链 ③分层定位 ④假设-验证 ⑤正常vs异常对照 ⑥跨模块归因（缺日志就明确要数据）⑦收敛报告。

可用命令（每次恰好一个）:

1) 锚定（①；只记录现象、时间、对象，不查日志）:
   {"action":"anchor","phenomenon":"现象","time_start":"YYYY-MM-DD HH:MM","time_end":"YYYY-MM-DD HH:MM","robot":"车号或空串"}

2) 假设（④；只记录一条待验证的判断，不查日志）:
   {"action":"hypothesis","hypothesis":"若…则日志里应出现…","if_true":"成立后下一步","if_false":"不成立后下一步"}

3) 查询（验证假设；analysis 必须写清本轮假设）:
   {"action":"query","analysis":"假设:…；验证若成立/不成立下一步","query":{"time_start":"YYYY-MM-DD HH:MM","time_end":"YYYY-MM-DD HH:MM","robot_filter":"车型ID或空串","task_filter":"任务ID或空串","keyword_filter":"目标点或不可达等原文词","error_only":false,"max_results":50}}

4) 对照（⑤；必须带 query，比较故障窗和故障前正常窗）:
   {"action":"contrast","analysis":"对照:故障窗 vs 正常窗","query":{"time_start":"YYYY-MM-DD HH:MM","time_end":"YYYY-MM-DD HH:MM","robot_filter":"","task_filter":"","keyword_filter":"","error_only":false,"max_results":50}}

5) 只读命令（③取证；至少做过一次 query 之后才能用；省略文件名则读当前日志）:
   {"action":"shell","analysis":"假设:…","cmd":"grep -n '一致性校验失败' | head -n 50"}
   允许 grep/head/tail/sed(-n)/awk({print})/wc/cat/sort/uniq/cut 及管道。禁止写文件、重定向、白名单外命令。

6) 归因（⑥；只记录跨模块判断，不查日志；缺日志写 need_feed）:
   {"action":"attribute","module":"TMS","because":"本文件缺哪一环","need_feed":"TMS"}

7) 下结论（⑦；证据不足也要 conclude，写清缺什么）:
{"action":"conclude","conclusion":"一句话根因+证据","confidence":0.0,"need_feed":"","evidence_lines":["L数字: 关键内容"]}

8) 放弃（当前日志完全无法推进）:
{"action":"fallback","reason":"为什么确定查不出","need_feed":"建议用户补充的日志模块"}

硬性规则:
- time_start/time_end、robot_filter、task_filter 只能从「日志客观事实」或「首轮锚定」里选；绝不虚构日期或ID。
- anchor、hypothesis、attribute 不读取日志。第一次读取日志必须是窄 query，禁止整日/全量扫描，禁止一开始 cat 全文件。
- 每条 query 只验证一个假设；命中过多就缩窗，不要加大 max_results。
- **禁止臆造错误码**（如 PATH_PLANNING_SINGLE_AGENT_NO_SOLUTION）；grep/keyword 只能用问题里出现的词、或「程序预检索」里已命中的原文片段。
- 「更新Robots / 更新休息点」等 INFO 刷屏行体内常嵌历史 error_code，**不是本轮故障**；error_only 命中过多时改用 keyword_filter（目标点/车号/不可达/topo/求解）且 error_only=false。
- 查「目标点不可达 / 路径求解」时优先 keyword_filter=目标点编号 或 不可达/求解/topo/NO_SOLUTION/TRAFFIC_LOCK/blocked_edges，不要只扫 error_only。
- **可达性结论必须有因果链**：先找「无解/NO_SOLUTION/路径规划失败」原文 → 再查同分钟障碍物/锁区/blocked_edges → 再对照故障前数分钟同目标点是否仍可解。禁止只凭 mapId 不一致、地图层提示就下根因结论（那是旁证，须继续验证）。
- **任务 ID 分层检索**：业务任务号 ≠ 原子任务/task_node_id。须用用户给出的全部 ID，并在日志里用车号追出 `新任务-----车:{task_node_id}`。禁止只搜业务号 0 命中就 conclude「无该任务规划日志」。
- **「路径规划中 / 不动 / 无路径下发」按手册 6.1**：DYNAMIC_MAP(刷新+上轨) → TMS(新任务+路径规划开始+求解成功) → DYNAMIC_MAP(接收/拒收)。现行优先 MAPF「求解成功」；DPP 仅作旧环境旁证。缺环即停，need_feed 换下一份算法日志。禁止因 error_only=0 判定无证据。
- 用户消息里的「算法日志先查哪份」「任务 ID 怎么查」是权威表；「当前模块深挖」只约束本文件。keyword/grep 用手册原文串，禁止臆造错误码。
- 程序预检索已命中的原文串必须优先沿用；不要跳到臆造的 PATH_PLANNING_* 枚举。
- error_only=true 只回真实 ERROR/WARN 级别；要上下文时 false 且时间窗 ≤20 分钟。
- shell 输出会被截断；命中过多先用 grep 收窄再 head，不要 cat 整份日志。
- 证据不足时 conclude.need_feed 写清要 TMS/全局规划/定位等哪类日志，禁止硬猜。
- 每轮只输出一个JSON，输出前不要有任何思考文字。"""


# ── 日志分析结论模型 ─────────────────────────────────────────

class LogAnalysisResult:
    """日志子 Agent 输出"""
    def __init__(self):
        self.conclusion = ""          # 一句话结论
        self.evidence = []            # [{"line": 390, "ts": "11:01:44", "summary": "..."}]
        self.queries_made = 0         # 执行了几轮查询
        self.fallback_used = False    # 是否兜底了
        self.parse_failures = 0       # LLM 输出解析失败次数
        self.confidence = None        # 0~1，可选
        self.need_feed = ""           # 建议补充的日志模块
        self.anchor = {}              # ① 锚定
        self.step_hypotheses = []     # ④ 假设
        self.attributions = []        # ⑥ 归因

    def to_dict(self):
        return {
            "conclusion": self.conclusion,
            "evidence": self.evidence[:10],
            "queries": self.queries_made,
            "fallback": self.fallback_used,
            "confidence": self.confidence,
            "need_feed": self.need_feed,
            "anchor": self.anchor,
            "step_hypotheses": list(self.step_hypotheses),
            "attributions": list(self.attributions),
        }

    def to_prompt_text(self) -> str:
        """生成供诊断 Prompt 注入的文本"""
        parts = []
        if self.conclusion:
            parts.append(f"日志分析结论: {self.conclusion}")
        if self.confidence is not None:
            parts.append(f"置信度: {self.confidence}")
        if self.need_feed:
            parts.append(f"建议补充数据: {self.need_feed}")
        if self.attributions:
            last = self.attributions[-1]
            parts.append(
                f"跨模块归因: {last.get('module') or ''} {last.get('because') or ''}".strip()
            )
        if self.evidence:
            parts.append("关键日志行:")
            for e in self.evidence[:8]:
                # summary 已包含时间戳和字段信息，直接展示
                parts.append(f"  L{e['line']}: {e['summary'][:280]}")
        if self.queries_made:
            parts.append(f"共查询 {self.queries_made} 轮")
        return "\n".join(parts)


# ── 日志索引进程内内存缓存 ─────────────────────────────────────
# 同一份日志（同一 log_path + 文件未变）在 AI 服务进程运行期间反复讨论时，
# 复用已建好的 LogIndex，避免大日志每次全量 rebuild（数十秒）。
# key: log_path；value: (signature, index)。signature 用 (mtime, size, build_ver) 判断失效。
_LOG_INDEX_CACHE: Dict[str, tuple] = {}
_LOG_CACHE_MAX = 16  # 缓存条目上限，防无限增长
# 索引构建逻辑变更时递增，强制丢弃旧缓存（避免仍用「更新行当 ERROR」的旧索引）
_INDEX_BUILD_VER = 5


def _log_index_signature(path: str) -> tuple:
    """日志文件签名：mtime + size + 构建版本。"""
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size, _INDEX_BUILD_VER)
    except Exception:
        return (0, 0, _INDEX_BUILD_VER)


def _get_cached_log_index(log_path: str) -> Optional[LogIndex]:
    """从进程内缓存取日志索引；缓存未命中或文件已变（mtime/size 变化）时返回 None。

    索引是日志的稳定物理结构，不因"重新分析"而重建：只要文件没变就一直复用，
    用户再次要求分析时只重新跑推理（analyze），索引仍走缓存。
    """
    hit = _LOG_INDEX_CACHE.get(log_path)
    if hit is None:
        return None
    sig, idx = hit
    if sig != _log_index_signature(log_path):
        _LOG_INDEX_CACHE.pop(log_path, None)
        return None
    return idx


def _cache_log_index(log_path: str, idx: LogIndex) -> None:
    """把建好的索引写进进程内缓存（LRU 上限淘汰最旧）。"""
    _LOG_INDEX_CACHE[log_path] = (_log_index_signature(log_path), idx)
    if len(_LOG_INDEX_CACHE) > _LOG_CACHE_MAX:
        try:
            oldest = next(iter(_LOG_INDEX_CACHE))
            _LOG_INDEX_CACHE.pop(oldest, None)
        except Exception:
            pass


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

    MAX_ROUNDS = 10  # 含 anchor/hypothesis/attribute；真正查日志仍受 SOFT_LIMIT 限制
    SOFT_LIMIT = 4

    def __init__(self, log_path: str, source_name: str = ""):
        self.log_path = log_path
        # 时间窗临时文件名可能丢前缀；传原始文件名供模块识别
        self._source_name = source_name or Path(log_path).name
        self._index: Optional[LogIndex] = None
        self._facts: Dict = {}
        self._llm = None
        self._live_probe: Optional[Dict] = None

    def _algo_module(self) -> str:
        return _detect_algo_log_module(self.log_path, name_hint=self._source_name)

    async def _ensure_clients(self, progress=None):
        if self._llm is None:
            self._llm = await get_llm_client()
        if self._index is None:
            try:
                # 复用进程内缓存：同一份日志 + 文件未变 → 不重新全量 build（快，数十秒省到毫秒）。
                # 索引是稳定物理结构，不因"重新分析"重建；用户再次要求分析时只重跑 analyze 推理。
                cached = _get_cached_log_index(self.log_path)
                _reusing = cached is not None
                if progress is not None:
                    progress({
                        "id": "log_index",
                        "description": ("复用已缓存的日志索引" if _reusing
                                        else "正在建立日志索引（大日志需数十秒）"),
                        "status": "in_progress",
                        "capability": "log_analyze",
                        "phase": "running",
                    })
                if _reusing:
                    self._index = cached
                    logger.info(f"LogSubAgent: 复用已缓存日志索引 path={Path(self.log_path).name}")
                else:
                    self._index = LogIndex(self.log_path).build()
                    _cache_log_index(self.log_path, self._index)
                self._facts = self._index.discover_facts(top_n=8)
            finally:
                try:
                    if progress is not None:
                        progress({
                            "id": "log_index",
                            "description": "日志索引就绪",
                            "status": "completed",
                            "capability": "log_analyze",
                            "phase": "done",
                        })
                except Exception:
                    pass

    def _seed_keyword_scan(
        self,
        question: str,
        task: Dict,
        result: "LogAnalysisResult",
    ) -> str:
        """程序预检索：用问题里的目标点/车号/可达性词扫全文件（error_only=false）。

        「路径规划中」类现象走缺环对账（仅此类）；不可达等其它问题仍走通用种子词。
        """
        if not self._index:
            return ""
        # 路径规划中：只做 Actor 缺环表，不混扫其它故障词表
        if _is_planning_stuck(question, task):
            return self._planning_chain_scan(question, task, result)

        kws = _extract_seed_keywords(question, task)
        module = self._algo_module()
        kws = _filter_seeds_by_module(kws, module)
        if not kws:
            return ""
        # 从问题里抽车号，用来收敛刷屏词（更新机器人数据---）
        robots = _extract_robot_ids(question, task)
        robot_hint = robots[0] if robots else None
        ts = (self._facts or {}).get("time_start")
        te = (self._facts or {}).get("time_end")
        parts = [
            "## 程序预检索（已绕过 error_only / 更新刷屏噪音；请基于下列命中继续验证，勿臆造错误码）",
            f"- 当前模块=`{module}`，已按 Actor 裁剪预检索词",
        ]
        any_hit = False
        for kw in kws:
            rf = robot_hint if kw in _NOISY_SEEDS else None
            log_query = LogQuery(
                time_start=ts,
                time_end=te,
                keyword=kw,
                robot_filter=rf,
                error_only=False,
                max_results=40,
                context_before=1,
                context_after=1,
            )
            text = self._index.query(log_query)
            matched = _matched_lines(text)
            logger.info(
                f"LogSubAgent seed keyword={kw!r} module={module} "
                f"robot={rf or '-'} → {matched} lines"
            )
            if matched <= 0:
                continue
            any_hit = True
            _collect_evidence(result, text, round_num=0)
            # 截断喂给 LLM，避免把超长 UPDATE 行整段灌进去
            clipped = (text or "")[:8000]
            parts.append(f"### keyword=`{kw}` 命中 {matched} 行\n{clipped}")
        if not any_hit:
            return (
                "## 程序预检索\n"
                f"- 已扫关键词 {kws}（module={module}），本文件时间窗内无直接命中；"
                "请改用 shell grep 原文片段，或 conclude 要求换 TMS-MAP / DYNAMIC_MAP 日志。"
            )
        return "\n\n".join(parts)

    def _planning_chain_scan(
        self,
        question: str,
        task: Dict,
        result: "LogAnalysisResult",
    ) -> str:
        """路径规划中专用：按 Actor 缺环表顺序扫关键字，输出停在哪一环。

        其它现象不得调用本方法。
        """
        module = self._algo_module()
        robots = _extract_robot_ids(question, task)
        robot = robots[0] if robots else ""
        steps = _PLANNING_CHAIN_STEPS.get(module)
        ts = (self._facts or {}).get("time_start")
        te = (self._facts or {}).get("time_end")

        parts = [
            "## 路径规划中 · 缺环对账（仅此类现象；勿当成不可达/其它故障流程）",
            f"- 模块=`{module}` 车=`{robot or '（未抽出车号，命中会偏宽）'}`",
            "- 「路径规划中」不是算法日志原文；用全链路缺哪环定位卡点",
            "- `新任务：`（DMAP，车上挂了 task_node）≠ `新任务-----`（TMS-MAP，规划器感知）",
            "- 指定车直接下发不进 TASK-MANAGER，也不必然进 TMS-MAP；先看 DMAP 新任务是什么",
        ]
        if _is_specified_robot_dispatch(question, task):
            parts.append(
                "- 工单/问题写明指定车或现场直发：不要因 TMS-MAP / TASK-MANAGER 搜空下结论"
            )
        if not steps:
            parts.append(
                f"- 当前文件未能识别为 DMAP/TMS/TASK-MANAGER（path=`{self._source_name}`），"
                "无法跑缺环表；请换对应算法日志或改用通用预检索。"
            )
            return "\n".join(parts)

        hit_counts: Dict[str, int] = {}
        for step_name, kw_tpl, kind in steps:
            kw = kw_tpl.format(robot=robot) if "{robot}" in kw_tpl else kw_tpl
            # 模板含车号但没抽出车号时，退回不带车号的通用串
            if "{robot}" in kw_tpl and not robot:
                if step_name == "上轨调用":
                    kw = "Locate Robot"
                elif step_name == "新任务感知":
                    kw = "新任务-----"
                elif step_name == "无定位点":
                    kw = "no cur node"
            # 关键字已带车号则不再叠 robot_filter；刷屏「更新机器人数据」才靠 filter 收敛
            rf = None
            if robot and (
                kw.startswith("更新机器人数据")
                or kw.startswith("新任务：")
                or kw.startswith("新移动单")
            ):
                rf = robot
            log_query = LogQuery(
                time_start=ts,
                time_end=te,
                keyword=kw,
                robot_filter=rf,
                error_only=False,
                max_results=30,
                context_before=0,
                context_after=1,
            )
            text = self._index.query(log_query)
            matched = _matched_lines(text)
            hit_counts[step_name] = matched
            logger.info(
                f"LogSubAgent chain step={step_name!r} kw={kw!r} "
                f"module={module} → {matched} lines"
            )
            mark = "✓" if matched > 0 else "·"
            parts.append(f"- {mark} [{kind}] {step_name}  keyword=`{kw}`  → {matched} 行")
            if matched > 0:
                _collect_evidence(result, text, round_num=0)
                clipped = (text or "")[:5000]
                parts.append(f"### {step_name} 命中摘录\n{clipped}")

        extras = self._planning_chain_extras(module, robot, question, task, hit_counts)
        if extras.get("notes"):
            parts.append("")
            parts.append("### 程序从原文抽出的关键字段")
            for note in extras["notes"]:
                parts.append(f"- {note}")

        verdict, need_feed = _verdict_planning_chain(
            module, hit_counts, robot=robot, extras=extras,
        )
        parts.append("")
        parts.append("## 缺环判定（程序结论，LLM 必须以此为起点，禁止改口成「没有车端日志」）")
        parts.append(f"- **判定**: {verdict}")
        if need_feed:
            parts.append(f"- **need_feed**: {need_feed}")
        parts.append(
            "- conclude 时：写清停在哪一环 + 关键原文；"
            "若 need_feed 非空必须带上；不要搜「路径规划中」字符串本身。"
        )
        logger.info(
            f"LogSubAgent chain verdict module={module} robot={robot or '-'} "
            f"→ {verdict[:120]}"
        )
        return "\n".join(parts)

    def _planning_chain_extras(
        self,
        module: str,
        robot: str,
        question: str,
        task: Dict,
        hit_counts: Dict[str, int],
    ) -> Dict:
        """从索引原文抽 mapId/taskNodeId/pathId，并识别「通用 TMS 仅心跳」。"""
        out: Dict = {"notes": []}
        if _is_specified_robot_dispatch(question, task):
            out["specified_robot"] = True
            out["notes"].append(
                "工单/问题判定为指定车直接下发：任务不进 TASK-MANAGER，TMS-MAP 的 `新任务-----` 可能本来就没有"
            )
        # 现场 Ray 快照优先写入（日志缺环时仍有硬证据）
        probe = self._live_probe if isinstance(self._live_probe, dict) else None
        if probe and probe.get("ok"):
            out["from_live_probe"] = True
            for k in ("map_id", "task_id", "task_node_id"):
                if probe.get(k):
                    out[k] = probe[k]
            if probe.get("path_id_none"):
                out["path_id_none"] = True
            elif probe.get("path_id"):
                out["path_id"] = probe.get("path_id")
            for note in probe.get("notes") or []:
                out["notes"].append(note)

        if not self._index:
            return out
        ts = (self._facts or {}).get("time_start")
        te = (self._facts or {}).get("time_end")

        if module in ("tms", "tms_map"):
            alive_q = LogQuery(
                time_start=ts, time_end=te, keyword="SERVICES-ALIVE",
                error_only=False, max_results=5,
            )
            plan_q = LogQuery(
                time_start=ts, time_end=te, keyword="路径规划开始",
                error_only=False, max_results=5,
            )
            new_q = LogQuery(
                time_start=ts, time_end=te, keyword="新任务-----",
                error_only=False, max_results=5,
            )
            alive_n = _matched_lines(self._index.query(alive_q))
            plan_n = _matched_lines(self._index.query(plan_q))
            new_n = _matched_lines(self._index.query(new_q))
            total = int(getattr(self._index, "_total", 0) or 0)
            # 源文件名不是 TMS-MAP-，且几乎只有心跳
            src = (self._source_name or "").upper()
            if "TMS-MAP" not in src and alive_n > 0 and plan_n == 0 and new_n == 0:
                out["tms_heartbeat_only"] = True
                out["notes"].append(
                    f"源文件=`{self._source_name}`：SERVICES-ALIVE×{alive_n}，"
                    f"新任务/规划开始均为 0，总行约 {total} ——判定为通用 TMS 心跳壳"
                )
            return out

        if module != "dynamic_map" or not robot:
            return out

        # 从「更新机器人数据」摘录里抠挂车任务与 pathId
        q = LogQuery(
            time_start=ts,
            time_end=te,
            keyword="更新机器人数据---",
            robot_filter=robot,
            error_only=False,
            max_results=20,
            context_before=0,
            context_after=0,
        )
        text = self._index.query(q) or ""
        # 业务号（问题里的）优先
        biz = ""
        blob = " ".join(filter(None, [
            question or "",
            (task or {}).get("title") or "",
            (task or {}).get("problem_summary") or "",
        ]))
        m_biz = re.search(r"(?:任务\s*)?(\d{4,8})", blob)
        if m_biz:
            biz = m_biz.group(1)

        map_id = out.get("map_id") or ""
        node_id = out.get("task_node_id") or ""
        task_id = out.get("task_id") or ""
        path_none = bool(out.get("path_id_none"))
        path_set = bool(out.get("path_id"))
        for line in text.splitlines():
            if robot not in line or "更新机器人数据" not in line:
                continue
            if biz and biz not in line and "'tasks': []" in line:
                continue
            # 报文原文字段是 camelCase mapId，取值写入内部 map_id
            mm = re.search(r"'mapId': '([^']+)'", line)
            if mm and not map_id:
                map_id = mm.group(1)
            # currentTaskState
            m_cts = re.search(
                r"'currentTaskState': \{[^}]*'pathId': ([^,]+)[^}]*"
                r"'taskNodeId': ([^,]+)[^}]*'taskId': ([^,}]+)",
                line,
            )
            if m_cts:
                pid_raw = m_cts.group(1).strip()
                nid_raw = m_cts.group(2).strip().strip("'\"")
                tid_raw = m_cts.group(3).strip().strip("'\"")
                if pid_raw == "None":
                    path_none = True
                elif pid_raw not in ("None",):
                    path_set = True
                if nid_raw and nid_raw != "None":
                    node_id = nid_raw
                if tid_raw and tid_raw != "None":
                    task_id = tid_raw
            # tasks 列表里的业务号 / taskNode
            if "'id': '" in line and "taskNodeList" in line:
                m_tid = re.search(r"'tasks': \[\{'id': '(\d+)'", line)
                if m_tid and not task_id:
                    task_id = m_tid.group(1)
                m_nid = re.search(r"'taskNodeList': \[\{'id': '(\d+)'", line)
                if m_nid and not node_id:
                    node_id = m_nid.group(1)
                m_pt = re.search(r"'pointId': '([^']*)'", line)
                m_nd = re.search(r"'nodeId': ([^,}\]]+)", line)
                if m_pt or m_nd:
                    out["notes"].append(
                        f"目标 pointId={m_pt.group(1) if m_pt else '?'} "
                        f"nodeId={m_nd.group(1).strip() if m_nd else '?'}"
                    )
                m_ty = re.search(r"'type': '([^']+)'", line)
                if m_ty and not out.get("task_type"):
                    out["task_type"] = m_ty.group(1)
                    out["notes"].append(f"车上任务类型=`{m_ty.group(1)}`（EVADE 不会进 TMS `新任务-----`）")

        # DMAP「新任务：」原文：车上挂了哪个 task_node
        nq = LogQuery(
            time_start=ts,
            time_end=te,
            keyword="新任务：",
            robot_filter=robot or None,
            error_only=False,
            max_results=10,
        )
        ntext = self._index.query(nq) or ""
        for line in ntext.splitlines():
            m_dn = re.search(r"新任务：(\S+?)---", line)
            if m_dn:
                nid = m_dn.group(1).strip()
                if nid and nid != "None":
                    node_id = node_id or nid
                    note = f"DMAP `新任务：` → task_node_id=`{nid}`（这不是 TMS 的 `新任务-----`）"
                    if note not in out["notes"]:
                        out["notes"].append(note)
                break

        if map_id:
            out["map_id"] = map_id
            if out.get("specified_robot"):
                note = (
                    f"车上 map_id=`{map_id}`；指定车直发时不要默认去 `TMS-MAP-{map_id}` 找规划原文，"
                    "先看本文件 `新任务：` 是什么"
                )
            else:
                note = f"车上 map_id=`{map_id}` → 规划服务名/日志前缀为 `TMS-MAP-{map_id}`"
            if note not in out["notes"]:
                out["notes"].append(note)
        if task_id:
            out["task_id"] = task_id
            note = f"业务任务号=`{task_id}`"
            if note not in out["notes"]:
                out["notes"].append(note)
        if node_id:
            out["task_node_id"] = node_id
            note = f"原子任务 task_node_id=`{node_id}`（TMS 搜 `新任务-----{robot}:{node_id}`）"
            if note not in out["notes"]:
                out["notes"].append(note)
        if path_none and not path_set:
            out["path_id_none"] = True
            note = "currentTaskState.pathId 持续为 None（有任务无路径下发）"
            if note not in out["notes"]:
                out["notes"].append(note)
        elif path_set:
            out["notes"].append("已见到非空 pathId")
        return out

    def _program_conclude_on_llm_fail(self, seed_block: str, err: str) -> str:
        """LLM 不可用时，用程序预检索/缺环判定给出可交付结论，禁止空跑。"""
        err_short = (err or "").split("\n")[0][:160]
        # 优先取缺环判定段
        m = re.search(r"\*\*判定\*\*:\s*(.+)", seed_block or "")
        if m:
            need = ""
            m2 = re.search(r"\*\*need_feed\*\*:\s*(.+)", seed_block or "")
            if m2:
                need = f" 下一步换 `{m2.group(1).strip()}`。"
            return (
                f"程序缺环判定（LLM 中断：{err_short}）：{m.group(1).strip()}。"
                f"{need}"
            )
        module = self._algo_module()
        src = self._source_name or Path(self.log_path).name
        hits = re.findall(r"### keyword=`([^`]+)` 命中 (\d+) 行", seed_block or "")
        hit_pairs = [(k, int(n)) for k, n in hits if int(n) > 0]
        if not hit_pairs:
            return (
                f"已索引 `{src}`（module={module}），程序预检索无手册关键串命中；"
                f"LLM 分析中断（{err_short}）。请检查 LLM 网络后重试，"
                f"或 need_feed 换 TMS / DYNAMIC_MAP 对照。"
            )
        top = "；".join(f"`{k}`×{n}" for k, n in hit_pairs[:8])
        return (
            f"程序预检索（LLM 中断：{err_short}）：`{src}` module={module}。"
            f"命中摘要：{top}。请恢复 LLM 后重跑。"
        )

    async def analyze(
        self,
        task_context: Dict,
        user_question: str = "",
        progress=None,
        is_cancelled=None,
        task_id: str = "",
        supplements_bag: Optional[list] = None,
        live_probe: Optional[Dict] = None,
        available_log_paths: Optional[List[str]] = None,
    ) -> LogAnalysisResult:
        """主入口：多轮推理 → 返回分析结论。

        progress: 可选进度回调（ai.progress）——上报建索引 / R1..Rn 等子节点，
        让前端在日志分析期间展示内部子步骤，避免"卡在一个节点上很久"。
        live_probe: USP 现场 Ray 车态快照（路径规划中缺环用）。
        available_log_paths: 本轮已拉到的全部算法日志，注入角色目录供切换。
        """
        self._live_probe = live_probe if isinstance(live_probe, dict) else None
        def _p(payload: dict) -> None:
            try:
                if progress is not None:
                    progress(payload)
            except Exception:
                pass

        t0 = _time.perf_counter()
        logger.info(
            f"LogSubAgent start: path={Path(self.log_path).name} "
            f"module={self._algo_module()} "
            f"question={user_question[:60]}"
        )
        await self._ensure_clients(progress=_p)
        result = LogAnalysisResult()

        log_date = self._facts.get("date", "unknown")
        cur_module = self._algo_module()

        if not isinstance(task_context, dict):
            task_context = {}
        context_text = await _fit_ticket_opening(task_context, user_question, self._llm)
        context_text += f"\n\n{_facts_to_text(self._facts)}"
        context_text += f"\n\n**日志日期**: {log_date}"
        context_text += (
            f"\n\n**当前日志文件**: {Path(self.log_path).name}"
            f"（module=`{cur_module}`：{_MODULE_ROLE_CN.get(cur_module, '')}；"
            "shell 省略文件名即读这一份）"
        )
        catalog = format_available_log_catalog(available_log_paths)
        if catalog:
            context_text += f"\n\n{catalog}"
        context_text += f"\n\n{_anchor_spotlight(self._facts, task_context or {}, user_question)}"
        # 路由总表（始终）+ 当前 Actor 深挖指引（按文件分流）
        try:
            docs = _load_log_docs(self._source_name or self.log_path)
            if docs:
                context_text += f"\n\n{docs}"
                logger.info(
                    f"LogSubAgent 手册注入 module={cur_module} "
                    f"source={self._source_name} chars={len(docs)}"
                )
        except Exception as e:
            logger.warning(f"LogSubAgent 手册加载失败: {e}")

        # 程序预检索：从问题抽出目标点/车号/可达性词，绕过 error_only 刷屏噪音先捞证据
        seed_block = self._seed_keyword_scan(user_question, task_context or {}, result)
        if seed_block:
            context_text += f"\n\n{seed_block}"
            # 程序缺环判定里的 need_feed 先写入，供上层按角色自动换文件
            m_nf = re.search(r"\*\*need_feed\*\*:\s*(.+)", seed_block)
            if m_nf and not result.need_feed:
                result.need_feed = m_nf.group(1).strip()

        system_prompt = _make_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_text},
        ]
        query_history: List = []
        narrow_queries = 0
        tid = str(task_id or (task_context or {}).get("task_id") or "")

        async def _pull_round_injects() -> None:
            nonlocal user_question
            if not tid:
                return
            try:
                from ai.agents.AiTaskPlatform.runtime.inject_mailbox import drain
                extras = await drain(tid)
            except Exception:
                extras = []
            if not extras:
                return
            if supplements_bag is not None:
                supplements_bag.extend(extras)
            extra_txt = "\n".join(extras)
            user_question = f"{user_question}\n{extra_txt}".strip() if user_question else extra_txt
            messages.append({
                "role": "user",
                "content": f"工程师本轮补充（请纳入后续查询与结论，不要当成新一轮独立问题）:\n{extra_txt}",
            })

        for round_num in range(1, self.MAX_ROUNDS + 1):
            if is_cancelled is not None:
                try:
                    _c = is_cancelled()
                    if asyncio.iscoroutine(_c):
                        _c = await _c
                    if _c:
                        logger.info(f"LogSubAgent aborted by client at R{round_num}")
                        result.conclusion = ""
                        result.fallback_used = True
                        break
                except Exception:
                    pass
            await _pull_round_injects()
            _p({
                "id": f"log_r{round_num}",
                "description": f"日志分析第 {round_num} 轮：推理下一步查询",
                "status": "in_progress",
                "capability": "log_analyze",
                "phase": "running",
            })
            try:
                response = await self._llm.chat(
                    messages=messages, max_tokens=300, temperature=0.0,
                )
            except Exception as llm_err:
                logger.error(
                    f"LogSubAgent R{round_num} LLM 失败: {type(llm_err).__name__}: {llm_err}"
                )
                result.conclusion = self._program_conclude_on_llm_fail(
                    seed_block, f"{type(llm_err).__name__}: {llm_err}"
                )
                result.fallback_used = True
                break

            cmd = _parse_llm_command(response)
            if cmd is None:
                result.parse_failures += 1
                logger.warning(f"LogSubAgent R{round_num} parse failed, raw[:200]={response[:200]}")
                if result.queries_made >= self.SOFT_LIMIT:
                    # 查询已到预算 → 抢救任何 conclude 结论，避免丢答案
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
                                             "锚定用 {\"action\":\"anchor\",...}，假设用 {\"action\":\"hypothesis\",...}，"
                                             "查索引用 {\"action\":\"query\",...}，对照用 {\"action\":\"contrast\",...}，"
                                             "取证用 {\"action\":\"shell\",...}，归因用 {\"action\":\"attribute\",...}，"
                                             "证据足够用 {\"action\":\"conclude\",...}。"})
                continue

            action = cmd.get("action", "conclude")

            if action == "anchor":
                phenomenon = str(cmd.get("phenomenon") or "").strip()
                if not phenomenon and not cmd.get("time_start") and not cmd.get("robot"):
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": "anchor 需要 phenomenon，或 time_start 与对象。anchor 不查日志。",
                    })
                    continue
                result.anchor = {
                    "phenomenon": phenomenon,
                    "time_start": str(cmd.get("time_start") or ""),
                    "time_end": str(cmd.get("time_end") or ""),
                    "robot": str(cmd.get("robot") or ""),
                }
                shown = phenomenon or result.anchor["time_start"] or result.anchor["robot"]
                _p({
                    "id": f"log_r{round_num}",
                    "description": f"锚定：{shown[:40]}",
                    "status": "completed",
                    "capability": "log_analyze",
                    "phase": "done",
                })
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": "已记录锚定。下一步请 hypothesis，或直接用 query 按这个时间窗做窄查询。不要 shell，不要扫全文件。",
                })
                continue

            if action == "hypothesis":
                text = str(cmd.get("hypothesis") or "").strip()
                if not text:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": "hypothesis 缺少 hypothesis。请只写一条待验证的判断，不要查日志。",
                    })
                    continue
                result.step_hypotheses.append({
                    "hypothesis": text,
                    "if_true": str(cmd.get("if_true") or ""),
                    "if_false": str(cmd.get("if_false") or ""),
                })
                _p({
                    "id": f"log_r{round_num}",
                    "description": f"假设：{text[:40]}",
                    "status": "completed",
                    "capability": "log_analyze",
                    "phase": "done",
                })
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": "已记录假设。请用 query 或 contrast 验证这一条。",
                })
                continue

            if action == "attribute":
                because = str(cmd.get("because") or cmd.get("analysis") or "").strip()
                module = str(cmd.get("module") or "").strip()
                need = str(cmd.get("need_feed") or "").strip()
                if not because and not module and not need:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": "attribute 需要 module、because 或 need_feed。attribute 不查日志。",
                    })
                    continue
                if need:
                    result.need_feed = need
                result.attributions.append({
                    "module": module,
                    "because": because,
                    "need_feed": need,
                })
                shown = because or module or need
                _p({
                    "id": f"log_r{round_num}",
                    "description": f"归因：{shown[:40]}",
                    "status": "completed",
                    "capability": "log_analyze",
                    "phase": "done",
                })
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": "已记录归因。证据够了就 conclude；还要验证就 query。",
                })
                continue

            if action == "shell" and result.queries_made < self.SOFT_LIMIT:
                if narrow_queries == 0:
                    messages.append({
                        "role": "user",
                        "content": "请先用 query 按锚定的时间窗/对象做窄查询，不要在查询之前 shell。",
                    })
                    continue
                raw_cmd = str(cmd.get("cmd") or "").strip()
                if not raw_cmd:
                    messages.append({
                        "role": "user",
                        "content": "shell 缺少 cmd。请给出白名单命令，例如 grep -n 'ERROR' | head -n 50。",
                    })
                    continue
                if any(raw_cmd == h for h in query_history if isinstance(h, str)):
                    messages.append({
                        "role": "user",
                        "content": "这条 shell 与之前一轮相同。请换关键词或改用 query 缩窗。",
                    })
                    continue
                query_history.append(raw_cmd)
                from ai.agents.AiTaskPlatform.log_analyzer.shell_tool import run_readonly_shell
                ok, shell_text, n_lines = await asyncio.to_thread(
                    run_readonly_shell, raw_cmd, self.log_path,
                )
                result.queries_made += 1
                logger.info(
                    f"LogSubAgent R{round_num} shell ok={ok} lines={n_lines} "
                    f"cmd={raw_cmd[:80]}"
                )
                if ok:
                    _collect_shell_evidence(result, shell_text)
                _p({
                    "id": f"log_r{round_num}",
                    "description": f"日志 shell R{round_num}：{n_lines} 行（{cmd.get('analysis','')[:40]}）",
                    "status": "completed",
                    "capability": "log_analyze",
                    "phase": "done",
                    "result_summary": f"R{round_num} shell: {n_lines} lines",
                })
                feedback = shell_text
                if result.queries_made >= self.SOFT_LIMIT:
                    feedback += f"\n\n(已查{result.queries_made}轮，接近上限，请尽快conclude或fallback)"
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": feedback})

            elif action in ("query", "contrast") and result.queries_made < self.SOFT_LIMIT:
                if action == "contrast" and not isinstance(cmd.get("query"), dict):
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": "contrast 必须带 query，用来比较故障窗和故障前的正常窗。",
                    })
                    continue
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
                narrow_queries += 1

                matched = _matched_lines(query_result)
                logger.info(f"LogSubAgent R{round_num}: {cmd.get('analysis','?')[:60]} → {matched} lines")

                _collect_evidence(result, query_result, round_num)

                _step = "对照" if action == "contrast" else "查询"
                _p({
                    "id": f"log_r{round_num}",
                    "description": f"日志{_step} R{round_num}：命中 {matched} 行（{cmd.get('analysis','')[:40]}）",
                    "status": "completed",
                    "capability": "log_analyze",
                    "phase": "done",
                    "result_summary": f"R{round_num}: {matched} lines",
                })

                _release_older_log_feedback(messages)
                feedback = _feedback_to_llm(
                    query_result, matched,
                    room_tokens=_log_feedback_room(messages, response or ""),
                )
                if _feedback_is_too_broad(matched):
                    feedback += (f"\n\n⚠ 本轮命中 {matched} 行过多，说明过滤条件太宽。"
                                 "请从「日志客观事实」里挑一个真实车型/任务，或把时间窗缩窄到错误密集时段再查询；"
                                 "找不到合适过滤条件就 conclude 或 fallback。")
                if result.queries_made >= self.SOFT_LIMIT:
                    feedback += f"\n\n(已查{result.queries_made}轮，接近上限，请尽快conclude或fallback)"

                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": feedback})

            else:
                if action == "conclude":
                    result.conclusion = cmd.get("conclusion", "") or _salvage_conclusion(response)
                    result.need_feed = str(cmd.get("need_feed") or "").strip()
                    try:
                        if cmd.get("confidence") is not None:
                            result.confidence = max(0.0, min(1.0, float(cmd.get("confidence"))))
                    except (TypeError, ValueError):
                        result.confidence = None
                    _filter_evidence_by_citation(result, cmd.get("evidence_lines", []))
                    logger.info(f"LogSubAgent conclude at R{round_num}: {result.conclusion[:80]} (evidence={len(result.evidence)})")
                elif action == "fallback":
                    result.conclusion = cmd.get("reason", "无结论")
                    result.need_feed = str(cmd.get("need_feed") or "").strip()
                    result.fallback_used = True
                    logger.info(f"LogSubAgent fallback at R{round_num}: {result.conclusion[:80]}")
                _p({
                    "id": f"log_r{round_num}",
                    "description": "日志分析得出结论",
                    "status": "completed",
                    "capability": "log_analyze",
                    "phase": "done",
                    "result_summary": (result.conclusion or "")[:80],
                })
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
                sample.append({"line": int(ln), "summary": summary[:280]})
            if len(sample) >= 30:
                break
        # 命中行不足时补充 context 行
        if len(sample) < 10:
            for line_match in re.findall(r"  L(\d+)\| (.*)", query_result):
                ln, sm = line_match
                summary = sm.strip()
                if summary:
                    sample.append({"line": int(ln), "summary": summary[:280]})
                if len(sample) >= 15:
                    break

        return {
            "matched": matched,
            "text": _feedback_to_llm(query_result, matched),
            "sample": sample,
            "evidence": result_evidence,
        }


# ── 辅助函数 ────────────────────────────────────────────────

def _build_context(task: Dict, question: str) -> str:
    """日志分析开头的工单上下文。描述和讨论用原文，不预先截断。"""
    task = task if isinstance(task, dict) else {}
    parts = ["## 工单信息"]
    if task.get("title"):
        parts.append(f"标题: {task['title']}")
    if task.get("problem_summary"):
        parts.append(f"问题概述: {task['problem_summary']}")
    if task.get("description"):
        parts.append(f"描述: {task['description']}")
    if task.get("occurrence_time"):
        parts.append(f"发生时间: {task['occurrence_time']}")
    if task.get("location"):
        parts.append(f"地点: {task['location']}")
    if task.get("hypotheses"):
        parts.append(f"推测原因: {' / '.join(task['hypotheses'])}")
    if task.get("ruled_out"):
        parts.append(f"已排除: {' / '.join(task['ruled_out'])}")
    if task.get("robot_type"):
        parts.append(f"车型: {task['robot_type']}")
    if task.get("fault_code"):
        parts.append(f"故障码: {task['fault_code']}")
    facts = (task.get("project_facts") or "").strip()
    if facts:
        parts.append(f"现场档案（车型与调度版本）:\n{facts}")
    if task.get("collected_info"):
        ci = task["collected_info"]
        if isinstance(ci, dict):
            for k, v in ci.items():
                parts.append(f"{k}: {v}")
    summaries = task.get("attachment_summaries") or []
    if summaries:
        parts.append("已有附件摘要:")
        for line in summaries:
            if line:
                parts.append(f"- {line}")
    discussion = (task.get("discussion") or "").strip()
    if discussion:
        parts.append(f"\n## 本工单讨论\n{discussion}")
    if question:
        parts.append(f"\n## 用户问题\n{question}")
    parts.append("\n---")
    parts.append("请按 7 步方法论推进：先用首轮锚定的时间窗/对象做窄查询，不要扫全量日志。")
    return "\n".join(parts)


async def _fit_ticket_opening(task: Dict, question: str, llm_client) -> str:
    """工单上下文未到窗口时原样放入；到了窗口才压缩描述和更早讨论。"""
    from ai.agents.AiTaskPlatform.contexts.history_compact import (
        context_limit,
        estimate_tokens,
        fit_ticket_history,
    )

    text = _build_context(task, question)
    if estimate_tokens(text) <= context_limit():
        return text
    lines = [ln for ln in (task.get("discussion") or "").split("\n") if ln.strip()]
    fixed = "\n".join(filter(None, [
        task.get("title") or "",
        task.get("problem_summary") or "",
        task.get("project_facts") or "",
        question or "",
    ]))
    try:
        desc, disc = await fit_ticket_history(
            description=task.get("description") or "",
            comment_lines=lines,
            fixed_text=fixed,
            llm_client=llm_client,
        )
    except Exception as e:
        logger.warning(f"LogSubAgent 工单上下文压缩失败，保留原文: {type(e).__name__}: {e}")
        return text
    folded = dict(task)
    folded["description"] = desc
    folded["discussion"] = disc
    logger.info("[log] 工单上下文到达窗口，压缩描述和更早讨论后再分析日志")
    return _build_context(folded, question)


# ── 查询参数的可信校验与夹紧 ─────────────────────────────────

_BROAD_WINDOW_HINT_LINES = 200  # 命中超过此数量视为太宽，提示缩窄


def _message_tokens(messages: list) -> int:
    from ai.agents.AiTaskPlatform.contexts.history_compact import estimate_tokens
    return sum(estimate_tokens(str(m.get("content") or "")) for m in messages or [])


def _log_feedback_room(messages: list, extra: str = "") -> int:
    from ai.agents.AiTaskPlatform.contexts.history_compact import context_limit, estimate_tokens
    return context_limit() - _message_tokens(messages) - estimate_tokens(extra) - 1500


def _release_older_log_feedback(messages: list) -> None:
    """整段对话已经顶到窗口时，更早的查询回灌让位，最近一次查询保持原文。"""
    from ai.agents.AiTaskPlatform.contexts.history_compact import context_limit, estimate_tokens
    limit = context_limit()
    if _message_tokens(messages) <= limit:
        return
    idxs = [
        i for i, m in enumerate(messages)
        if m.get("role") == "user" and str(m.get("content") or "").startswith("查询结果")
    ]
    for i in idxs[:-1]:
        if _message_tokens(messages) <= limit:
            break
        messages[i]["content"] = "此前查询结果已让出窗口，以最近一次查询为准。"
        if estimate_tokens(messages[i]["content"]) < 0:
            break


def _feedback_to_llm(query_result: str, matched: int, room_tokens: int | None = None) -> str:
    """查询回灌：放得进剩余窗口就用原文；放不下才压成高频片段和首尾样例。"""
    from ai.agents.AiTaskPlatform.contexts.history_compact import context_limit, estimate_tokens
    header = f"查询结果(命中{matched}行)"
    raw = query_result or ""
    room = context_limit() if room_tokens is None else max(int(room_tokens), 200)
    if estimate_tokens(raw) <= room:
        return f"{header}:\n{raw}"

    hits = re.findall(r"\* L(\d+)\| (.*)", raw)
    from collections import Counter
    phrases = Counter()
    for _ln, sm in hits:
        text = (sm or "").strip()
        for tok in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_\-]{2,}|ERR=\S+|0x[0-9A-Fa-f]+", text):
            phrases[tok] += 1
    top = phrases.most_common(8)
    freq = "、".join(f"{k}×{v}" for k, v in top) if top else "（无稳定短语）"
    samples = hits[:8] + (hits[-4:] if len(hits) > 8 else [])
    # 去重保序
    seen = set()
    sample_lines = []
    for ln, sm in samples:
        if ln in seen:
            continue
        seen.add(ln)
        sample_lines.append(f"* L{ln}| {(sm or '').strip()[:280]}")
    body = (
        f"{header}，超过窗口，已压成高频片段和样例，禁止据此假装读完全量。\n"
        f"高频片段: {freq}\n"
        f"样例 {len(sample_lines)} 行:\n" + "\n".join(sample_lines)
    )
    if estimate_tokens(body) <= room:
        return body
    return body[:room] + "\n（日志回灌仍超过窗口，样例已再截短）"


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

    # 2) 车型/任务：伪造纯数字置空；命中索引时保留用户短 ID（如 agv-0011），
    #    让 query 用 `fval in key` 覆盖所有实例后缀（agv-0011_2026...），禁止收成单一实例。
    robot = (q.get("robot_filter") or "").strip()
    if robot:
        hit = idx.valid_robot(robot)
        if hit:
            q["robot_filter"] = robot if robot in hit else hit
        elif re.search(r"[A-Za-z]", robot):
            q["robot_filter"] = robot
        else:
            q["robot_filter"] = ""
    else:
        q["robot_filter"] = ""

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


_LOG_ACTIONS = (
    "query", "shell", "conclude", "fallback",
    "anchor", "hypothesis", "contrast", "attribute",
)


def _parse_llm_command(raw: str) -> Optional[Dict]:
    """从 LLM 输出中提取最可信的一条命令。
    优先形状正确的 JSON；越靠后的完整命令越可能是最终意图。
    """
    best = None
    best_score = -1
    for obj in _iter_json_objects(raw):
        if not isinstance(obj, dict):
            continue
        action = obj.get("action")
        if action not in _LOG_ACTIONS:
            continue
        score = 0
        if action == "query" and isinstance(obj.get("query"), dict):
            score = 3
        elif action == "contrast" and isinstance(obj.get("query"), dict):
            score = 3
        elif action == "shell" and obj.get("cmd"):
            score = 3
        elif action == "conclude" and obj.get("conclusion"):
            score = 3
        elif action == "fallback" and obj.get("reason"):
            score = 2
        elif action == "anchor" and (obj.get("phenomenon") or obj.get("time_start") or obj.get("robot")):
            score = 3
        elif action == "hypothesis" and obj.get("hypothesis"):
            score = 3
        elif action == "attribute" and (obj.get("because") or obj.get("module") or obj.get("need_feed")):
            score = 3
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
        if not isinstance(h, dict):
            continue
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
        result.evidence.append({"line": int(ln), "summary": sm[:280], "round": round_num})


def _collect_shell_evidence(result: LogAnalysisResult, shell_text: str) -> None:
    """grep -n 的「行号:正文」收进证据，供 conclude 引用。"""
    for ln, sm in re.findall(r"^(\d{1,8})[:|](.*)$", shell_text or "", re.M):
        summary = (sm or "").strip()
        if not summary:
            continue
        line_no = int(ln)
        if any(e["line"] == line_no for e in result.evidence):
            continue
        result.evidence.append({"line": line_no, "summary": summary[:280], "round": 0})
        if len(result.evidence) >= 20:
            break


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
        evidence.append({"line": int(ln), "summary": sm[:280]})
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
                      "超时", "失败", "ERR=", "CANCELED", "拒绝", "锁区", "回调", "状态机",
                      "occupy", "release")
        result.evidence = [
            e for e in result.evidence
            if any(kw in e["summary"] for kw in _SIGNAL_KW)
        ]
