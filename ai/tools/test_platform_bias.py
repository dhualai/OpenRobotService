"""USP 偏置修复验证：不带"服务号"关键词的问法应归到服务号平台，而非 USP。

用法:
    python -m ai.tools.test_platform_bias
"""
import asyncio
import json
import sys
import time
import httpx

BASE_URL = "http://localhost:8401"

QUESTIONS = [
    "为什么我看不到工单",
    "权限是怎么配置的",
    "工单的状态有哪些，分别是什么意思",
    "服务号能做什么",
]


async def converse(client, sid, query):
    tokens, result, err = [], None, None
    async with client.stream(
        "POST", f"{BASE_URL}/api/ai/qa/ask/stream",
        json={"session_id": sid, "query": query},
        timeout=120.0,
    ) as resp:
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except Exception:
                continue
            if "token" in data:
                tokens.append(data["token"])
            elif "error" in data:
                err = data.get("error", str(data))
            elif "action" in data or "ticket" in data:
                result = data
    seen = "".join(tokens)
    return (result or {}).get("action", "?"), seen or (err or "(无输出)")


async def main():
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i, q in enumerate(QUESTIONS):
            sid = f"bias-{int(time.time() * 1000) % 100000}-{i}"
            t0 = time.time()
            action, seen = await converse(client, sid, q)
            print(f"\n=== [{i+1}] 问: {q}  (action={action}, {int(time.time()-t0)}s) ===")
            print(f"    答: {seen[:600]}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
