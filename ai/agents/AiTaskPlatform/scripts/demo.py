"""任务 Agent 演示脚本 — 用 Mock 检索数据模拟完整流程

运行方式:
    cd OpenRobotService
    python -m ai.agents.AiTaskPlatform.demo

或:
    python ai/agents/AiTaskPlatform/demo.py
"""

import sys
import json
import asyncio
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "backend" / ".env")

from ai.agents.AiTaskPlatform.pipeline import AiTaskAgent
from ai.agents.AiTaskPlatform.schemas import TaskContext
from ai.agents.AiTaskPlatform.prompts import USER_PROMPT_TEMPLATE, TASK_AGENT_SYSTEM_PROMPT


# ============================================================
# Mock 工单 — 提单 Agent 的诊断结果（故障场景 #44946）
# ============================================================

# 工程师看到这个工单时的上下文：
# 提单 Agent 已经诊断过了，产出了 hypotheses / ruled_out / collected_info
# 任务 Agent 不复诊，直接从这些信息开始往下走

TICKET = TaskContext(
    task_id="44946",
    title="避让后车不动",
    description=(
        "44946避让生成的时候，车已经在这个位置了。"
        "且44946没有路径，导致车不动了。"
        "后续是人车切手动后，才重新规划并完成。"
    ),
    task_type="problem",
    priority="高",
    status="in_progress",
    source="ai_agent",
    problem_summary="避让后车不动，路径起点=终点",
    hypotheses=[
        "路径规划死锁",
        "MAPF算法异常",
        "避让场景路径生成bug",
    ],
    ruled_out=[
        "网络通信异常",
        "MQTT连接断开",
        "车辆硬件故障",
    ],
    collected_info={
        "robot_type": "潜伏车",
        "error_time": "2026-07-05 14:40",
    },
    robot_type="潜伏车",
    diagnosis_rounds=2,
)

# ============================================================
# Mock 检索结果 — 模拟 Qdrant 排查树 + 历史工单方案
# ============================================================
# 这些数据在 Qdrant 就绪后由 analyzer.py 自动获取
# 目前先用 mock 来验证 Agent 核心逻辑

MOCK_TROUBLESHOOTING = """\
排查树 1：车不动，任务状态显示路径规划中
【结论】原因：MAPF 避让算法在特定场景下生成的路径起点=终点，形成死循环路径，车辆无法执行。
方案：回退 MAPF 算法版本至 v1.1.1，或升级至 v1.2.0（已修复此bug）。
同时检查路径规划参数中的避让距离阈值，建议设为 >=2.0m。
---
排查树 2：车不动，路径已下发但车未执行
【结论】原因：可能为路径包含不可达节点，或车辆定位漂移导致路径校验失败。
方案：检查定位置信度，必要时重新标定反光板；确认路径上的所有节点均可达。"""

MOCK_HISTORY = """\
历史工单 1：工单 #44123（相似度 0.89）
标题：AGV避让后死锁不动  |  解决时间：2026-07-01
根因：MAPF v1.1.2 的避让逻辑在双车同时避让时，会为被避让方生成起点=终点的占位路径，该路径实际不可执行。
方案：回退版本至 v1.1.1 后问题消失。已提 issue 给算法组。
---
历史工单 2：工单 #43892（相似度 0.72）
标题：叉车避让后路径循环  |  解决时间：2026-06-27
根因：路径规划参数 misconfiguration，避让重新规划时使用了错误的起点坐标。
方案：在路径规划配置中设置 avoidance_start_from_current_pos=true。"""


# ============================================================
# 核心流程：构建 Prompt → LLM 生成 → 解析输出
# ============================================================

async def main():
    agent = AiTaskAgent()
    await agent._ensure_clients()

    print("=" * 70)
    print(f"  工单 #{TICKET.task_id} | {TICKET.title} | 优先级: {TICKET.priority}")
    print("=" * 70)
    print()
    print("--- 提单 Agent 诊断结果 ---")
    print(f"  问题概述: {TICKET.problem_summary}")
    print(f"  推测原因: {' / '.join(TICKET.hypotheses)}")
    print(f"  已排除:   {' / '.join(TICKET.ruled_out)}")
    print(f"  已收集:   {json.dumps(TICKET.collected_info, ensure_ascii=False)}")
    print(f"  诊断轮数: {TICKET.diagnosis_rounds}")
    print()
    print("--- 知识库检索 (Mock) ---")
    print("  排查树: 2 个结论节点命中")
    print("  历史工单: 2 个相似案例 (0.89 / 0.72)")
    print("  附件: 无")
    print()
    print("--- LLM 分析中 ... ---")
    print()

    # 构建 Prompt
    prompt = USER_PROMPT_TEMPLATE.format(
        title=TICKET.title,
        description=TICKET.description,
        task_type=TICKET.task_type,
        priority=TICKET.priority,
        source=TICKET.source,
        problem_summary=TICKET.problem_summary,
        hypotheses="、".join(TICKET.hypotheses),
        ruled_out="、".join(TICKET.ruled_out),
        collected_info=json.dumps(TICKET.collected_info, ensure_ascii=False),
        rounds=TICKET.diagnosis_rounds,
        fault_info=f"车型: {TICKET.robot_type} | 故障码: 无",
        troubleshooting_conclusions=MOCK_TROUBLESHOOTING,
        historical_solutions=MOCK_HISTORY,
        attachment_analysis="（无附件）",
    )

    # LLM 调用
    import time
    t0 = time.perf_counter()
    raw = await agent._llm_client.complete(
        prompt=prompt,
        system_prompt=TASK_AGENT_SYSTEM_PROMPT,
        max_tokens=1500,
        temperature=0.3,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    # 解析输出
    draft = agent._parse_solution(raw)

    # 打印结果
    print("=" * 70)
    print("  AI 解决方案草稿 (LLM 耗时:", elapsed_ms, "ms)")
    print("=" * 70)
    print()
    print("【根因分析】")
    print(draft.root_cause_analysis)
    print()
    print(f"【建议步骤】({len(draft.suggested_actions)} 步)")
    for i, action in enumerate(draft.suggested_actions, 1):
        print(f"  {i}. {action}")
    print()
    print("【参考来源】")
    if draft.references:
        for ref in draft.references:
            print(f"  - {ref}")
    else:
        print("  (无)")
    print()
    print(f"【置信度】{draft.confidence:.2f}")
    print(f"【需要更多信息】{draft.needs_more_info}")
    print()
    print("=" * 70)
    print("  工程师可以编辑上述草稿，修改后提交完成工单")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
