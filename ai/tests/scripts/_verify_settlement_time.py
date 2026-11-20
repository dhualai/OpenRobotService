#!/usr/bin/env python3
"""项目维度时间口径验证：settlement_period（业绩核算期）月份过滤

覆盖：
1. metric_planner._extract_time 月份词解析（「9月份」→ custom 当月 1 号至月末；
   未来月份回退一年；「近3个月」时长表达不误入；「9月7号」绝对日期优先）
2. report_generator._norm_settlement_period 各形态归一化（YYYYMM / YYYY-MM / 异常值）
3. report_generator._settlement_month_keys 窗口 → 月份集合
4. AnalysisPlanner 快路径「9月份有多少项目」→ custom + explicit + 项目维度兜底指标
5. ReportDataCollector._collect_project_metrics 显式时间下按 settlement_period 过滤
   （fake db 注入，不连真实数据库）

用法：python ai/tests/_verify_settlement_time.py
"""
from __future__ import annotations

import asyncio
import importlib.util as _ilu
import sys
import types
from datetime import date, datetime
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent.parent
_dap_dir = _project_root / "ai" / "agents" / "AiDataAnalysisPlatform"


def _load_submodule(pkg_name: str, path: Path):
    spec = _ilu.spec_from_file_location(pkg_name, str(path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub_load():
    ai_pkg = types.ModuleType("ai")
    ai_pkg.__path__ = [str(_project_root / "ai")]
    agents_pkg = types.ModuleType("ai.agents")
    agents_pkg.__path__ = [str(_project_root / "ai" / "agents")]
    dap_pkg = types.ModuleType("ai.agents.AiDataAnalysisPlatform")
    dap_pkg.__path__ = [str(_dap_dir)]
    ai_core_pkg = types.ModuleType("ai.core")
    ai_core_pkg.__path__ = [str(_project_root / "ai" / "core")]
    sys.modules.setdefault("ai", ai_pkg)
    sys.modules.setdefault("ai.agents", agents_pkg)
    sys.modules.setdefault("ai.agents.AiDataAnalysisPlatform", dap_pkg)
    sys.modules.setdefault("ai.core", ai_core_pkg)
    _load_submodule("ai.core.database", _project_root / "ai" / "core" / "database.py")

    mods = {}
    for name in ["logging_config", "metric_registry", "schemas", "prompts",
                 "llm_client", "config", "report_prompts", "report_schemas",
                 "metric_planner", "report_generator"]:
        mods[name] = _load_submodule(
            f"ai.agents.AiDataAnalysisPlatform.{name}", _dap_dir / f"{name}.py",
        )
    return mods


mods = _stub_load()
planner_mod = mods["metric_planner"]
rg_mod = mods["report_generator"]

checks: list[tuple[bool, str]] = []


def check(ok: bool, label: str):
    checks.append((bool(ok), label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


# ── 1. 月份词解析 ──────────────────────────────────────────────
print("1. _extract_time 月份词解析")
extract = planner_mod._extract_time

today = date.today()
# 「3月份」→ 当年 3 月 1 号至 3 月末（3 月必为过去或当前月，年份不回退）
t, explicit, days, s, e, label = extract("3月份有多少项目")
check(t == "custom" and explicit is True, "「3月份」→ custom 显式")
check(s == f"{today.year}-03-01" and e == f"{today.year}-03-31", f"「3月份」起止 = 当年 3 月整月（{s}~{e}）")
check(label == "3月份", "label = 「3月份」")

# 未来月份回退一年（如今天 2026-09，问「12月份」→ 2025-12）
future_month = 12 if today.month < 12 else 11
t2, _, _, s2, e2, _ = extract(f"{future_month}月份有多少项目")
expect_year = today.year - 1 if (today.year, future_month) > (today.year, today.month) else today.year
check(s2.startswith(f"{expect_year}-{future_month:02d}"), f"未来月份「{future_month}月份」回退 → {s2}~{e2}")

# 时长表达不误入
t3, explicit3, _, _, _, _ = extract("近3个月有多少项目")
check(t3 == "recent_days" and explicit3 is False, "「近3个月」时长表达不误入月份词")

# 绝对日期优先于月份词
t4, _, _, s4, e4, _ = extract("9月7号有多少项目")
check(t4 == "custom" and s4 == e4, "「9月7号」绝对日期优先（单日）")

# ── 2. settlement_period 归一化 ────────────────────────────────
print("2. _norm_settlement_period 归一化")
norm = rg_mod._norm_settlement_period
check(norm("202608") == "202608", "'202608' 原样")
check(norm("2026-08") == "202608", "'2026-08' → '202608'")
check(norm("2026-8") == "202608", "'2026-8' → '202608'")
check(norm(" 2026/09 ") == "202609", "' 2026/09 ' → '202609'")
check(norm("") == "" and norm(None) == "", "空值 → ''")
check(norm("abc") == "abc", "非数字原样返回")
check(norm("2026-13") == "2026-13", "非法月份原样返回")

# ── 3. 窗口 → 月份集合 ────────────────────────────────────────
print("3. _settlement_month_keys 窗口月份集合")
months = rg_mod._settlement_month_keys
mk1 = months(datetime(2026, 9, 1), datetime(2026, 9, 30))
check(mk1 == {"202609"}, "9 月整月窗口 → {'202609'}")
mk2 = months(datetime(2026, 8, 15), datetime(2026, 9, 15))
check(mk2 == {"202608", "202609"}, "跨月窗口 → {202608, 202609}")
mk3 = months(datetime(2025, 12, 31), datetime(2026, 1, 1))
check(mk3 == {"202512", "202601"}, "跨年窗口 → {202512, 202601}")

# ── 4. 快路径「9月份有多少项目」────────────────────────────────
print("4. 快路径 plan 解析")
planner = planner_mod.AnalysisPlanner(None)


def _fast_plan(text: str):
    return planner_mod._fast_path_parse(text)


plan = _fast_plan("9月份有多少项目")
if plan is None:
    check(False, "「9月份有多少项目」快路径产出 plan")
else:
    check(set(plan.metric_keys) == {"project.total", "project.active_count", "project.by_status"},
          f"metric_keys = 项目维度兜底（{sorted(plan.metric_keys)}）")
    check(plan.time_range.type == "custom" and plan.time_range.explicit is True,
          f"time_range = custom 显式（{plan.time_range.start}~{plan.time_range.end}）")

plan2 = _fast_plan("有多少项目")
if plan2 is None:
    check(False, "「有多少项目」快路径产出 plan")
else:
    check(plan2.time_range.explicit is False, "「有多少项目」无时间 → 非显式（不过滤）")

# ── 5. 采集层过滤（fake db）────────────────────────────────────
print("5. _collect_project_metrics 显式时间过滤")


class _FakeProject:
    def __init__(self, pid: str, status: str = "active", settlement: str | None = None):
        self.id = pid
        self.code = pid
        self.name = f"项目{pid}"
        self.status = status
        self.settlement_period = settlement
        self.issues = 0
        self.risks = 0
        self.contact_person = ""


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, rows, reported_ids=()):
        self._rows = rows
        self._reported = reported_ids

    def query(self, *args):
        # no_data 判定查 collection_data 上报项目列；其余查项目表
        if args and args[0] is rg_mod.CollectionData.project:
            return _FakeQuery([(pid,) for pid in self._reported])
        return _FakeQuery(self._rows)

    def close(self):
        pass


# 4 个项目：2 个 202609、1 个 202608、1 个空
rows = [
    _FakeProject("A", settlement="202609"),
    _FakeProject("B", settlement="2026-09"),
    _FakeProject("C", settlement="202608"),
    _FakeProject("D", settlement=None),
]

collector = rg_mod.ReportDataCollector.__new__(rg_mod.ReportDataCollector)
collector._project_ids = None
collector._get_db = lambda: _FakeDb(rows)

win = (datetime(2026, 9, 1), datetime(2026, 9, 30))
r_all = collector._collect_project_metrics({"project.total"}, *win, explicit_time=False)
check(r_all["total"] == 4, "非显式时间 → 全部 4 个项目")

r_sep = collector._collect_project_metrics({"project.total"}, *win, explicit_time=True)
check(r_sep["total"] == 2, "显式 9 月 → 仅 settlement_period ∈ {202609} 的 2 个（含 '2026-09' 归一化）")

r_active = collector._collect_project_metrics({"project.active_count"}, *win, explicit_time=True)
check(r_active["active_count"] == 2, "过滤后 active_count 基于过滤集计算")

r_status = collector._collect_project_metrics({"project.by_status"}, *win, explicit_time=True)
check(r_status.get("by_status") == {"进行中": 2}, "过滤后 by_status 分布基于过滤集")

# no_data_items 口径：判定对象为全部用户关联项目，不受 settlement_period
# 显式时间过滤影响（问的是 collection_data 采集数据，不是项目口径）。
# 模拟本周窗口内 A、C 有 GroupEfficiency 上报 → 无数据应为 B、D
collector2 = rg_mod.ReportDataCollector.__new__(rg_mod.ReportDataCollector)
collector2._project_ids = None
collector2._get_db = lambda: _FakeDb(rows, reported_ids=("A", "C"))

r_no = collector2._collect_project_metrics(
    {"project.no_data_items"}, *win, explicit_time=True
)
check({i["项目ID"] for i in r_no["no_data_list"]} == {"B", "D"},
      "显式 9 月：no_data 判定含 settlement 为空/非 9 月项目（B、D）")
check(r_no["no_data_count"] == 2 and r_no["with_data_count"] == 2,
      "no_data_count=2 / with_data_count=2（全集口径）")

r_no_all = collector2._collect_project_metrics(
    {"project.no_data_items"}, *win, explicit_time=False
)
check(r_no_all["no_data_count"] == 2, "非显式时间 no_data 同样基于全集（口径一致）")

r_total2 = collector2._collect_project_metrics(
    {"project.total"}, *win, explicit_time=True
)
check(r_total2["total"] == 2, "项目自身指标（total）仍按 settlement_period 过滤")

# ── 6. 提示词日期注入（LLM 不知道“今天”，必须在提示词里告知）────
print("6. 提示词当前日期注入与月份年份规则")
prompts_mod = mods["prompts"]
_today_str = date.today().strftime("%Y-%m-%d")

agentic_prompt = prompts_mod.build_agentic_system_prompt()
check(_today_str in agentic_prompt, f"agentic 系统提示词包含今天日期 {_today_str}")
check("只提月份" in agentic_prompt and "默认使用当前年份" in agentic_prompt,
      "agentic 系统提示词包含月份默认当年规则")

parser_prompt = prompts_mod.build_plan_parser_user_prompt("9月份有多少项目")
check(_today_str in parser_prompt, f"plan parser 提示词包含今天日期 {_today_str}")
check("只提月份" in parser_prompt and "默认当前年份" in parser_prompt,
      "plan parser 提示词包含月份默认当年规则")

# ── 汇总 ───────────────────────────────────────────────────────
failed = [label for ok, label in checks if not ok]
print(f"\nSETTLEMENT TIME CHECKS: {len(checks) - len(failed)}/{len(checks)} 通过")
if failed:
    print("失败项：")
    for label in failed:
        print(f"  - {label}")
    sys.exit(1)
print("ALL SETTLEMENT TIME CHECKS PASSED")
