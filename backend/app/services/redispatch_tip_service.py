"""二次派单感知增强（M3 高情商回复）：未派到指定人时的「派单说明」话术生成。

从 `app/modules/tasks/api/task.py` 的 `_build_redispatch_tip_detail` 抽离，收敛为独立 service：
- 模板拼装 + 分支引导（确定性文案，零 LLM 成本）
- 可选 AI 润色（REDISPATCH_TIP_AI_POLISH=True 时用 ModelService 润色，失败降级模板）；prompt 收敛在此

调用方：`task.py::get_task` 组装 `redispatch.result.tip_detail` 时调用。
"""
from typing import List, Optional

from app.core.config import settings


def build_redispatch_tip(log, user_map) -> Optional[str]:
    """派单结果提醒的唯一出口（列表摘要 / 详情非「未派到」分支）。

    数据源：task_dispatch_log 结构化字段（preferred_id / pinyin_match /
    name_collision / profile.missing / profile.specified_name）。
    Step0 指定人与重派倾向人走同一条规则。
    """
    if log is None:
        return None
    assigned_name = user_map.get(log.assigned_id, log.assigned_id)
    preferred_id = log.preferred_id
    preferred_name = user_map.get(preferred_id, preferred_id) if preferred_id else None
    prof = log.profile if isinstance(getattr(log, "profile", None), dict) else {}
    specified_name = (prof.get("specified_name") or "").strip()

    # Step7 无人可派：不编接单人，但要让提单人看到失败说明（优先于其它分支）
    if prof.get("unassignable"):
        return (
            "暂时无法派单：项目未配置对接人和项目经理，工单还没有接单人。"
            "配置后系统会继续尝试。"
        )

    # Step0 指定人找不到：没有 users.id，只记下了指定名
    if specified_name and not preferred_id:
        return f"没找到您指定的【{specified_name}】，已按智能派单处理"

    # ② 未派到指定人 / 倾向人
    if preferred_id and preferred_id != log.assigned_id:
        tip = f"很抱歉，您指定的【{preferred_name}】暂未采纳，已改派更合适的【{assigned_name}】处理"
    # ④ 拼音命中（已派到解析出的人）
    elif getattr(log, "pinyin_match", False):
        if specified_name and specified_name != assigned_name:
            tip = (
                f"系统找到的是【{assigned_name}】没有您指定的【{specified_name}】，"
                "有可能不准确"
            )
        else:
            tip = f"系统找到的是【{assigned_name}】，有可能不准确"
    # ③ 同名
    elif getattr(log, "name_collision", False):
        if prof.get("collision_random"):
            tip = f"指派人存在同名，已随机选择【{assigned_name}】"
        else:
            tip = f"指派人存在同名，已按评估选择【{assigned_name}】"
    else:
        tip = None

    # ① 画像不完整（可叠加；同名随机多半两边都不完整）
    missing = (prof.get("missing") or []) if prof else []
    if missing:
        if preferred_id and preferred_id == log.assigned_id:
            suffix = "您指定的接单人画像不完整。"
        else:
            suffix = "该接单人画像不完整，待补充"
        tip = (f"{tip}；{suffix}") if tip else suffix
    # 部门职责画像：只认库，yaml 不补；库里没有就提醒去后台补
    if prof.get("no_dept_profile"):
        suffix = "没有部门画像，请到后台补充部门职责"
        tip = (f"{tip}；{suffix}") if tip else suffix
    return tip


async def build_redispatch_tip_detail(
    pref_name: str,
    assigned_name: str,
    reasoning: str = "",
    pref_missing_zh: Optional[List[str]] = None,
) -> str:
    """二次派单感知增强（M3 高情商回复）：未派到指定人时的完整情商话术。

    默认纯模板（settings.REDISPATCH_TIP_AI_POLISH=False）：文案确定、零 LLM 成本、可复用。
    可选 AI 润色：仅当 REDISPATCH_TIP_AI_POLISH=True 时把模板喂给 LLM 润色，失败降级模板。

    模板带分支引导：
    - 倾向人画像不完整（pref_missing_zh 非空）→ 点明缺失项 + 引导先补充画像后重新派单；
    - 画像完整 → 引导「@ 接单人 帮忙转派」或重新派单。
    返回 string。
    """
    pref_missing_zh = pref_missing_zh or []
    missing_txt = "、".join(pref_missing_zh) if pref_missing_zh else ""

    # 分支引导段（尽量精简）
    if pref_missing_zh:
        guide = (
            f"您倾向的【{pref_name}】画像不完整（缺：{missing_txt}），"
            "暂不足以直接指派，可补充画像后重新派单。"
        )
    else:
        guide = f"如需【{pref_name}】接单，可 @ 接单人 转派或重新派单。"

    # 备注：reasoning 由派单引擎（LLM 决策 / fallback）在源头就生成「一句话简洁原因」，
    # 因此这里原样展示即可，不再做截断/取首句处理；配合精简模板，整段话术保持简短。
    if reasoning:
        reason_txt = f"，原因：{reasoning.strip()}"
    else:
        reason_txt = ""
    template = (
        f"很抱歉，未派给您指定的【{pref_name}】；"
        f"已优先改派给【{assigned_name}】处理{reason_txt}。"
        f"{guide}"
    )

    # 默认纯模板（settings.REDISPATCH_TIP_AI_POLISH=False，零 LLM 成本、文案确定可复用）。
    # 仅当显式开启 AI_POLISH 时才用 LLM 润色；失败 / LLM_STREAM 返回生成器 → 仍降级模板。
    try:
        if getattr(settings, "REDISPATCH_TIP_AI_POLISH", False):
            from app.modules.call.services.model_service import ModelService

            prompt = (
                "下面是一段给工单提单人的「派单结果说明」。请把它润色成更自然、有温度、简洁的中文话术，"
                "保留以下要点：1) 未派到提单人指定的处理人并致歉；2) 说明实际改派给了谁；"
                f"3) 若倾向人画像不完整({missing_txt or '无'})则引导先补画像，否则引导可 @ 接单人转派或重新派单。\n"
                "要求：口语化但专业、言简意赅、**尽可能简洁精炼（能一句话说清就不多写）**，"
                "不写编造的额外信息、不要用 Markdown、不要出现散列 id。\n"
                f"原始模板：\n{template}"
            )
            polished = await ModelService.generate_answer(
                prompt,
                system_prompt="你是工单系统的亲和客服助手，负责把派单结果转述给提单人，语气温和、简洁、可信。",
            )
            if isinstance(polished, str) and polished.strip():
                # 逗号/句号结尾兜底规范化（去掉可能的引号包裹等）
                return polished.strip().strip('"\u201c\u201d') or template
    except Exception:
        # AI 润色失败 / LLM_STREAM 场景返回流式生成器 → 降级为模板
        pass
    return template
