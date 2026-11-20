"""Actor 识别：路径优先，正文兜底；压缩包内 debug_logs.log 也能分清。"""
from __future__ import annotations

import os
from pathlib import Path

from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import (
    _detect_algo_log_module,
    match_log_path_for_need_feed,
    order_logs_for_analysis,
)


def test_tms_map_parent_dir_not_generic_tms(tmp_path: Path):
    p = tmp_path / "TMS-MAP-DK-USPA-LOGS-" / "debug_logs.log"
    p.parent.mkdir()
    p.write_text("timestamp SERVICES-ALIVE\n", encoding="utf-8")
    assert _detect_algo_log_module(str(p)) == "tms_map"


def test_sniff_dynamic_map_generic_name(tmp_path: Path):
    p = tmp_path / "debug_logs.log"
    p.write_text(
        "Locate Robot 12\n[LOCATE SUCCESS]\n更新机器人数据---{'id': 12}\n接收到TMS路径\n",
        encoding="utf-8",
    )
    assert _detect_algo_log_module(str(p)) == "dynamic_map"


def test_sniff_tms_map_generic_name(tmp_path: Path):
    p = tmp_path / "debug_logs.log"
    p.write_text(
        "新任务-----12:{'goal': 1}\n路径规划开始-----map=DK\n求解成功---ok\n",
        encoding="utf-8",
    )
    assert _detect_algo_log_module(str(p)) == "tms_map"


def test_dmap_xinrenwu_colon_is_not_tms_map(tmp_path: Path):
    p = tmp_path / "debug_logs.log"
    p.write_text("Robot:12----新任务：{'node': 3}---\nLocate Robot 12\n", encoding="utf-8")
    assert _detect_algo_log_module(str(p)) == "dynamic_map"


def test_order_planning_stuck_prefers_dmap(tmp_path: Path):
    dmap = tmp_path / "a" / "debug_logs.log"
    tms = tmp_path / "TMS-MAP-DK-USPA-LOGS-" / "debug_logs.log"
    dmap.parent.mkdir()
    tms.parent.mkdir()
    dmap.write_text("[LOCATE SUCCESS]\n更新机器人数据---x\n", encoding="utf-8")
    tms.write_text("路径规划开始-----x\n求解成功---y\n", encoding="utf-8")
    ordered = order_logs_for_analysis([str(tms), str(dmap)], "路径规划中车不动")
    assert ordered[0] == str(dmap)
    assert ordered[1] == str(tms)


def test_need_feed_matches_parent_tms_map(tmp_path: Path):
    p = tmp_path / "TMS-MAP-DK-USPA-LOGS-" / "debug_logs.log"
    p.parent.mkdir()
    p.write_text("路径规划开始-----x\n", encoding="utf-8")
    other = tmp_path / "DYNAMIC_MAP-SERVICES-ACTOR-USPA-LOGS-" / "debug_logs.log"
    other.parent.mkdir()
    other.write_text("[LOCATE SUCCESS]\n", encoding="utf-8")
    got = match_log_path_for_need_feed("需要 TMS-MAP-DK 规划原文", [str(other), str(p)])
    assert got and os.path.normcase(got) == os.path.normcase(str(p))
