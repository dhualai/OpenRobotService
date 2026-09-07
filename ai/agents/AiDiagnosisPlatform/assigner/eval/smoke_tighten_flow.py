"""
候选收紧流程冒烟测试：使用真实工程师画像，验证 部门 → 产品（模块层已从收紧中移除）。

用法（项目根目录）：
    python ai/agents/AiDiagnosisPlatform/assigner/eval/smoke_tighten_flow.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[5]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_backend_dir = _project_root / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.candidate_tightener import CandidateTightener
from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig

# 真实工程师画像备份（与 test_hardware_dispatch 同源）
ENGINEERS_JSON_CANDIDATES = [
    Path(__file__).resolve().parents[6] / "assigner_data_backup" / "engineers.json",
    _project_root / "ai" / "agents" / "AiDiagnosisPlatform" / "assigner" / "eval" / "data" / "engineers.json",
]


def load_engineers_from_json() -> list[EngineerProfile]:
    path = next((p for p in ENGINEERS_JSON_CANDIDATES if p.exists()), None)
    if not path:
        raise FileNotFoundError(
            "未找到 engineers.json，请放置于 D:/CodeHub/AI/assigner_data_backup/engineers.json"
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    profiles = []
    for i, item in enumerate(raw):
        name = (item.get("name") or "").strip()
        if not name:
            continue
        modules = item.get("responsibility_modules") or {}
        if isinstance(modules, list):
            modules = {"其他": modules}
        profiles.append(EngineerProfile(
            # 优先 users.id；旧 JSON 仅有 username 时回退，不改案例内容
            id=item.get("id") or item.get("username") or f"user_{i:03d}",
            name=name,
            department=item.get("department") or "",
            responsibility_modules=modules,
            job_level=int(item.get("job_level") or 1),
            duty_text=item.get("duty_text"),
        ))
    print(f"  加载工程师画像: {path} ({len(profiles)} 人)")
    return profiles


CASES = [
    {
        "name": "硬件-strong→机器人事业部",
        "ticket": TicketContext(
            id="smoke_hw",
            title="AGV左前轮脱落，车体碰撞变形",
            problem_description="潜伏车轮子脱落，车体底部碰撞，需要维修",
            status="new",
        ),
        "expect_dept": "机器人事业部",
        "expect_dept_mode": "hard_filter",
        "expect_names": {"文永翔"},
        "forbid_names": set(),
    },
    {
        "name": "定位-strong→移动研究院(非徐浩南)",
        "ticket": TicketContext(
            id="smoke_slam",
            title="车辆定位漂移严重",
            problem_description="重定位失败，激光雷达数据异常",
            status="new",
        ),
        "expect_dept": "智能移动研究院",
        "expect_dept_mode": "hard_filter",
        "expect_names_subset": {"朱永丰"},
        "forbid_names": {"徐浩南"},  # 徐浩南是规划院算法，不负责车端定位
    },
    {
        "name": "地图编辑→规划院+调度USP",
        "ticket": TicketContext(
            id="smoke_dispatch",
            title="地图编辑无法删除已有库位",
            problem_description="编辑地图时库位删不掉，保存失败",
            status="new",
            project_name="四川峨眉山乐飞光电混场项目",
        ),
        "expect_dept": "智能规划研究院",
        "expect_product": "调度USP",
        "forbid_names": {"朱永丰", "文永翔"},
    },
    {
        "name": "摇人吧系统任务→排除吴佳秀",
        "ticket": TicketContext(
            id="smoke_yaoren",
            title="系统任务页面加载失败",
            problem_description="打开系统任务白屏",
            status="new",
            project_name="摇人吧服务号提单",
        ),
        "expect_product": "摇人吧服务号",
        "forbid_names": {"吴佳秀", "徐浩南"},  # 无服务号产品职责
        "expect_names_subset": {"张文星", "张俊磊", "贾爽"},  # 负责服务号/系统任务
    },
]


def _print_tighten(case_name: str, tighten, engineers: list[EngineerProfile]):
    names = [e.name for e in tighten.candidates]
    print(f"\n{'─' * 60}")
    print(f"用例: {case_name}")
    print(f"  收紧: {tighten.before_count} → {tighten.after_count} 人")
    print(
        f"  Layer1 部门: mode={tighten.dept.mode} "
        f"primary={tighten.dept.primary_dept or '-'} "
        f"({tighten.dept.reasoning[:50]})"
    )
    print(
        f"  Layer2 产品: mode={tighten.product.mode} "
        f"product={tighten.product.product or '-'} ({tighten.product.source})"
    )
    print("  Layer3 模块: 已从收紧中移除（不再做模块收紧）")
    print(f"  候选人: {names}")
    for e in tighten.candidates[:8]:
        prods = list((e.responsibility_modules or {}).keys())
        print(f"    - {e.name} | {e.department} | products={prods}")


def _check_case(case: dict, tighten, ok: list, fail: list):
    name = case["name"]
    errors = []
    names = {e.name for e in tighten.candidates}

    if "expect_dept" in case and tighten.dept.primary_dept != case["expect_dept"]:
        errors.append(
            f"部门期望 {case['expect_dept']} 实际 {tighten.dept.primary_dept}"
        )
    if "expect_dept_mode" in case and tighten.dept.mode != case["expect_dept_mode"]:
        errors.append(f"部门mode期望 {case['expect_dept_mode']} 实际 {tighten.dept.mode}")
    if "expect_product" in case and tighten.product.product != case["expect_product"]:
        errors.append(f"产品期望 {case['expect_product']} 实际 {tighten.product.product}")
    if "expect_names" in case and names != case["expect_names"]:
        errors.append(f"候选人期望 {case['expect_names']} 实际 {names}")
    if "expect_names_subset" in case and not (names & case["expect_names_subset"]):
        errors.append(f"候选人应含其一 {case['expect_names_subset']} 实际 {names}")
    forbidden = case.get("forbid_names") or set()
    bad = names & forbidden
    if bad:
        errors.append(f"不应出现 {bad}")

    if errors:
        fail.append(name)
        print(f"  [FAIL]: {'; '.join(errors)}")
    else:
        ok.append(name)
        print(f"  [PASS]")


async def run_cases(with_llm: bool = False):
    cfg = AssignerConfig()
    if not with_llm:
        routing = dict(cfg.department_routing or {})
        llm = dict(routing.get("llm") or {})
        llm["enabled"] = False
        routing["llm"] = llm
        hist = dict(routing.get("history") or {})
        hist["enabled"] = False
        routing["history"] = hist
        cfg.department_routing = routing

    tightener = CandidateTightener(config=cfg)
    engineers = load_engineers_from_json()
    ok, fail = [], []

    print("=" * 60)
    print("  候选收紧流程冒烟测试（真实工程师画像）")
    print(f"  R2/R3: {'开' if with_llm else '关(仅R5)'}")
    print("=" * 60)

    for case in CASES:
        tighten = await tightener.tighten(case["ticket"], engineers)
        _print_tighten(case["name"], tighten, engineers)
        _check_case(case, tighten, ok, fail)

    print(f"\n{'=' * 60}")
    print(f"  结果: {len(ok)} 通过 / {len(fail)} 失败 / {len(CASES)} 总计")
    if fail:
        print(f"  失败: {', '.join(fail)}")
    print("=" * 60)
    return len(fail) == 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--with-llm", action="store_true")
    args = p.parse_args()
    passed = asyncio.run(run_cases(with_llm=args.with_llm))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
