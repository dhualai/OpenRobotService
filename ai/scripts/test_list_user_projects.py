# -*- coding: utf-8 -*-
"""list_user_projects 工具三层验证。

1. _format_user_projects_block 纯函数：0/3/7 个项目的截断与提示语
2. _execute_plan_tools 执行器（stub _get_user_projects）：身份只认 created_by
3. 真实 flash 规划器路由：正例应调 list_user_projects，反例禁调

运行：python ai/scripts/test_list_user_projects.py
"""
import asyncio
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    AiDiagnosisPlatform,
    _PLANNER_SYSTEM,
    _PLANNER_TOOLS,
)

FAILS = []


def check(label, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


# ── 1. 格式化纯函数 ──
def test_formatter():
    print("\n=== 1. 格式化纯函数 ===")
    names = lambda n: [{"name": f"项目{i}", "code": f"P{i}"} for i in range(1, n + 1)]

    b0 = AiDiagnosisPlatform._format_user_projects_block([])
    check("0 个：暂无项目措辞", "暂无关联项目" in b0 and "不要编造" in b0)
    check("0 个：也带边界话术（问进度场景指后台）",
          "后台管理" in b0 and "禁止追问" in b0)

    b3 = AiDiagnosisPlatform._format_user_projects_block(names(3))
    check("3 个：全部列出", all(f"项目{i}" in b3 for i in (1, 2, 3)))
    check("3 个：提示语=全部", "全部" in b3 and "仅展示" not in b3)
    check("3 个：收口话术（禁追问+指后台）",
          "禁止追问" in b3 and "后台管理" in b3 and "说出项目名称" not in b3)

    b7 = AiDiagnosisPlatform._format_user_projects_block(names(7))
    check("7 个：只列前 5", all(f"项目{i}" in b7 for i in range(1, 6)))
    check("7 个：第 6/7 个物理截断", "项目6" not in b7 and "项目7" not in b7)
    check("7 个：提示语=共 7 仅展示前 5", "共 7 个项目" in b7 and "前 5 个" in b7)
    check("7 个：收口话术（禁追问+指后台）",
          "禁止追问" in b7 and "后台管理" in b7 and "说出项目名称" not in b7)

    b5 = AiDiagnosisPlatform._format_user_projects_block(names(5))
    check("5 个：边界走全部分支", "共 5 个项目" in b5 and "全部" in b5 and "仅展示" not in b5)


# ── 2. 执行器（stub 身份隔离） ──
async def test_executor():
    print("\n=== 2. 执行器 stub ===")
    seen = {}

    class _Stub(AiDiagnosisPlatform):
        async def _get_user_projects(self, username):
            seen["username"] = username
            return [{"name": "甲项目", "code": "A1"}, {"name": "乙项目", "code": "B2"}]

    state = SimpleNamespace(ticket_ref_context=None)
    res = await AiDiagnosisPlatform._execute_plan_tools(
        _Stub(), "sess_x", state, [("list_user_projects", {})], "tester")
    check("身份只认 created_by（LLM 参数被无视）",
          seen.get("username") == "tester" and "甲项目" in res)
    check("资料块拼进 parts", "【用户名下项目】" in res)

    class _FailStub(AiDiagnosisPlatform):
        async def _get_user_projects(self, username):
            raise RuntimeError("db down")

    res2 = await AiDiagnosisPlatform._execute_plan_tools(
        _FailStub(), "sess_x", state, [("list_user_projects", {})], "tester")
    check("查询异常降级为空列表措辞（不抛错）", "暂无关联项目" in res2)


# ── 3. 真实 flash 规划器路由 ──
async def test_planner():
    print("\n=== 3. 真实规划器路由（flash） ===")
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


async def main():
    test_formatter()
    await test_executor()
    await test_planner()
    print("\n" + ("❌ 失败: " + "; ".join(FAILS) if FAILS else "✅ 全部通过"))


if __name__ == "__main__":
    asyncio.run(main())
