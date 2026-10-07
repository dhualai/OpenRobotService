# -*- coding: utf-8 -*-
"""list_user_projects 工具：真实规划器路由校验（需真实 LLM，仅手动运行）。

依赖 ai/.env 里的 LLM 配置，不属于可断言的离线测试，故留在 scripts/ 下按脚本运行；
离线部分（格式化纯函数、执行器身份隔离）已收编为 ai/tests/test_list_user_projects.py。

运行：python ai/scripts/check_list_user_projects_planner.py
"""
import asyncio
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    _PLANNER_SYSTEM,
    _PLANNER_TOOLS,
)

FAILS = []


def check(label, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


async def main():
    print("\n=== 真实规划器路由（flash） ===")
    from ai.core import get_intent_client

    llm = await get_intent_client()

    async def route(msg):
        ctx = f"以下是最近几轮对话（仅作上下文）：\n用户：你好\n\n本轮用户消息：{msg}"
        resp = await llm.complete_with_tools(
            tools=_PLANNER_TOOLS, prompt=ctx, system_prompt=_PLANNER_SYSTEM,
            max_tokens=250, temperature=0.0, thinking=False)
        tcs = [(tc.get("name"), tc.get("arguments"))
               for tc in (resp.get("tool_calls") or [])
               if isinstance(tc.get("arguments"), dict)]
        print(f"  [{msg}] → {tcs}")
        return tcs

    tcs = await route("我名下有哪些项目")
    check("正例1「我名下有哪些项目」", any(n == "list_user_projects" for n, _ in tcs))

    tcs = await route("我参与的项目都有什么")
    check("正例2「我参与的项目」", any(n == "list_user_projects" for n, _ in tcs))

    tcs = await route("帮我看看我关联了哪些项目")
    check("正例3「我关联了哪些项目」", any(n == "list_user_projects" for n, _ in tcs))

    tcs = await route("本川项目的进度怎么样了")
    check("正例4「项目进度」→ 也调（返回边界话术）",
          any(n == "list_user_projects" for n, _ in tcs))

    tcs = await route("AGV 报错 E201 怎么处理")
    check("反例1 故障咨询不调", all(n != "list_user_projects" for n, _ in tcs))

    tcs = await route("我们项目的车不动了，帮我看看")
    check("反例2 故障类提到项目不调", all(n != "list_user_projects" for n, _ in tcs))

    tcs = await route("帮我提个工单，车不动了")
    check("反例3 提单诉求不调", all(n != "list_user_projects" for n, _ in tcs))

    print("\n" + ("❌ 失败: " + "; ".join(FAILS) if FAILS else "✅ 全部通过"))


if __name__ == "__main__":
    asyncio.run(main())
