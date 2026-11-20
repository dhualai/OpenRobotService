"""派单开发者模式：基于真实工程师画像生成可控模拟工单。

这里属于 assigner AI 模块，不依赖前端，也不放业务后端。
前端只负责展示/载入/触发；backend 只做权限和代理；AI 模块负责理解真实画像和派单分支。
"""
from typing import Any, Dict

from ai.core.logging import get_logger
from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import load_engineers

logger = get_logger("ASSIGNER_DEBUG_SCENARIOS")


def build_real_profile_examples() -> Dict[str, Dict[str, Any]]:
    """为每个分支生成基于当前真实工程师画像的模拟工单示例。

    这些示例用于前端「载入例子 / 跑此例」。它们不 mock 数据，
    只尽量从真实画像里挑选存在的工程师、产品和模块；
    当前环境无法稳定制造的分支会在 note 里说明需要人工调整。
    """
    try:
        engineers = load_engineers() or []
    except Exception as e:
        logger.warning("生成派单示例读取工程师画像失败: %s", e)
        engineers = []

    primary = next((e for e in engineers if _complete(e)), None) or (engineers[0] if engineers else None)
    secondary = next((e for e in engineers if primary and e.id != primary.id and _complete(e)), None)
    incomplete = next((e for e in engineers if e.id and e.name and not _complete(e)), None)
    product, module = _first_product_module(primary)
    base_title = f"{product}{module}异常" if product or module else "车辆在站点停下不动"
    base_desc = f"{product or '机器人'}{module or '任务'}出现异常，请根据真实画像派单。"

    primary_name = getattr(primary, "name", "") or "请填写真实工程师姓名"
    primary_id = getattr(primary, "id", "") or ""
    secondary_id = getattr(secondary, "id", "") or primary_id
    incomplete_id = getattr(incomplete, "id", "") or ""

    def ex(title: str, desc: str, **extra: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"title": title, "desc": desc, **extra}
        if not primary:
            payload["note"] = "当前未加载到真实工程师画像，请先确认 users 表和画像同步。"
        return payload

    missing_profile_note = "当前真实画像里未找到画像不完整人员；如需稳定命中，请手动填写一个画像不完整的 users.id。"
    difficult_note = "该分支受当前模块树、历史索引、LLM 和项目兜底配置影响，示例只提供真实画像输入，最终以实际命中路径为准。"

    return {
        "step0-hit": ex(f"指定处理人：{primary_name}", base_desc, expected_branch="step0-hit"),
        "step0-miss": ex("指定处理人：不存在的测试人员999", base_desc, expected_branch="step0-miss"),
        "preferred-twice": ex(
            "倾向处理人连续确认测试", base_desc,
            preferred_assignee=primary_id, repeat=2, expected_branch="preferred-twice",
            note="连续两次同一倾向人需要重复跑同一个示例 2 次。",
        ),
        "pref-incomplete": ex(
            "倾向人画像不完整准入测试", base_desc,
            preferred_assignee=incomplete_id, expected_branch="pref-incomplete",
            note="使用真实画像中的不完整人员。" if incomplete_id else missing_profile_note,
        ),
        "step1-hard": ex(base_title, base_desc, robot_type=product or "", expected_branch="step1-hard", note=difficult_note),
        "step1-soft": ex(f"{base_title}，可能需要相关团队协助", base_desc, robot_type=product or "", expected_branch="step1-soft", note=difficult_note),
        "step1-no-filter": ex("通用现场问题", "用户只描述现场异常，没有明显部门或产品线索。", expected_branch="step1-no-filter", note=difficult_note),
        "step1-empty": ex("不存在产品线测试", "这是一个不存在产品线和模块的测试工单。", robot_type="不存在产品线", expected_branch="step1-empty", note=difficult_note),
        "step2-severe": ex("坏了", "不知道什么情况。", dispatch_hint="severe", expected_branch="step2-severe"),
        "step2-normal": ex(base_title, base_desc, expected_branch="step2-normal"),
        "step3-llm": ex(base_title, base_desc, robot_type=product or "", expected_branch="step3-llm", note=difficult_note),
        "step3-similar": ex("历史相似工单召回测试", f"之前类似的{module or '任务'}问题再次出现。", expected_branch="step3-similar", note=difficult_note),
        "step3-cluster": ex("问题簇召回测试", f"多次出现的{module or '现场'}异常，需要从问题簇中找常见处理人。", expected_branch="step3-cluster", note=difficult_note),
        "step3-empty": ex("全新未知问题", "没有历史、画像和问题簇线索的全新问题。", expected_branch="step3-empty", note=difficult_note),
        "step4-rank": ex(base_title, base_desc, expected_branch="step4-rank", note=difficult_note),
        "step4-preferred-floor": ex(base_title, base_desc, preferred_assignee=primary_id, expected_branch="step4-preferred-floor", note=difficult_note),
        "step4-union": ex("历史捞回候选测试", f"{module or '任务'}问题和历史处理记录高度相似。", expected_branch="step4-union", note=difficult_note),
        "step6-success": ex(base_title, base_desc, robot_type=product or "", expected_branch="step6-success", note=difficult_note),
        "step6-fail": ex("LLM 无法确定处理人测试", "描述模糊且候选差异不明显，请判断是否无法给出明确处理人。", expected_branch="step6-fail", note=difficult_note),
        "step6-exception": ex("LLM 异常兜底测试", "用于观察 LLM 异常时是否进入 Step7。", expected_branch="step6-exception", note="该分支通常需要人为制造 LLM 异常，普通示例不一定能命中。"),
        "step7-contact": ex("对接人兜底测试", "信息严重不足，优先兜底给项目对接人。", dispatch_hint="severe", contact=secondary_id or primary_id, expected_branch="step7-contact"),
        "step7-project-pm": ex("项目经理兜底测试", "信息严重不足且需要按本单项目经理兜底。", dispatch_hint="severe", expected_branch="step7-project-pm", note=difficult_note),
        "step7-config-pm": ex("配置项目经理兜底测试", "信息严重不足且本单没有项目经理时走全局配置兜底。", dispatch_hint="severe", expected_branch="step7-config-pm", note=difficult_note),
        "step7-unassignable": ex("无法指派测试", "信息严重不足，且没有对接人、项目经理或全局兜底配置。", dispatch_hint="severe", expected_branch="step7-unassignable", note=difficult_note),
    }


def _complete(engineer: Any) -> bool:
    return bool(
        getattr(engineer, "id", None)
        and getattr(engineer, "name", None)
        and getattr(engineer, "department", None)
        and getattr(engineer, "job_level", 0)
        and getattr(engineer, "responsibility_modules", None)
    )


def _first_product_module(engineer: Any) -> tuple[str, str]:
    modules = getattr(engineer, "responsibility_modules", None) or {}
    if not isinstance(modules, dict) or not modules:
        return "", ""
    product = next((str(k) for k in modules.keys() if k), "")
    by_iface = modules.get(product) or {}
    if isinstance(by_iface, list):
        module = next((str(x) for x in by_iface if x), "")
        return product, module
    if isinstance(by_iface, dict):
        for funcs in by_iface.values():
            values = funcs if isinstance(funcs, list) else [funcs]
            module = next((str(x) for x in values if x), "")
            if module:
                return product, module
    return product, ""
