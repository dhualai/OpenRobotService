"""
提单流程 live 测试 —— 真实 LLM，覆盖各种提单状况。

两种模式：
  默认 HTTP 模式：起 run.py 后，打 /api/ai/qa/ask/stream 接口（SSE 流式，端到端：router+真实Redis/MySQL）
  --direct 模式 ：不开服务，直接调 platform.run_stream()（内存记忆、模拟提单不写库、状态可见性最高）

用法:
    # 1. 先起服务
    python ai/run.py
    # 2. 另开终端跑测试（HTTP 模式，默认 localhost:8401）
    python -m ai.tools.live_ticket_test
    python -m ai.tools.live_ticket_test --group C
    python -m ai.tools.live_ticket_test --only A1,C1,G3
    python -m ai.tools.live_ticket_test --base-url http://localhost:8401 --retrieve

    # 直调模式（不开服务，深调试，能看 ticket_collecting/collect_rounds/last_ticket）
    python -m ai.tools.live_ticket_test --direct
"""
import asyncio
import argparse
import sys
import json
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from ai.core.logging import get_logger
logger = get_logger("live-test")

SEP = "-" * 72


# ============================================================
# 场景定义（HTTP / direct 通用）
# ============================================================

SCENARIOS = [
    # -- A. 信息齐全 -> 直接提单 --
    {"id": "A1", "group": "A", "name": "一次性给全信息+转工单->直接提单",
     "turns": ["华大制造基地的X1152机器人报404错误，昨天下午开始每次启动都出现，转工单"],
     "expect": "第1轮直接 submit，project=华大制造基地，无需追问"},
    {"id": "A2", "group": "A", "name": "先描述故障，再说转工单",
     "turns": ["我们的搬运机器人在3号产线老是离线", "华大基地，车型XP1152，今天早上开始，偶发", "转工单"],
     "expect": "前两轮诊断/收集，第3轮转工单->信息齐->submit"},

    # -- B. 缺字段 -> 收集 --
    {"id": "B1", "group": "B", "name": "缺project->追问->补齐->提单",
     "turns": ["机器人报错了，转工单", "华大制造基地"],
     "expect": "第1轮缺project->ask追问；第2轮给project后继续收集或提单"},
    {"id": "B2", "group": "B", "name": "多字段逐个收集",
     "turns": ["转工单", "华大制造基地", "X1152", "昨天下午", "每次都有"],
     "expect": "逐轮收集 project/车型/时间/频率，集齐后自动提单"},

    # -- C. 闭环保护（提单后）--
    {"id": "C1", "group": "C", "name": "提单后裸'转工单'->拦截",
     "turns": ["华大基地X1152报404昨天开始每次都有，转工单", "转工单"],
     "expect": "第1轮提单成功；第2轮裸转单->闭环拦截'请先描述新现象'"},
    {"id": "C2", "group": "C", "name": "提单后新问题+转单->第二张工单",
     "turns": ["华大基地X1152报404昨天开始每次都有，转工单",
              "另外一台X2200也报404了，华大基地，转工单"],
     "expect": "第1轮提单；第2轮描述新问题->放行，第二张工单"},
    {"id": "C3", "group": "C", "name": "提单后问进度->answer不提单",
     "turns": ["华大基地X1152报404昨天开始每次都有，转工单", "这个工单什么时候能处理好"],
     "expect": "第2轮正常 answer，不触发提单"},

    # -- D. 意图识别 --
    {"id": "D1", "group": "D", "name": "否定意图：'我不转工单就问问'",
     "turns": ["我不转工单，就问问这个404错误什么意思"],
     "expect": "LLM 识别为否定->answer/ask 正常解答，不提单"},
    {"id": "D2", "group": "D", "name": "口语化提单：'给我下个单'",
     "turns": ["机器人离线好几次了，华大基地X1152，给我下个单"],
     "expect": "LLM 识别为提单意图->submit（信息齐）"},
    {"id": "D3", "group": "D", "name": "纯闲聊",
     "turns": ["你好啊", "谢谢"],
     "expect": "chat/answer，绝不提单"},

    # -- E. 工单类型多样 --
    {"id": "E1", "group": "E", "name": "bug类：版本+复现步骤",
     "turns": ["系统v3.2版本，一点导出按钮就崩溃，复现步骤是打开报表点导出，转工单"],
     "expect": "ticket_type=bug，project 需收集->ask 或给齐则 submit"},
    {"id": "E2", "group": "E", "name": "feature类：功能需求",
     "turns": ["希望库位配置能加个航向角字段让车自动调整，转工单"],
     "expect": "ticket_type=feature，需补 project"},
    {"id": "E3", "group": "E", "name": "非车辆故障：自定义字段",
     "turns": ["企微集成之后消息发不出去，一发送就超时，转工单"],
     "expect": "LLM 设自定义 required_fields（如错误现象/复现），project 需补"},

    # -- F. 鬼打墙防护 --
    {"id": "F1", "group": "F", "name": "收集超限->强制提单",
     "turns": ["转工单", "华大基地", "不知道", "不清楚", "没有", "真没有"],
     "expect": "给 project 后其他字段答不上来，collect_rounds>=4->强制提单"},
    {"id": "F2", "group": "F", "name": "诊断多轮无果->建议转单（软提示）",
     "turns": ["机器人不动了", "还是不行", "还是不行", "还是不行", "还是不行", "还是不行", "还是不行"],
     "expect": "diagnosis_rounds>=6 时 prompt 提示 LLM 收尾/建议转单"},

    # -- G. 按钮路径（对话驱动到目标状态后点按钮）--
    {"id": "G1", "group": "G", "name": "按钮prepare：有project->draft_ready",
     "turns": ["我在华大制造基地，机器人有点问题想看看", "__PREPARE__"],
     "expect": "turn1 给了 project 未提单；prepare->stage=draft_ready"},
    {"id": "G2", "group": "G", "name": "按钮prepare：缺project->not_ready",
     "turns": ["机器人报错了", "__PREPARE__"],
     "expect": "prepare->stage=not_ready，missing 含项目名称"},
    {"id": "G3", "group": "G", "name": "按钮prepare：提单后->闭环拦截",
     "turns": ["华大基地X1152报404昨天开始每次都有，转工单", "__PREPARE__"],
     "expect": "turn1 提单；prepare->闭环拦截 code=1"},

    # -- H. 边界 & 按钮确认 & 混合 --
    {"id": "H1", "group": "H", "name": "未知项目->平台项目兜底",
     "turns": ["某某未知科技公司厂区的X1152报404，今天开始每次都有，转工单"],
     "expect": "submit 成功，project 匹配不上走'平台项目'兜底"},
    {"id": "H2", "group": "H", "name": "bug类信息齐+project->直接提单",
     "turns": ["安吉北区这边系统v3.2一点导出就崩溃，复现是打开报表点导出，转工单"],
     "expect": "ticket_type=bug + project 有 -> submit"},
    {"id": "H3", "group": "H", "name": "同句否定：'先别转工单'",
     "turns": ["先别转工单，我还想再排查一下这个404"],
     "expect": "不提单（_user_wants_ticket 否定前置过滤）"},
    {"id": "H4", "group": "H", "name": "按钮完整流：prepare->confirm 成功",
     "turns": ["华大基地X1152报404想转单", "__PREPARE__", "__CONFIRM__"],
     "expect": "prepare draft_ready -> confirm code=0 提单成功"},
    {"id": "H5", "group": "H", "name": "按钮confirm无草稿->报错",
     "turns": ["__CONFIRM__"],
     "expect": "confirm code=1 '没有待确认的工单草稿'"},
    {"id": "H6", "group": "H", "name": "混合：对话提单后按钮被拦",
     "turns": ["华大基地X1152报404昨天开始每次都有，转工单", "__PREPARE__"],
     "expect": "turn1 对话提单；prepare->闭环拦截"},
    {"id": "H7", "group": "H", "name": "多轮收集中用户改口补充project",
     "turns": ["转工单", "X1152报404昨天每次都有", "哦项目是安吉北区"],
     "expect": "前两轮缺project被拦，第3轮给project后提单"},

    # -- I. 主流场景：先排查诊断 -> 用户决定转单（此时信息不全）-> 工单填写模式补信息 -> 提单 --
    {"id": "I1", "group": "I", "name": "排查后转单，project在问题描述里，再补车型/时间",
     "turns": ["安吉北区的车不动了", "重启过了没用，转工单吧", "X1152", "昨天下午开始每次都有"],
     "expect": "turn1 排查(turn1含project=安吉北区) -> turn2 转单但缺车型/时间 -> 工单填写模式追问 -> turn3-4 补齐 -> submit"},
    {"id": "I2", "group": "I", "name": "排查后转单，完全没说project->先补project",
     "turns": ["我们的车不动了", "检查过了没问题，转工单吧", "安吉北区", "X1152", "今天早上开始偶尔"],
     "expect": "turn1-2 排查(无project) -> turn2 转单 -> 追问project -> turn3-5 补齐 -> submit"},
    {"id": "I3", "group": "I", "name": "多轮排查后才转单",
     "turns": ["X1152在安吉北区不动了", "重启没用", "网络也正常", "那就转工单吧"],
     "expect": "turn1-3 排查 -> turn4 转单(已有project+车型) -> 追问时间/频率或直接submit"},
]

# 应当最终提单的场景（用于多轮稳定性自动判定）
# 注：I 系列（多轮主流流程）flash 模型下偶发单轮不提交，由人工判断，不参与自动判定
SHOULD_SUBMIT = {"A1", "A2", "B2", "C2", "D2", "H1", "H2", "H7", "F1"}
# 应当【不】提单的场景
SHOULD_NOT_SUBMIT = {"C3", "D1", "D3", "H3", "H5"}


# ============================================================
# HTTP 模式
# ============================================================

async def http_converse(client, base_url, sid, query, skip_retrieval):
    """打 /api/ai/qa/ask/stream（SSE），收集 result + token 流 + status 阶段。

    用户真正看到的是 token 流（含 ⚠️ 追加修正），result.message 只是服务端最终状态，
    所以按 data 形状分发，把 token 拼成 `_user_seen` 作为"用户看到的完整消息"。
    """
    result, stages, err, tokens = None, [], None, []
    async with client.stream("POST", f"{base_url}/api/ai/qa/ask/stream",
                             json={"session_id": sid, "query": query, "skip_retrieval": skip_retrieval},
                             timeout=120.0) as resp:
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except Exception:
                continue
            if "token" in data:
                tokens.append(data["token"])
            elif "stage" in data:
                stages.append(data.get("stage"))
            elif "error" in data:
                err = data.get("error", str(data))
            elif "action" in data or "ticket" in data:
                result = data
    if err and not result:
        return {"action": "error", "message": err, "_user_seen": "".join(tokens)}, stages
    if result is None:
        result = {"action": "?", "message": "(无 result 事件)"}
    result["_user_seen"] = "".join(tokens)
    return result, stages


async def http_prepare(client, base_url, sid):
    resp = await client.post(f"{base_url}/api/ai/qa/ticket/prepare",
                             json={"session_id": sid}, timeout=60.0)
    return resp.json()


async def http_confirm(client, base_url, sid, overrides=None):
    resp = await client.post(f"{base_url}/api/ai/qa/ticket/confirm",
                             json={"session_id": sid, "overrides": overrides or {}},
                             timeout=60.0)
    return resp.json()


def print_http_confirm(idx, r):
    data = r.get("data") or {}
    print(f"  [{idx}] [按钮] confirm_submit")
    print(f"      -> code={r.get('code', 0)} "
          f"{'OK已提单' + str(data.get('ticket', {}).get('ticket_id', '')) if r.get('code')==0 else '未提单'}")
    print(f"        message: {str(r.get('message', ''))[:90]}")


def print_http_turn(idx, query, r, stages=None):
    action = r.get("action", "?")
    msg = r.get("message", "")
    asu = r.get("agent_state") or {}
    submitted = "ticket" in r and r.get("ticket")
    ticket_id = ""
    if submitted:
        t = r["ticket"].get("data", {}).get("ticket", {})
        ticket_id = t.get("ticket_id", "")
    print(f"  [{idx}] 用户: {query}")
    print(f"      -> action={action}  phase={asu.get('phase', '?')}  "
          f"{'OK已提单(' + ticket_id + ')' if submitted else '未提单'}")
    if stages:
        print(f"        stages: {' -> '.join(stages)}")
    print(f"        collected_fields={asu.get('collected_fields', [])}  "
          f"ticket_type={asu.get('ticket_type', '?')}")
    seen = r.get("_user_seen") or msg
    print(f"        用户看到: {str(seen)[:160].replace(chr(10), '⏎')}")
    if r.get("_user_seen") and r.get("_user_seen") != msg:
        print(f"        服务端result.message: {str(msg)[:90]}")


def print_http_prepare(idx, r):
    data = r.get("data") or r
    print(f"  [{idx}] [按钮] prepare_ticket")
    print(f"      -> code={r.get('code', 0)} stage={data.get('stage', '-')} "
          f"missing={data.get('missing_info') or data.get('missing_fields', [])}")
    print(f"        message: {str(data.get('message', ''))[:90]}")


# ============================================================
# direct 模式（不开服务，深调试）
# ============================================================

async def setup_direct_platform():
    import httpx
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        AiDiagnosisPlatform, DiagnosisRequest, AgentState,
        _load_agent_state, _save_agent_state, _reset_state_after_submit, _can_submit,
    )
    from ai.core.memory import SessionMemory

    class _Mem:
        def __init__(self):
            self._s = {}; self.max_turns = 20
        def _g(self, sid):
            if sid not in self._s:
                self._s[sid] = SessionMemory(session_id=sid, turns=[], metadata={})
            return self._s[sid]
        async def get_memory(self, sid): return self._g(sid)
        async def add_turn(self, sid, role, c):
            m = self._g(sid); m.turns.append({"role": role, "content": c}); return m
        async def save_memory(self, m): self._s[m.session_id] = m
        async def resolve_pronoun(self, q, sid): return (q, {})
        async def add_pending_ticket(self, sid): pass
        async def remove_pending_ticket(self, sid): pass

    p = AiDiagnosisPlatform()
    p._memory_manager = _Mem()
    await p._ensure_clients()

    async def _mock_submit(session_id, created_by=""):
        m = await p._memory_manager.get_memory(session_id)
        st = _load_agent_state(m.metadata) or AgentState(session_id=session_id)
        can, reason = _can_submit(st)
        if not can: raise ValueError(reason)
        ticket = await p._build_ticket(session_id, st, m)
        st.ticket_seq += 1
        ticket["ticket_seq"] = st.ticket_seq
        _reset_state_after_submit(st, m, ticket, db_id=0)
        await p._memory_manager.save_memory(m)
        print(f"      [模拟提单·未写库] title={ticket.get('title')} | "
              f"type={ticket.get('type')} | project={ticket.get('project')}")
        return {"type": "ticket", "data": {"ticket": ticket, "db_id": 0, "notice": "（模拟）"}}
    p.submit = _mock_submit
    return p


async def direct_converse(platform, sid, query):
    from ai.agents.AiDiagnosisPlatform.pipeline import DiagnosisRequest, _load_agent_state
    req = DiagnosisRequest(session_id=sid, query=query, skip_retrieval=True, created_by="live-test")
    result = None
    async for event in platform.run_stream(req):
        if event.get("event") == "result":
            result = event["data"]
    m = await platform._memory_manager.get_memory(sid)
    state = _load_agent_state(m.metadata)
    return result, state


def print_direct_turn(idx, query, result, state):
    action = (result or {}).get("action", "?")
    msg = (result or {}).get("message", "")
    submitted = "ticket" in (result or {})
    ticket_id = result["ticket"]["data"]["ticket"].get("ticket_id", "") if submitted else ""
    print(f"  [{idx}] 用户: {query}")
    print(f"      -> action={action}  phase={state.phase}  "
          f"{'OK已提单(' + ticket_id + ')' if submitted else '未提单'}")
    print(f"        project={state.collected_info.get('project', '(空)')!r}  "
          f"collect_rounds={state.collect_rounds}  "
          f"ticket_collecting={state.ticket_collecting}")
    if state.last_submitted_ticket:
        print(f"        last_ticket={state.last_submitted_ticket.get('ticket_id', '?')}")
    print(f"        消息: {msg[:90]}")


# ============================================================
# 场景执行
# ============================================================

async def run_scenario_http(client, base_url, skip_retrieval, sc, quiet=False):
    sid = f"live-{sc['id']}-{uuid4().hex[:6]}"
    if not quiet:
        print(f"\n{SEP}\n场景 {sc['id']} [{sc['group']}]  {sc['name']}\n期望: {sc['expect']}\n{SEP}")
    ticket_count = 0
    last_action = "?"
    errored = False
    for i, q in enumerate(sc["turns"], 1):
        try:
            if q == "__PREPARE__":
                r = await http_prepare(client, base_url, sid)
                if not quiet:
                    print_http_prepare(i, r)
            elif q == "__CONFIRM__":
                r = await http_confirm(client, base_url, sid)
                if r.get("code") == 0:
                    ticket_count += 1
                if not quiet:
                    print_http_confirm(i, r)
            else:
                r, stages = await http_converse(client, base_url, sid, q, skip_retrieval)
                if r.get("ticket"):
                    ticket_count += 1
                last_action = r.get("action", "?")
                if not quiet:
                    print_http_turn(i, q, r, stages)
        except Exception as e:
            errored = True
            if not quiet:
                print(f"  [{i}] 用户: {q}\n      [ERR] {e}")
    return {"id": sc["id"], "tickets": ticket_count, "last_action": last_action, "errored": errored}


async def run_scenario_direct(platform, sc):
    sid = f"live-{sc['id']}-{uuid4().hex[:6]}"
    print(f"\n{SEP}\n场景 {sc['id']} [{sc['group']}]  {sc['name']}\n期望: {sc['expect']}\n{SEP}")
    for i, q in enumerate(sc["turns"], 1):
        try:
            if q == "__PREPARE__":
                r = await platform.prepare_ticket(sid)
                data = r if "stage" in r else r.get("data", r)
                print(f"  [{i}] [按钮] prepare_ticket")
                print(f"      -> code={r.get('code', 0)} stage={data.get('stage', '-')} "
                      f"missing={data.get('missing_info') or data.get('missing_fields', [])}")
            else:
                result, state = await direct_converse(platform, sid, q)
                print_direct_turn(i, q, result, state)
        except Exception as e:
            print(f"  [{i}] 用户: {q}\n      [ERR] {e}")


# ============================================================
# main
# ============================================================

async def main_async(args):
    if args.list:
        for sc in SCENARIOS:
            print(f"  {sc['id']:3} [{sc['group']}] {sc['name']}")
        return

    selected = SCENARIOS
    if args.only:
        want = {x.strip().upper() for x in args.only.split(",")}
        selected = [s for s in SCENARIOS if s["id"] in want]
    elif args.group:
        selected = [s for s in SCENARIOS if s["group"] == args.group.upper()]
    if not selected:
        print("没有匹配的场景"); return

    skip_retrieval = not args.retrieve

    if args.direct:
        print(f"[direct 模式] 不开服务，内存记忆，提单模拟（不写库）。共 {len(selected)} 个场景。")
        platform = await setup_direct_platform()
        for sc in selected:
            await run_scenario_direct(platform, sc)
    else:
        print(f"[HTTP 模式] base_url={args.base_url}  skip_retrieval={skip_retrieval}。"
              f" 确保已 python ai/run.py。共 {len(selected)} 个场景。")
        import httpx
        async with httpx.AsyncClient(trust_env=False) as client:
            # 健康检查
            try:
                hc = await client.get(f"{args.base_url}/api/ai/qa/health", timeout=20.0)
                print(f"  服务健康检查: {hc.status_code}")
            except Exception as e:
                print(f"  [!] 连不上 {args.base_url}，请先起服务 python ai/run.py\n      {e}")
                return
            if args.rounds and args.rounds > 1:
                await run_rounds(client, args.base_url, skip_retrieval, selected, args.rounds)
            else:
                for sc in selected:
                    await run_scenario_http(client, args.base_url, skip_retrieval, sc)

    print(f"\n{SEP}\n完成。逐场景对照「期望」人工核对 LLM 行为。")


async def run_rounds(client, base_url, skip_retrieval, selected, rounds):
    """多轮稳定性：把 selected 跑 rounds 遍，按场景汇总提单/拦截结果。"""
    from collections import defaultdict
    print(f"\n{SEP}\n[稳定性测试] {len(selected)} 场景 × {rounds} 轮\n{SEP}")
    # tally[id] = {"submit":n, "no_submit":n, "error":n}
    tally = defaultdict(lambda: {"submit": 0, "no_submit": 0, "error": 0})
    for rnd in range(1, rounds + 1):
        print(f"\n===== 第 {rnd}/{rounds} 轮 =====")
        for sc in selected:
            res = await run_scenario_http(client, base_url, skip_retrieval, sc, quiet=True)
            if res["errored"]:
                tally[res["id"]]["error"] += 1
                mark = "ERR"
            elif res["tickets"] > 0:
                tally[res["id"]]["submit"] += 1
                mark = f"提单x{res['tickets']}"
            else:
                tally[res["id"]]["no_submit"] += 1
                mark = f"未提单({res['last_action']})"
            # 自动判定（仅对声明了期望的场景）
            verdict = ""
            if res["id"] in SHOULD_SUBMIT:
                verdict = " ✅" if res["tickets"] > 0 else " ❌(应提单未提)"
            elif res["id"] in SHOULD_NOT_SUBMIT:
                verdict = " ✅" if res["tickets"] == 0 else " ❌(不该提单却提了)"
            print(f"  {res['id']:3} {mark:14}{verdict}")
    # 汇总
    print(f"\n{SEP}\n汇总（{rounds} 轮）\n{SEP}")
    print(f"{'场景':4} {'提单':>6} {'未提单':>6} {'错误':>6}  期望  判定")
    stable_all = True
    for sc in selected:
        t = tally[sc["id"]]
        sid = sc["id"]
        if sid in SHOULD_SUBMIT:
            expect, ok = "应提单", t["submit"] == rounds and t["error"] == 0
        elif sid in SHOULD_NOT_SUBMIT:
            expect, ok = "不应提", t["no_submit"] == rounds and t["error"] == 0
        else:
            expect, ok = "—", None
        if ok is False:
            stable_all = False
        flag = ("✅" if ok else "❌") if ok is not None else "—"
        print(f"{sid:4} {t['submit']:>6} {t['no_submit']:>6} {t['error']:>6}  {expect:5} {flag}")
    print(f"\n结论: {'✅ 声明期望的场景全部稳定' if stable_all else '❌ 仍有不稳定场景（见上表 ❌）'}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="提单流程 live 测试")
    ap.add_argument("--base-url", default="http://localhost:8401")
    ap.add_argument("--retrieve", action="store_true", help="启用真实检索（默认跳过，聚焦提单逻辑）")
    ap.add_argument("--direct", action="store_true", help="不开服务直调模式（深调试）")
    ap.add_argument("--group", help="只跑某组：A/B/C/D/E/F/G/H")
    ap.add_argument("--only", help="指定场景，逗号分隔，如 A1,C1")
    ap.add_argument("--rounds", type=int, default=1, help="稳定性测试：跑 N 轮并汇总提单/拦截")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n中断")


if __name__ == "__main__":
    main()
