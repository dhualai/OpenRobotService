"""测试标题生成：发送 2 轮对话，检查 title SSE 事件"""
import httpx
import json
import uuid
import asyncio

BASE = "http://localhost:8401"
SESSION = f"test-title-{uuid.uuid4().hex[:8]}"

async def send_round(query: str, round_num: int):
    """发送一轮对话，收集所有 SSE 事件"""
    print(f"\n{'='*60}")
    print(f"Round {round_num}: {query}")
    events = []
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST", f"{BASE}/api/ai/qa/ask/stream",
            json={"session_id": SESSION, "query": query},
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                # SSE 格式: "event: xxx" 或 "data: {...}"
                if line.startswith("event: "):
                    events.append({"event": line[7:], "data": None})
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        data = line[6:]
                    if events and events[-1]["data"] is None:
                        events[-1]["data"] = data
                    else:
                        events.append({"event": "data", "data": data})

    for ev in events:
        ev_type = ev['event']
        if ev_type in ('title', 'result', 'first_token', 'done', 'error'):
            data_str = json.dumps(ev['data'], ensure_ascii=False)
            if len(data_str) > 200:
                data_str = data_str[:200] + "..."
            print(f"  [{ev_type}] {data_str}")
    return events

async def main():
    print(f"Session: {SESSION}")

    # Round 1: 第一次提问
    events1 = await send_round("AGV无法充电怎么办？", 1)

    # Round 2: 追问（触发标题生成）
    events2 = await send_round("试过了还是不行，充电桩指示灯不亮", 2)

    # 检查 title 事件
    title_events = [e for e in events2 if e['event'] == 'title']
    if title_events:
        print(f"\n[OK] Title generated: {title_events[0]['data']}")
    else:
        print("\n[FAIL] No title event received!")
        for e in events2:
            if e['event'] == 'result' and e['data']:
                if e['data'].get('title'):
                    print(f"  (title in result: {e['data']['title']})")
                else:
                    print(f"  (no title in result either)")

if __name__ == "__main__":
    asyncio.run(main())
