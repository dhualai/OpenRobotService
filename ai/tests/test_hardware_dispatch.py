"""车体硬件工单派单全链路测试 — 验证能分到机器人事业部的 L2 而非智能规划的 L1"""
import sys, asyncio, json, os, time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 加载人员 JSON（模拟 users 表）
JSON_PATH = Path("D:/CodeHub/AI/assigner_data_backup/engineers.json")
if not JSON_PATH.exists():
    print(f"人员 JSON 不存在: {JSON_PATH}")
    sys.exit(1)

with open(JSON_PATH, "r", encoding="utf-8") as f:
    raw = json.load(f)

# 给每个人填临时 id（模拟 users.id）
for i, item in enumerate(raw):
    item["id"] = item["id"] or f"user_temp_{i:03d}"

print(f"加载 {len(raw)} 人\n")

# ── 部门分布 ──
from collections import Counter
dept_counts = Counter(item.get("department") or item.get("department", "") for item in raw)
for dept, count in dept_counts.items():
    people = [f'{p["name"]}(L{p["job_level"]})' for p in raw if p.get("department") == dept]
    print(f"  {dept}: {count}人 — {', '.join(people[:5])}{'...' if len(people) > 5 else ''}")
print()

# ── 构建 EngineerProfile ──
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.candidate_tightener import CandidateTightener

profiles = []
for item in raw:
    name = item.get("name", "")
    if not name:
        continue
    modules = item.get("responsibility_modules") or {}
    if isinstance(modules, list):
        modules = {"其他": modules}
    profiles.append(EngineerProfile(
        id=item.get("id", f"u_{name}"),
        name=name,
        department=item.get("department") or "",
        responsibility_modules=modules,
        job_level=item.get("job_level", 1),
        duty_text=item.get("duty_text"),
    ))

print(f"={ '=' * 60 }=")
print("测试 1: 车体硬件工单")
print(f"={ '=' * 60 }=")

ticket = TicketContext(
    id="test_hw_001",
    title="AGV车轮脱落，车体碰撞损坏",
    problem_description="潜伏车在运行中左前轮脱落，车体底部碰撞地面变形，需要维修",
    status="new",
    priority="高",
    ticket_type="problem",
)

print(f"\n[工单] {ticket.title}")
print(f"  {ticket.problem_description}")

# ── Step 1: 部门收紧 ──
print(f"\n{'─' * 60}")
print("Step 1: 部门收紧 (CandidateTightener Layer1)")

tightener = CandidateTightener()

async def _dept_step():
    tighten = await tightener.tighten(ticket, profiles)
    dept = tighten.dept.primary_dept
    print(f"  匹配结果: \"{dept}\" mode={tighten.dept.mode}")
    print(f"  候选人数: {len(tighten.candidates)} (收紧前 {tighten.before_count})")
    for c in tighten.candidates:
        prods = list(c.responsibility_modules.keys())
        print(f"    {c.name} L{c.job_level} {c.department} products={prods}")
    return dept, tighten.candidates

dept, candidates = asyncio.run(_dept_step())

if not candidates:
    print("  ⚠️ 部门过滤后无候选人！回退全量")
    candidates = profiles

# ── Step 1: 召回（测试模式 — 跳过 LLM 和 Embedding，只做模拟）──
print(f"\n{'─' * 60}")
print("Step 1: 三路召回（模拟）")

# 模拟 L1 LLM 打分：机器人事业部的人给高分
sim_llm_scores = {}
sim_sem_scores = {}
sim_hist_scores = {}

robot_dept = "机器人事业部"

for c in candidates:
    # LLM 模拟：硬件问题 → 机器人事业部 > 车端软件 > 智能规划
    if c.department == robot_dept:
        sim_llm_scores[c.id] = 0.95  # 唯一匹配
    elif c.department == "车端软件":
        sim_llm_scores[c.id] = 0.40  # 部分相关
    elif "调度USP" in c.responsibility_modules and any(
        m in c.responsibility_modules.get("调度USP", []) for m in ["车端", "后端"]
    ):
        sim_llm_scores[c.id] = 0.30  # 调度车端有点关系
    else:
        sim_llm_scores[c.id] = 0.10  # 不相关

    # 语义模拟
    sim_sem_scores[c.id] = round(sim_llm_scores[c.id] * 0.7, 2)

print(f"  L1 LLM 得分 Top5:")
for eid, score in sorted(sim_llm_scores.items(), key=lambda x: x[1], reverse=True)[:5]:
    eng = next(e for e in candidates if e.id == eid)
    print(f"    {eng.name}({eng.department} L{eng.job_level}): {score:.2f}")

# ── Step 2: 精排 + 职级折扣 ──
print(f"\n{'─' * 60}")
print("Step 2: 精排 + 职级折扣")

from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.ranking.ranker import Ranker

rr = RecallResult()
rr.llm_recall = sim_llm_scores
rr.semantic_recall = sim_sem_scores
rr.history_recall = sim_hist_scores

ranker = Ranker()
ranked = ranker.rank(rr, engineers=candidates)

print(f"  最终排名:")
for rank, (eid, d) in enumerate(list(ranked.items())[:5], 1):
    eng = next(e for e in candidates if e.id == eid)
    print(f"    #{rank} {eng.name} {eng.department} L{eng.job_level} "
          f"raw={d['raw_total']:.3f} x{d['level_multiplier']} = {d['total_score']:.3f}")

# ── 验证：Top-1 必须是文永翔 ──
top_id = next(iter(ranked))
top_eng = next(e for e in candidates if e.id == top_id)
top_score = ranked[top_id]["total_score"]

print(f"\n{'─' * 60}")
print("验证")
expected = "文永翔"
if top_eng.name == expected:
    print(f"  ✅ Top-1 是 {top_eng.name} ({top_eng.department} L{top_eng.job_level}) 置信度={top_score:.3f}")
    print(f"  ✅ 验证通过：硬件问题正确分到了机器人事业部的 L2")
else:
    print(f"  🔴 Top-1 是 {top_eng.name} ({top_eng.department} L{top_eng.job_level})，期望 {expected}")
    print(f"  🔴 验证失败！")

# 额外验证：Top-2 不是机器人事业部的（因为没有其他人了）
if len(ranked) > 1:
    top2_id = list(ranked)[1]
    top2_eng = next(e for e in candidates if e.id == top2_id)
    print(f"\n  Top-2: {top2_eng.name} ({top2_eng.department} L{top2_eng.job_level}) "
          f"score={ranked[top2_id]['total_score']:.3f}")
    if top2_eng.department != robot_dept:
        print(f"  ✅ Top-2 是其他部门")
    else:
        print(f"  ⚠️ Top-2 也是机器人事业部")

# ── 对比：如果不过滤的情况 ──
print(f"\n{'─' * 60}")
print("对比：无部门过滤时的情况")

all_ranked = ranker.rank(rr, engineers=profiles)
all_top = next(e for e in profiles if e.id == next(iter(all_ranked)))
all_top_score = all_ranked[all_top.id]["total_score"]
print(f"  全量 Top-1: {all_top.name} ({all_top.department} L{all_top.job_level}) score={all_top_score:.3f}")
print(f"  结论: 部门过滤将候选人从 {len(profiles)} 缩小到 {len(candidates)}，避免了跨部门竞争")

print(f"\n{'=' * 60}")
print("测试完成")
print(f"{'=' * 60}")
