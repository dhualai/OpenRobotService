"""任务 Agent 命令行交互工具

运行：python ai/agents/AiTaskPlatform/cli_chat.py

模拟前端 ChatPanel + TasksView 的完整交互流程：
  - 启动：看到工单列表（模拟 GET /api/tasks/ → tickets 表）
  - 自由问答：无 taskId → chat()  (前端 ChatPanel scene=tasks 无 taskId)
  - 选工单：输入编号 → 展示诊断信息 (前端点击卡片 → taskId 绑定)
  - 工单分析：输入消息 → chat(taskId=...) (前端 ChatPanel 有 taskId)
  - 完整分析：/analyze → analyze() → SolutionDraft (前端 SSE analyze/stream)
  - 提交方案：/submit → submit() (前端 SolutionCard 点提交)

命令：
  /trace    切换埋点节点显示（每个回复后显示 trace 树）
  /analyze  完整三路分析 → 输出方案草稿
  /submit   提交当前编辑的解决方案
  /list     重新显示工单列表
  /chat     退出工单分析模式，回到自由问答
  /help     显示此帮助
  /quit     退出
"""

import sys
import json
import asyncio
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "backend" / ".env")

from ai.agents.AiTaskPlatform.pipeline import AiTaskAgent
from ai.agents.AiTaskPlatform.schemas import TaskContext, TaskAnalyzeRequest
from ai.agents.AiTaskPlatform.prompts import DIAGNOSE_USER_TEMPLATE, DIAGNOSE_SYSTEM_PROMPT

# ============================================================
# Mock 工单数据 — 模拟分配给当前工程师（你）的工单
# ============================================================
MY_TICKETS = [
    {
        "id": "44946", "title": "避让后车不动",
        "description": "44946避让生成的时候，车已经在这个位置了。且44946没有路径，导致车不动了。后续是人车切手动后，才重新规划并完成。",
        "priority": "高", "status": "in_progress", "type": "problem", "robot_type": "潜伏车",
        "fault_code": "",
        "diagnosis": {
            "problem_summary": "避让后车不动，路径起点=终点",
            "hypotheses": ["路径规划死锁", "MAPF算法异常", "避让场景路径生成bug"],
            "ruled_out": ["网络通信异常", "MQTT连接断开", "车辆硬件故障"],
            "collected_info": {"robot_type": "潜伏车", "error_time": "2026-07-05 14:40"},
            "rounds": 2,
        },
    },
    {
        "id": "44958", "title": "地图编辑后加载不完整",
        "description": "用户反馈编辑完地图后保存，重新打开只显示部分背景图，SLAM定位点缺失",
        "priority": "中", "status": "in_progress", "type": "problem", "robot_type": "叉车",
        "diagnosis": {
            "problem_summary": "地图保存后重新加载，背景图只显示部分，SLAM点缺失",
            "hypotheses": ["地图存储路径异常", "浏览器缓存导致部分加载"],
            "ruled_out": ["网络中断"],
            "collected_info": {"robot_type": "叉车"}, "rounds": 1,
        },
    },
    {
        "id": "44972", "title": "充电桩通信超时",
        "description": "车辆到达充电桩后，请求充电时通信超时，无法自动充电，需要人工重启充电桩",
        "priority": "低", "status": "pending", "type": "support", "robot_type": "潜伏车",
        "fault_code": "E_CHARGE_TIMEOUT",
        "diagnosis": {
            "problem_summary": "充电桩通信超时，自动充电失败",
            "hypotheses": ["充电桩MQTT消息丢失", "心跳超时"],
            "ruled_out": ["车辆电池故障"],
            "collected_info": {"robot_type": "潜伏车"}, "rounds": 1,
        },
    },
]

HELP = """
  ┌──────────────────────────────────────────────────────┐
  │  输入工单编号  → 进入分析模式（模拟前端点击卡片）    │
  │  /analyze      → 完整三路分析 → 方案草稿             │
  │  /submit       → 提交当前解决方案                    │
  │  /list         → 重新显示工单列表                    │
  │  /chat         → 退出分析模式，回到自由问答          │
  │  /trace        → 切换埋点追踪（显示节点耗时）        │
  │  /help         → 显示此帮助                          │
  │  /quit         → 退出                                │
  │  直接输入       → 当前模式下：自由问答 / 工单相关提问  │
  └──────────────────────────────────────────────────────┘"""


def show_tickets():
    print()
    print("-" * 60)
    print("  [系统任务] — 分配给我的工单")
    print("-" * 60)
    for t in MY_TICKETS:
        status_label = {"in_progress": "进行中", "pending": "待处理", "resolved": "已解决"}.get(t["status"], t["status"])
        print(f"  #{t['id']:6s} [{status_label}] [{t['priority']}] {t['title']}")
        print(f"           {t['description'][:70]}...")
    print("-" * 60)


def show_trace(trace: list, total_ms: int = 0):
    """渲染埋点追踪树"""
    if not trace:
        print("  (无追踪数据)")
        return
    print()
    print("  ┌─ 请求追踪 ───────────────────────────────────")
    status_icon = {"ok": "[OK]", "error": "[FAIL]", "skipped": "[-]"}
    for t in trace:
        icon = status_icon.get(t["status"], "[?]")
        node = t["node"]
        ms = t.get("elapsed_ms", 0)
        inp = t.get("input", {})
        out = t.get("output", {})
        detail = ""
        if "token_count" in out:
            detail = f'tokens={out["token_count"]}'
        elif "response_chars" in out:
            detail = f'chars={out["response_chars"]}'
        elif "confidence" in out:
            detail = f'confidence={out["confidence"]:.0%}'
        elif "has_title" in out:
            detail = f'loaded={"yes" if out["has_title"] else "no"}'
        print(f"  │ {icon:6s} {node:20s} {ms:5.0f}ms  {detail}")
    if total_ms:
        print(f"  │         {'total':20s} {total_ms:5.0f}ms")
    print("  └──────────────────────────────────────────────")


async def main():
    agent = AiTaskAgent()
    await agent._ensure_clients()
    import time
    session_id = f"cli_{int(time.time())}"
    current_task = None  # 模拟前端 taskId
    show_trace_enabled = True  # 默认显示 trace

    print()
    print("=" * 60)
    print("  任务 Agent — 系统任务（供给视角）")
    print("  你是：接单工程师  |  session:", session_id[:12], "...")
    print("=" * 60)
    show_tickets()
    print("  /trace 查看节点  |  /help 帮助  |  直接输入开始")
    print()

    while True:
        # 提示符
        if current_task:
            prompt_text = f"  [#{current_task['id']}] > "
        else:
            prompt_text = "  > "
        try:
            user_input = input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  再见！")
            break

        if not user_input:
            continue

        # ── 命令 ──
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()

            if cmd == "/quit":
                print("  再见！")
                break
            elif cmd == "/help":
                print(HELP)
            elif cmd == "/list":
                show_tickets()
            elif cmd == "/trace":
                show_trace_enabled = not show_trace_enabled
                print(f"  埋点追踪: {'ON' if show_trace_enabled else 'OFF'}")
            elif cmd == "/chat":
                if current_task:
                    print(f"  已退出工单 #{current_task['id']}，回到自由问答")
                    current_task = None
                else:
                    print("  当前已在自由问答模式")
            elif cmd == "/submit" and current_task:
                print("  提交中...", end="\r")
                try:
                    from ai.agents.AiTaskPlatform.schemas import SolutionDraft
                    draft = SolutionDraft(
                        root_cause_analysis="工程师手动确认的根因",
                        suggested_actions=["已完成的步骤"],
                    )
                    result = await agent.submit(
                        task_id=current_task["id"],
                        session_id=session_id,
                        draft=draft,
                    )
                    print(" " * 10, end="\r")
                    print(f"  提交结果: ticket_updated={result['data'].get('ticket_updated')}")
                    if show_trace_enabled:
                        show_trace(result["data"].get("_trace", []), result["data"].get("_total_ms", 0))
                except Exception as e:
                    print(f"  提交失败: {e}")
            else:
                print(f"  未知命令: {cmd}")
            continue

        # ── 工单编号匹配（模拟前端点击卡片）──
        ticket = next((t for t in MY_TICKETS if t["id"] == user_input.split()[0]), None)
        if ticket:
            current_task = ticket
            diag = ticket.get("diagnosis", {})
            print()
            print(f"  ┌─ 工单 #{ticket['id']} ──────────────────────────────")
            print(f"  │ 标题: {ticket['title']}")
            print(f"  │ 状态: {getattr(ticket, 'status', ticket.get('status',''))} | 优先级: {ticket['priority']} | 车型: {ticket['robot_type']}")
            print(f"  │ 描述: {ticket['description']}")
            if diag:
                print(f"  │")
                print(f"  │ 提单 Agent 诊断:")
                print(f"  │   推测: {' / '.join(diag.get('hypotheses', []))}")
                print(f"  │   排除: {' / '.join(diag.get('ruled_out', []))}")
                print(f"  │   收集: {json.dumps(diag.get('collected_info', {}), ensure_ascii=False)}")
            print(f"  └──────────────────────────────────────────")
            print(f"  可直接提问，或 /analyze 做完整分析")
            print()
            continue

        # ── /analyze — 完整三路分析 → SolutionDraft ──
        if user_input.lower() == "/analyze" and current_task:
            print("  分析中（三路检索 + LLM）...", flush=True)
            diag = current_task.get("diagnosis", {})
            ctx = TaskContext(
                task_id=current_task["id"], title=current_task["title"],
                description=current_task["description"],
                task_type=current_task.get("type", "problem"),
                priority=current_task["priority"], status=current_task["status"],
                source="ai_agent",
                problem_summary=diag.get("problem_summary", ""),
                hypotheses=diag.get("hypotheses", []) or [],
                ruled_out=diag.get("ruled_out", []) or [],
                collected_info=diag.get("collected_info", {}) or {},
                robot_type=current_task.get("robot_type", ""),
                fault_code=current_task.get("fault_code", ""),
                diagnosis_rounds=diag.get("rounds", 0),
            )
            import time as _time
            t0 = _time.perf_counter()
            raw = await agent._llm_client.complete(
                prompt=agent._build_prompt(ctx, {
                    "troubleshooting": "（Qdrant 未就绪，跳过）",
                    "history": "（task_resolutions 未就绪，跳过）",
                    "attachment_analysis": {},
                }),
                system_prompt=TASK_AGENT_SYSTEM_PROMPT, max_tokens=1500, temperature=0.3,
            )
            draft, parse_status = agent._parse_solution_with_status(raw)
            ms = round((_time.perf_counter() - t0) * 1000)
            trace = agent._pop_trace()
            print(f"  LLM: {ms}ms")
            print()
            print(f"  ┌─ AI 解决方案草稿 ─────────────────────────")
            print(f"  │ 【根因分析】")
            for line in draft.root_cause_analysis.split("\n"):
                print(f"  │ {line}")
            print(f"  │")
            print(f"  │ 【建议步骤】({len(draft.suggested_actions)} 步)")
            for i, a in enumerate(draft.suggested_actions, 1):
                print(f"  │   {i}. {a}")
            if draft.references:
                print(f"  │")
                print(f"  │ 【参考来源】")
                for r in draft.references:
                    print(f"  │   - {r}")
            print(f"  │")
            print(f"  │ 【置信度】{draft.confidence:.0%}  |  解析: {parse_status}")
            print(f"  └──────────────────────────────────────────")
            if show_trace_enabled:
                show_trace(trace, ms)
            print()
            continue

        # ── 发送消息 ──
        print("  ...", end="\r")
        if current_task:
            diag = current_task.get("diagnosis", {})
            task_ctx = (
                f"标题: {current_task['title']}\n"
                f"描述: {current_task['description']}\n"
                f"推测原因: {' / '.join(diag.get('hypotheses', []))}\n"
                f"已排除: {' / '.join(diag.get('ruled_out', []))}"
            )
            response = await agent.chat(
                session_id=session_id, query=user_input,
                task_id=current_task["id"],
                task_title=current_task["title"],
                task_description=task_ctx,
            )
        else:
            response = await agent.chat(session_id=session_id, query=user_input)
        trace = agent._pop_trace()
        print(" " * 10, end="\r")
        print()
        print(response)
        if show_trace_enabled:
            total = sum(t.get("elapsed_ms", 0) for t in trace)
            show_trace(trace, total)
        print()


if __name__ == "__main__":
    asyncio.run(main())
