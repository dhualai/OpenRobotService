"""
交互式诊断测试 — 流式输出
运行: python agent_chat.py
"""
import json
import requests
import time

BASE = "http://127.0.0.1:8400/api/ai/qa/ask/stream"
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
        print(f"\n🎫 工单: {ticket.get('title', '')} | {ticket.get('category')} | {ticket.get('urgency')}")
    notice = d.get("ticket_notice", "")
    if notice:
        print(f"📢 {notice}")


def submit_ticket():
    """模拟点击转工单按钮"""
    resp = SESSION.post("http://localhost:8400/api/ai/qa/submit",
                         json={"session_id": SID}, timeout=30)
    data = resp.json()
    if data.get("code") == 0:
        t = data.get("data", {}).get("ticket", {})
        print(f"\n🎫 工单已生成: {t.get('title', '')}")
        print(f"   ticket_id: {t.get('ticket_id')}")
        print(f"   分类: {t.get('category')}  紧急度: {t.get('urgency')}")
    else:
        print(f"\n❌ 转工单失败: {data.get('message', '')}")


print("=" * 60)
print("  智能诊断 Agent — 流式交互")
print(f"  Session: {SID}")
print("  输入「转工单」生成工单 | quit 退出")
print("=" * 60)

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
    if msg == "转工单":
        submit_ticket()
    else:
        ask_stream(msg)
