"""
纯 LLM 流式调用 —— 参照 chat.py
用法: python llm_chat.py "你的问题"
"""
import json, time, sys, requests

BASE = "http://localhost:8000"


def call(query: str):
    body = {
        "session_id": f"ext-{int(time.time())}",
        "query": query,
        "max_tokens": 2000,
        "temperature": 0.7,
    }
    t0 = time.perf_counter()
    resp = requests.post(f"{BASE}/api/ai/chat/stream", json=body, stream=True, timeout=120)
    resp.raise_for_status()

    first_token = True
    text = ""

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if "token" in ev:
            if first_token:
                ttft = round((time.perf_counter() - t0) * 1000)
                print(f"\n🤖 ({ttft}ms) ", end="", flush=True)
                first_token = False
            print(ev["token"], end="", flush=True)
            text += ev["token"]

    total = round((time.perf_counter() - t0) * 1000)
    print(f"\n  ──  首token={round((time.perf_counter() - t0) * 1000) if first_token else ttft}ms  "
          f"总耗时={total}ms  回复{len(text)}字")


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]).strip()
    if not q:
        q = "小猫不吃猫粮怎么办"
    print(f"query: {q}")
    call(q)
