"""Step6 最终仲裁：静态铁律 / 辅助 / 产品附录 / 输出约定。

排名、工单、重派备注仍由 ranking/llm_decision.py 按单填入。
倾向人 / 原处理人只看候选人身上的 Step2 标签，不在这里再叙一遍。
"""

from __future__ import annotations

from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import person_anti_hallucination

IRON_RULES = (
    "【公共铁律】（必须遵守；违反即视为找不到人）\n"
    "1. 只能从【候选人排名】里选。engineer_id 必须精确复制「ID:」后面的 users.id，\n"
    "禁止自造、禁止把姓名填进 engineer_id。\n"
    "2. 很难决策、问题暂不清、名单里没人合适 → 输出 can_decide:false，\n"
    "不要因为没把握就默认精排 #1。\n"
    "3. 模块名是平级功能清单，不要按模块名硬套前端/后端技术分层。\n"
    "4. [提单人] 可以接（职责最匹配时）。\n"
    "[倾向接单人] 是用户勾选：正常情况不要拒绝这一选择，除非名单里另有非常合适的人。\n"
    "[原用户不满意的接单人] 默认避免再派回，除非画像确实最匹配。\n"
    "5. reasoning 给提单人看：一句话、用姓名、不要抄 users.id。\n"
    f"{person_anti_hallucination()}"
)

JUDGE_HINTS = (
    "【判断辅助】\n"
    "- 工单类型（support/feature/bug/problem/other）仅供参考，不要只因类型改选。\n"
    "- 职级 L1 一线 / L2 管理·审核 / L3 最高；用户要求上报上级时再抬职级。\n"
    "- 每人有「来源」：命中了 LLM / 相似工单 / 问题域的哪几路。名单是三路并集。\n"
    "- 总分取该人命中各路归一分的最高值；相似/问题域是绝对 0～1，不按本批第一名拉满。\n"
    "- 某路未命中不是不能接。只被历史捞回、且注明不在收紧名单的人，必须对照卡片判断能否接，不要只因历史高就派。\n"
    "- 没有 [倾向接单人] 时优先精排 #1；有则正常采纳用户选择，除非另有非常合适的人。"
)

# 每个产品一份附录。范围与 Step1【产品归属】对齐，不按前端/后端/算法分层。
PRODUCT_SCOPES = {
    "摇人吧服务号": (
        "小程序/后台/AI，不是车上软件，也不是地面调度或能拧的硬件。\n"
        "按子界面区分（我要摇人 / 系统任务 / 后台管理 / Agent / 数据分析），"
        "不要硬套前端/后端。下列负责人仅供参考，不是强制派给。"
    ),
    "调度USP": (
        "地面侧：任务下发/阻塞、路径规划、锁区、地图路网。"
        "不是车上软件，也不是能拧的硬件。\n"
        "按界面/功能对照候选人卡片判断谁能接，不要硬套前端/后端。"
    ),
    "车端软件": (
        "车上跑的：定位/SLAM、雷达、控制器、车载通信。"
        "不是地面调度，也不是能拧的硬件。\n"
        "按界面/功能对照候选人卡片判断谁能接，不要硬套前端/后端。"
    ),
    "车端硬件": (
        "车体/电池/电机/轮子/货叉等能拧的、能换的。"
        "不是车上软件，也不是地面调度。\n"
        "按界面/功能对照候选人卡片判断谁能接，不要硬套前端/后端。"
    ),
}

_UNKNOWN_SCOPE = (
    "未能对上已知产品（摇人吧服务号 / 调度USP / 车端软件 / 车端硬件）。\n"
    "按候选人卡片上的模块和职责判断谁能接，不要硬套前端/后端。"
)


def build_product_appendix(
    product: str,
    extra_lines: list | None = None,
    interfaces: list | None = None,
) -> str:
    """每个产品都出【产品附录】。认不出产品也出一块，标题用未识别产品。"""
    name = (product or "").strip() or "未识别产品"
    scope = PRODUCT_SCOPES.get(name, _UNKNOWN_SCOPE)
    lines = [f"【产品附录 · {name}】", scope]
    ifaces = [str(x).strip() for x in (interfaces or []) if str(x).strip()]
    if ifaces:
        lines.append("本产品界面：" + " / ".join(ifaces))
    for extra in extra_lines or []:
        if extra:
            lines.append(extra)
    return "\n".join(lines)


OUTPUT_CONTRACT = (
    "输出 JSON。\n"
    "engineer_id 必须精确复制「ID:」后面的 users.id；engineer_name 复制「姓名:」。\n"
    "很难决策时只输出 can_decide:false，不要填一个凑数的人。\n"
    '{"can_decide":true,"engineer_id":"<精确复制 ID:>","engineer_name":"<精确复制 姓名:>","confidence_score":0.85,"reasoning":"一句话简洁原因","decision_type":"auto"}\n'
    "或：\n"
    '{"can_decide":false,"reasoning":"当前画像下难以判定合适接单人"}\n'
    "decision_type: auto(>=0.8) / recommend(0.5-0.8) / fallback(<0.5)"
)
