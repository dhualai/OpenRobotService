"""
首 token 自动化对比测试
  - 直连 DeepSeek API (纯对话 vs Agent prompt)
  - 走本地服务端 (Agent)
各 3 次取平均

用法: 先启动 FastAPI 服务，然后运行此脚本
"""
import json, time, os, httpx, urllib.request
from pathlib import Path
from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent
_env_path = (_SCRIPT_DIR / ".." / ".env").resolve()
print(f"  加载 .env: {_env_path} (exists={_env_path.exists()})")
load_dotenv(_env_path)

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
if not API_KEY:
    print("  ❌ DEEPSEEK_API_KEY 为空，请检查 .env")
    exit(1)
print(f"  API: {BASE_URL}  model: {MODEL}  key: {API_KEY[:20]}...")
SERVER = "http://localhost:8000"

SYS = (
    "你是工业移动机器人（AGV/AMR）领域的技术支持专家，领域锁定，不做通用服务台。"
    "你所服务的产品是 USP（Universal Scheduling Platform）大调度系统，"
    "用于 AGV/AMR 的调度管理、车辆管理、设备管理、地图编辑与监控运维。"
    "你的用户可能是工程师、操作员或管理人员，直接针对问题本身回答，不用区分角色。"
    "USP 是网页端系统（PC浏览器访问），没有移动端APP。"
    "回答中严禁提及'手机'、'移动端'、'APP'、'屏幕阅读'等移动端概念。"
    "回答要求：清晰、结构化、适合网页端阅读。"
    "严禁给出手机、电脑等消费电子产品的通用回答，严禁超出 AGV/AMR 领域。"
)

DIAG = """你是一个诊断 Agent，帮用户排查 AGV/AMR 问题。解决不了就转工单。

## 对话
用户：{query}

## 状态：问题={query} | 已收集=（暂无）| 已排除=（暂无）| 推测=（待推断）
## 知识库：（跳过检索）
## 第1轮

---
输出 JSON：
```json
{{"thinking":"一句话推理","action":"ask|answer|escalate","state_update":{{"problem_summary":"概述","ruled_out":[],"hypotheses":[],"collected_info":{{}}}}}}
```
JSON 之后直接写回复内容，不需要任何分隔符。追问要具体，信息不足 escalate，同问题追2轮无果直接 escalate。语气像工程师，别重复问。"""

ROUNDS = 3
QUERY = "AGV突然不动了，闪红灯，错误码E001"


def avg(vals):
    v = [x for x in vals if x > 0]
    return f"{sum(v)//len(v)}ms" if v else "N/A"


# ===== 直连 DeepSeek =====
async def direct_first_token(messages: list, max_tokens: int, stop_at_sep=False) -> list:
    results = []
    for _ in range(ROUNDS):
        async with httpx.AsyncClient(timeout=60, trust_env=False) as c:
            t0 = time.perf_counter()
            buf = ""
            async with c.stream("POST", f"{BASE_URL}/chat/completions", json={
                "model": MODEL, "max_tokens": max_tokens, "temperature": 0.5,
                "stream": True, "messages": messages,
            }, headers={"Authorization": f"Bearer {API_KEY}"}) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data: ") and line[6:] != "[DONE]":
                        try:
                            tok = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                        except Exception:
                            continue
                        if not tok:
                            continue
                        if not stop_at_sep:
                            results.append(round((time.perf_counter() - t0) * 1000))
                            break
                        buf += tok
                        # 括号计数：{+1 }-1，归零时 JSON 结束
                        if "}" in tok:
                            d = buf.count("{") - buf.count("}")
                            if d <= 0 and "{" in buf:
                                results.append(round((time.perf_counter() - t0) * 1000))
                                break
                else:
                    results.append(-1)
    return results


# ===== 走服务端 =====
def server_first_token() -> list:
    results = []
    for _ in range(ROUNDS):
        sid = f"bench-{int(time.time())}"
        body = json.dumps({"session_id": sid, "query": QUERY,
                            "skip_retrieval": True}).encode()
        req = urllib.request.Request(f"{SERVER}/api/ai/qa/ask/stream", data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            for line_bytes in resp:
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                if "token" in ev:
                    results.append(round((time.perf_counter() - t0) * 1000))
                    break
        except Exception as e:
            print(f"  ⚠️ server err: {e}")
            results.append(-1)
    return results


async def main():
    print(f"模型: {MODEL} | 查询: {QUERY[:30]}… | 各测{ROUNDS}次取平均")
    print("=" * 70)

    # 1. 直连纯对话
    raw = await direct_first_token([
        {"role": "system", "content": SYS},
        {"role": "user", "content": QUERY},
    ], max_tokens=200)

    # 2. 直连 Agent (跟服务端完全相同: sys 单独 + DIAG)
    diag_prompt = DIAG.format(query=QUERY)
    agent = await direct_first_token([
        {"role": "system", "content": SYS},
        {"role": "user", "content": diag_prompt},
    ], max_tokens=2000, stop_at_sep=True)

    # 3. 服务端 Agent
    srv = server_first_token()

    print(f"  {'纯对话(直连)':<20s} {avg(raw):>10s}")
    print(f"  {'Agent(直连)':<20s} {avg(agent):>10s}")
    print(f"  {'Agent(服务端)':<20s} {avg(srv):>10s}")
    a_srv = avg(srv).replace('ms', '')
    a_agent = avg(agent).replace('ms', '')
    if a_srv.isdigit() and a_agent.isdigit():
        print(f"  {'差值(服务端开销)':<20s} ~{int(a_srv) - int(a_agent)}ms")
    print("=" * 70)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
