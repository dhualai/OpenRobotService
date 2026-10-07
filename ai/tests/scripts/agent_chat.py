"""
交互式诊断测试 — 流式输出
运行: python agent_chat.py
"""
import json
import requests
import time

BASE = "http://127.0.0.1:8401/api/ai/qa/ask/stream"
SID = f"chat-{int(time.time())}"
SESSION = requests.Session()  # 连接池复用，避免每次 TCP+TLS 握手


def ask_stream(query: str):
    body = {"session_id": SID, "query": query}
    t_start = time.perf_counter()
    try:
        resp = SESSION.post(BASE, json=body, stream=True, timeout=(5, 120))
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(f"\n❌ 无法连接 {BASE}，确认服务已启动？")
        return
    except requests.exceptions.ReadTimeout:
        print(f"\n❌ 响应超时，LLM 可能卡住了")
        return
    except Exception as e:
        print(f"\n❌ {type(e).__name__}: {e}")
        return

    result_data = None
    current_event = None
    first_line = first_token = True
    t_first_line = 0

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if first_line:
            first_line = False
            t_first_line = round((time.perf_counter() - t_start) * 1000)
        if line.startswith("event: "):
            current_event = line[7:].strip()
            continue
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except json.JSONDecodeError:
            continue

        if "token" in ev:
            if first_token:
                ttft = round((time.perf_counter() - t_start) * 1000)
                print(f"\n🤖 ({ttft}ms, 首行={t_first_line}ms) ", end="", flush=True)
                first_token = False
            print(ev["token"], end="", flush=True)

        # 提单成功提示（status 事件，stage=submitted）
        if current_event == "status" and isinstance(ev, dict) and ev.get("stage") == "submitted":
            print(f"\n\n🎫 工单已生成！")
            print(f"   工单号: {ev.get('ticket_id', '?')}")
            print(f"   标题: {ev.get('title', '?')}")
            print(f"   数据库ID: {ev.get('db_id', '?')}")
        if current_event == "status" and isinstance(ev, dict) and ev.get("stage") == "submit_failed":
            print(f"\n\n❌ 提单失败: {ev.get('error', '未知错误')}")
        if current_event == "status" and isinstance(ev, dict) and ev.get("stage") == "need_fields":
            missing = ev.get("missing_fields", [])
            prompt = ev.get("prompt", "")
            labels = {"project": "项目名称"}
            cn = [labels.get(m, m) for m in missing]
            print(f"\n\n⚠️  还缺: {'、'.join(cn)}")
            if prompt:
                print(f"   {prompt}")
        if current_event == "status" and isinstance(ev, dict) and ev.get("stage") == "need_info":
            missing = ev.get("missing_info", [])
            print(f"\n\n⚠️  信息不足: {missing}")

        if current_event == "result":
            result_data = ev
            current_event = None

    print()
    if first_token:
        print("🤖 (无响应 — 服务端未返回任何 token)")
    if result_data:
        show_result(result_data)


def show_result(d: dict):
    timing = d.get("timing", {})
    if timing:
        parts = [f"{k}={v}ms" for k, v in timing.items()
                 if k not in ("total", "prompt_chars") and isinstance(v, (int, float))]
        if timing.get("prompt_chars"):
            parts.append(f"prompt={timing['prompt_chars']}字")
        if parts:
            print(f"  ⏱  {' | '.join(parts)}")
    ticket = d.get("ticket", {})
    if ticket:
        # ticket 结构: {type:"ticket", data:{ticket:{title,type,priority,...}, db_id, notice}, ...}
        inner = ticket.get("data", {}).get("ticket", {}) if isinstance(ticket, dict) else {}
        if inner:
            print(f"\n🎫 工单: {inner.get('title', '')} | {inner.get('type', '')} | {inner.get('priority', '')}")
            if inner.get('ticket_id'):
                print(f"   ticket_id: {inner['ticket_id']}")
        notice = ticket.get("data", {}).get("notice", "")
        if notice:
            print(f"📢 {notice}")


DRAFT = {}  # 缓存 prepare 返回的草稿，供 confirm 使用


def prepare_ticket():
    """按钮路径第 1 步：生成工单草稿（/qa/ticket/prepare）。
    保底必填字段不足 → stage=not_ready，需回对话补充。
    信息齐全 → stage=draft_ready，弹窗展示草稿供确认。"""
    global DRAFT
    resp = SESSION.post("http://localhost:8401/api/ai/qa/ticket/prepare",
                         json={"session_id": SID}, timeout=30)
    data = resp.json()
    # API 层统一包在 data 里: {code:0, data:{stage, draft, missing_info, code, message, ...}}
    inner = data.get("data", {})
    stage = inner.get("stage", "")
    if inner.get("code") == 1 or stage == "not_ready":
        missing = inner.get("missing_info", [])
        if missing:
            print(f"\n⚠️  信息不足，还差：{'、'.join(missing)}")
            print(f"   请在对话中补充后再 !prepare")
        else:
            print(f"\n❌ prepare 失败: {inner.get('message', data.get('message', ''))}")
        DRAFT = {}
    else:
        draft = inner.get("draft", {})
        DRAFT = draft
        missing = inner.get("missing_fields", [])
        print(f"\n📋 工单草稿已生成（stage={stage or '?'}）:")
        print(f"   标题: {draft.get('title', '?')}")
        print(f"   类型: {draft.get('type', '?')}  优先级: {draft.get('priority', '?')}")
        print(f"   项目: {draft.get('project', '?')}")
        print(f"   描述: {(draft.get('description') or '')[:80]}")
        if missing:
            print(f"   ⚠️  缺弹窗字段: {missing}")
        if stage == "need_fields" and "project" in missing:
            print(f"   → 补充项目: !confirm project=项目名")
        else:
            print(f"   → 确认无误用 !confirm，修改字段用 !confirm key=value")


def confirm_ticket(args: str = ""):
    """按钮路径第 2 步：确认提交工单（/qa/ticket/confirm）。
    参数覆盖草稿字段，空格分隔: !confirm project=本川 priority=紧急"""
    global DRAFT
    overrides = {}
    # 清理括号和引号，方便粘贴
    clean = args.strip().strip("[]（）()").replace("'", "").replace('"', '')
    if clean:
        for part in clean.split():
            if "=" in part:
                k, v = part.split("=", 1)
                overrides[k.strip()] = v.strip()
    resp = SESSION.post("http://localhost:8401/api/ai/qa/ticket/confirm",
                         json={"session_id": SID, "overrides": (overrides if overrides else {})},
                         timeout=30)
    data = resp.json()
    # API 层: {code:0, data:{code, message, missing_fields, stage, missing_info, data:{ticket, db_id, notice}}}
    inner = data.get("data", {})
    i_code = inner.get("code", 0)
    i_msg = inner.get("message", "")
    i_stage = inner.get("stage", "")

    if i_code == 1 or i_stage == "not_ready":
        # 保底必填字段不足 → not_ready
        missing = inner.get("missing_info", [])
        if missing:
            print(f"\n⚠️  信息不足，还差：{'、'.join(missing)}")
            print(f"   请在对话中补充后再试")
        # 弹窗字段（如 project）缺失
        elif inner.get("missing_fields"):
            print(f"\n⚠️  弹窗字段缺失: {inner.get('missing_fields')}")
            print(f"   {i_msg}")
            if "project" in (inner.get("missing_fields") or []):
                print(f"   → 补充: !confirm project=项目名")
        else:
            print(f"\n❌ confirm 失败: {i_msg or data.get('message', '')}")
    elif data.get("code") == 0:
        ticket_data = inner.get("data", {})
        t = ticket_data.get("ticket", {}) or inner.get("ticket", {})
        print(f"\n🎫 工单已生成: {t.get('title', '')}")
        print(f"   ticket_id: {t.get('ticket_id')}  类型: {t.get('type', '?')}  优先级: {t.get('priority', '?')}")
        print(f"   数据库ID: {ticket_data.get('db_id', inner.get('db_id', '?'))}")
        DRAFT = {}
    else:
        print(f"\n❌ confirm 失败: {i_msg or data.get('message', '')}")


def submit_ticket():
    """直接提单（/qa/submit）—— 不走 prepare/confirm 流程，服务端校验通过即入库。"""
    resp = SESSION.post("http://localhost:8401/api/ai/qa/submit",
                         json={"session_id": SID}, timeout=30)
    data = resp.json()
    if data.get("code") == 0:
        t = data.get("data", {}).get("ticket", {})
        print(f"\n🎫 工单已生成: {t.get('title', '')}")
        print(f"   ticket_id: {t.get('ticket_id')}  类型: {t.get('type', '?')}  优先级: {t.get('priority', '?')}")
        print(f"   数据库ID: {data.get('data', {}).get('db_id', '?')}")
    else:
        print(f"\n❌ 转工单失败: {data.get('message', '')}")


print("=" * 65)
print("  智能诊断 Agent — 流式交互")
print(f"  Session: {SID}")
print("=" * 65)
print("  转工单三条路径：")
print("    1. 对话提单 ── 输入「转工单」等关键词，LLM 判断信息是否齐全")
print("    2. 按钮提单 ── !prepare 生成草稿 → !confirm [字段=值] 确认提交")
print("    3. 直接提单 ── !submit（不走 prepare/confirm，少用）")
print("  quit / exit / q ── 退出")
print("=" * 65)

while True:
    try:
        msg = input("\n👤 > ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n退出。")
        break
    if not msg:
        continue
    if msg.lower() in ("quit", "exit", "q"):
        print("退出。")
        break
    if msg.startswith("!prepare") or msg.startswith("!p "):
        prepare_ticket()
    elif msg.startswith("!confirm"):
        args = msg[len("!confirm"):].strip()
        confirm_ticket(args)
    elif msg.startswith("!submit") or msg.startswith("!s"):
        submit_ticket()
    else:
        ask_stream(msg)
