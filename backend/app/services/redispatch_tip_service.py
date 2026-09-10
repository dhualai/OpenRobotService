"""派单说明话术：列表 / 对话气泡 / 详情同一出口 `build_redispatch_tip`。

未派到倾向人用完整详情模板（原因 + 下一步）；Step0 短句仍走本函数其它分支。
可选 AI 润色仅 `build_redispatch_tip_detail`（默认关）。
"""
from typing import List, Optional

from app.core.config import settings

PROFILE_MISSING_LABEL = {
    "department": "部门",
    "job_level": "职级",
    "responsibility_modules": "责任模块",
}


def clean_reasoning_for_display(reasoning_raw, log, user_map) -> str:
    """把派单理由里的 users.id 换成姓名，给提单人 tip / 接单人「派单理由」共用。"""
    if not isinstance(reasoning_raw, str) or not reasoning_raw.strip():
        return reasoning_raw if isinstance(reasoning_raw, str) else ""
    txt = reasoning_raw
    for _cand in (getattr(log, "candidates", None) or []):
        if isinstance(_cand, dict):
            _cid = _cand.get("engineer_id")
            _cname = _cand.get("name") or (user_map or {}).get(_cid, _cid)
            if _cid and _cname:
                txt = txt.replace(f"ID:{_cid}", _cname)
                txt = txt.replace(f"({_cid})", f"({_cname})")
                txt = txt.replace(f"（{_cid}）", f"（{_cname}）")
                txt = txt.replace(_cid, _cname)
    for _cid, _cname in (user_map or {}).items():
        if _cname and _cid and isinstance(_cid, str) and _cid in txt:
            txt = txt.replace(_cid, _cname)
    return txt


def _pref_missing_zh(log) -> List[str]:
    preferred_id = getattr(log, "preferred_id", None)
    out: List[str] = []
    for cand in (getattr(log, "candidates", None) or []):
        if not isinstance(cand, dict) or cand.get("engineer_id") != preferred_id:
            continue
        for f in (cand.get("missing") or []):
            zh = PROFILE_MISSING_LABEL.get(str(f), str(f))
            if zh not in out:
                out.append(zh)
        break
    return out


def format_unmatched_preferred_tip(
    pref_name: str,
    assigned_name: str,
    reasoning: str = "",
    pref_missing_zh: Optional[List[str]] = None,
) -> str:
    """重派未派到倾向人：详情模板（列表 / 气泡 / 详情同一句）。"""
    pref_missing_zh = pref_missing_zh or []
    missing_txt = "、".join(pref_missing_zh) if pref_missing_zh else ""
    if pref_missing_zh:
        guide = (
            f"您倾向的【{pref_name}】画像不完整（缺：{missing_txt}），"
            "暂不足以直接指派，可补充画像后重新派单。"
        )
    else:
        guide = f"如需【{pref_name}】接单，可 @ 接单人 转派或重新派单。"
    reason_txt = f"，原因：{reasoning.strip()}" if (reasoning or "").strip() else ""
    return (
        f"很抱歉，未派给您指定的【{pref_name}】；"
        f"已优先改派给【{assigned_name}】处理{reason_txt}。"
        f"{guide}"
    )


def step0_blocks_redispatch(first_log) -> Optional[str]:
    """首轮已由 Step0 派上指定人 → 返回 assigned_id（拦截重派）。

    看第一轮日志，不看最新一轮（重派后 preferred_id 会变成表单倾向人）。
    不看 profile.specified_name：拼音命中也会写入对照原文，人已经派上了。
    找不到指定人走了智能派单：matched_pref 不是 True → 放行。
    """
    if first_log is None:
        return None
    if not getattr(first_log, "matched_pref", False):
        return None
    pref = getattr(first_log, "preferred_id", None)
    assigned = getattr(first_log, "assigned_id", None) or None
    if not pref:
        return None
    if assigned and pref != assigned:
        return None
    return assigned or pref


def build_redispatch_tip(log, user_map) -> Optional[str]:
    """派单结果提醒的唯一出口（列表 / 气泡 / 详情 tip_detail）。

    数据源：task_dispatch_log 结构化字段（preferred_id / pinyin_match /
    name_collision / profile.missing / profile.specified_name /
    profile.specified_multi / candidates / reasoning）。
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

    # Step0 指定人找不到：没有 users.id，只记下指定名。
    # 智能派单有门槛，只有画像完整的人能进候选池，不会派到画像不全的人。
    if specified_name and not preferred_id:
        return f"没找到您指定的【{specified_name}】，已按智能派单处理"

    # 重派未派到倾向人：详情模板，不再用「暂未采纳」短句
    if preferred_id and log.assigned_id and preferred_id != log.assigned_id:
        return format_unmatched_preferred_tip(
            preferred_name or preferred_id,
            assigned_name,
            reasoning=clean_reasoning_for_display(
                getattr(log, "reasoning", None) or "", log, user_map,
            ),
            pref_missing_zh=_pref_missing_zh(log),
        )

    parts = []
    if prof.get("specified_multi"):
        parts.append("工单暂时只允许分配一个处理人")
    pinyin_hit = getattr(log, "pinyin_match", False)
    if pinyin_hit and not prof.get("specified_multi"):
        if specified_name and specified_name != assigned_name:
            parts.append(
                f"系统找到的是【{assigned_name}】没有您指定的【{specified_name}】，"
                "有可能不准确"
            )
        else:
            parts.append(f"系统找到的是【{assigned_name}】，有可能不准确")
    collision = getattr(log, "name_collision", False)
    # 多人与同名都要提醒；无多人时拼音仍优先于同名（与旧口径一致）
    if collision and (prof.get("specified_multi") or not pinyin_hit):
        if prof.get("collision_random"):
            parts.append(f"指派人存在同名，已随机选择【{assigned_name}】")
        else:
            parts.append(f"指派人存在同名，已按评估选择【{assigned_name}】")
    tip = "；".join(parts) if parts else None

    # 画像不完整（可叠加）。已有主句时不写「您指定的」，避免和拼音对照句打架
    missing = (prof.get("missing") or []) if prof else []
    if missing:
        if tip:
            suffix = "接单人画像不完整。"
        elif preferred_id and preferred_id == log.assigned_id:
            suffix = "您指定的接单人画像不完整。"
        else:
            suffix = "该接单人画像不完整，待补充"
        tip = (f"{tip}；{suffix}") if tip else suffix
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
    """未派到倾向人的详情模板；仅当 REDISPATCH_TIP_AI_POLISH=True 时润色。

    主展示出口是 `build_redispatch_tip`（默认不润色，与列表一致）。
    """
    pref_missing_zh = pref_missing_zh or []
    missing_txt = "、".join(pref_missing_zh) if pref_missing_zh else ""
    template = format_unmatched_preferred_tip(
        pref_name, assigned_name, reasoning=reasoning, pref_missing_zh=pref_missing_zh,
    )
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
                return polished.strip().strip('"\u201c\u201d') or template
    except Exception:
        pass
    return template
