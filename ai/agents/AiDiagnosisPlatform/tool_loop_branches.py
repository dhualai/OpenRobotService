"""工具循环分支（阶段1/2 基建，从 pipeline.py 平移，0903 拆分）。

- ticket_tool_loop_branch：提单轮工具循环（AI_TICKET_TOOL_LOOP=1）
- diagnosis_tool_loop_branch：诊断轮工具循环（search_kb + submit_ticket
  并列，AI_DIAGNOSIS_TOOL_LOOP=1）

两个开关生产均未启用；pipeline 里保留薄转发，调用点零改动。
pipe 参数 = AiDiagnosisPlatformAgent 实例（依赖 _llm_client/_memory_manager/
_get_user_projects/_format_conversation/_retrieve_inner/_match_project_choice/
_choice_supported_by_amb/_build_ticket/_finalize_diagnosis）。
"""

import asyncio
import json
import logging
import os
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


async def ticket_tool_loop_branch(pipe, request, state, memory):
    """提单轮走 submit_ticket 工具循环（替代旧快路径 prompt + 状态机）。

    流程：
      意图判 ticket → 构造 messages（system + 对话历史 + 本轮用户消息）
      → run_tool_loop（LLM 调工具 ↔ 执行器回结果，最多 5 轮）
      → 工具 terminate（草稿就绪）→ 发 review 事件弹窗（复用现有前端链路）
      → 未 terminate（还在收集/LLM 正常回答）→ 流式输出最终文本

    工具循环期间不走旧状态机：不设 ticket_collecting/required_fields，
    不 backfill、不 decide。LLM 靠工具返回值自己组织追问。
    """
    from ai.agents.AiDiagnosisPlatform.ticket_tool import (
        TOOL_SCHEMA, TOOL_SCHEMA_SUPPLEMENT, execute_submit_ticket,
    )
    from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop

    yield {"event": "status", "data": {"stage": "analyzing", "round": state.diagnosis_rounds}}
    t0 = time.perf_counter()

    # 草稿是否已存在（本轮是补充/修改轮，而非首次提单）——决定用哪套工具
    # schema、要不要把跨轮已收集字段合并进本轮判缺。
    _supplement = bool(memory.metadata.get("ticket_draft"))

    # 项目预填数据源：该用户名下项目列表。拉不到/为空 → 不注入 prompt，
    # 后续行为与旧版完全一致（弹窗搜索选择）。
    _user_projects = await pipe._get_user_projects(request.created_by)

    # 构造 messages：system + 本轮用户消息 + 结构化提单状态。
    # ⚠️ 不能只依赖对话文本：turns buffer 截断（max_turns=10）后
    # turns[context_start:] 可能为空/只剩最近1-2轮，第二次提单的问题描述
    # 会被截没（日志实锤：LLM 说「历史对话是空的」重新问发生了什么）。
    # state 里的 problem_summary/collected_info 是跨轮持久的结构化事实，
    # 显式注入，保证续接轮 LLM 永远知道当前提单上下文。
    _conv = pipe._format_conversation(
        memory, from_turn=max(0, min(state.context_start, len(memory.turns) - 2)),
        max_turns=6)
    _state_block = []
    if state.problem_summary:
        _state_block.append(f"当前提单问题：{state.problem_summary}")
    if state.ticket_type:
        _state_block.append(f"工单类型：{state.ticket_type}")
    if state.collected_info:
        _ci = {k: v for k, v in state.collected_info.items() if v}
        if _ci:
            _state_block.append(f"已收集信息：{json.dumps(_ci, ensure_ascii=False)}")
    _state_text = "\n".join(_state_block) if _state_block else "（无）"
    system_prompt = (
        "你是「摇人吧」微信服务号的 AI 诊断助手 U老师，面向 AGV/AMR 行业。\n"
        "用户表达提单诉求（转工单/提单/派单/找工程师处理）时，调用 submit_ticket 工具。\n"
        "工具会返回还缺哪些信息：缺信息时用自然语气追问用户（一次只问一个，"
        "追问要短，一句话说清还缺什么即可，不要重复已问过的内容），"
        "拿到后再调用工具。工具返回草稿后流程即结束，收尾话术由系统统一发送。\n"
        "调用 submit_ticket 时不要输出过渡语：草稿结果由系统以弹窗统一展示，"
        "对话气泡里不需要预告或交代正在做什么，直接发起工具调用即可。\n"
        "过渡语红线：禁止出现「已提交」「工单已生成」「工单已创建」等完成时表述"
        "（草稿经用户确认前都不算提交）；不要播报项目预填情况"
        "（预填由系统校验后统一告知用户）。\n"
        "收尾铁律：\n"
        "- 用户明确说某个信息没有/不知道/不方便提供（如「没有日志」「不知道版本」），"
        "或说「直接提单」「就这些信息」「尽快提单」时，把该字段按「没有」写入"
        "collected_fields 后调用工具，绝不要反复追问同一项。\n"
        "- 不要每轮新增一项可有可无的信息：已有问题概述、设备/型号、现象、时间、"
        "频率、触发场景等足以让工程师初判时，直接调用工具生成草稿。\n"
        "- 追问次数最多 2-3 次：第 3 次调用工具时必须把缺项按「没有」填上并完成提单。\n"
        "注意：\n"
        "- 不要问项目名称（项目由用户在确认弹窗里选择）\n"
        "- 用户只是咨询问题（没提提单）时不要调用工具，正常回答即可\n"
        "- 用户明确表示不想提单/取消（如「算了」「不用了」「不想提单了」）时，"
        "不要调用工具，简短回复「好的，不转工单。有什么其他问题随时问我。」\n"
        "- 用户是在给已生成的草稿补充信息（如「提给XX」「补充一下XX」「再加上XX」）时，"
        "调用 submit_ticket 并带上补充的内容；不要当成新问题重新提单。"
        "补充信息这一轮就调用工具（不要先回一句「好的我记录」而不调，"
        "那样会被当作中途放弃）。\n"
        f"- 当前提单上下文（如果非空，说明用户已在提单流程中，不要当成新会话重新问问题）：\n{_state_text}\n"
    )
    if _user_projects:
        _proj_lines = "\n".join(
            f"- {p['name']}（编号: {p['code']}）" for p in _user_projects)
        system_prompt += (
            f"\n用户名下项目列表（仅这些可选）：\n{_proj_lines}\n"
            "项目预填规则：用户在对话中明确提到要给其中某个项目提单时，"
            "把该项目名称从上面列表**原样照抄**进 submit_ticket 的 project_choice "
            "参数；没提到或对不上就省略该参数。绝不向用户追问项目名称、不主动推荐项目。\n"
            "不要在对话中播报项目预填情况——预填结果由系统校验后在草稿生成时统一"
            "告知用户，弹窗中也会展示。\n"
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"以下是最近对话（供参考）：\n{_conv}\n\n本轮用户消息：{request.query}"},
    ]
    logger.info(f"[tool_loop] 提单工具循环 prompt: system={len(system_prompt)} "
                f"user={len(messages[1]['content'])} "
                f"合计={len(system_prompt) + len(messages[1]['content'])} chars: "
                f"session={request.session_id}")

    # 执行器包装：draft 生成走 _build_ticket（复用 LLM 总结 title/description 的链路，
    # 不再是最小草稿——否则 title==description，弹窗体验差）。
    # 每轮都把跨轮累计的 state.collected_info 合并进本轮 collected_fields 再判缺——
    # 模型每轮通常只传新增字段，不合并会把前几轮已收齐的字段重复判成缺失，
    # 导致重复追问甚至无限扩展追问项（deepseek 实测：报错日志/版本号 连问 7 轮）。
    # 合并后判缺逻辑对首轮/中间收集轮/补充轮统一，不用区分对待。
    async def _executor(params):
        merged = dict(state.collected_info)
        merged.update({k: v for k, v in (params.get("collected_fields") or {}).items() if v})
        params = {**params, "collected_fields": merged}
        return execute_submit_ticket(params, make_draft=None)

    final_text = ""
    tool_results = []
    final_streamed = False  # 循环内已把 final_text 逐 token 流式发出 → 结束时不得整段重发
    # 循环内实际流出的正文累计：terminate 轮（草稿就绪）done 事件 final_text
    # 恒为空，但过渡语可能已流式说出——收尾据此判断「用户已经看到什么」，
    # 已流出的只补尾巴，绝不整段重发（重发 = 气泡里同一段话出现两遍）。
    _streamed_text = ""
    from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop_stream
    _schema = TOOL_SCHEMA_SUPPLEMENT if _supplement else TOOL_SCHEMA
    _loop_failed = False

    async def _loop_events():
        """跑一遍工具循环：token 随到随发；done 结果经 nonlocal 写回外层。"""
        nonlocal final_text, tool_results, final_streamed, _streamed_text
        async with asyncio.timeout(60.0):
            async for ev in run_tool_loop_stream(
                    pipe._llm_client, messages, [_schema],
                    {"submit_ticket": _executor},
                    # 提单收集是轻量结构化任务，关闭思考可把首 token 等待
                    # 从 5-15s 砍到 1-2s（DeepSeek 与中转站 Claude 均生效）。
                    thinking=False):
                if ev["event"] == "token":
                    _streamed_text += ev.get("data") or ""
                    yield ev
                elif ev["event"] == "done":
                    final_text = ev["final_text"]
                    tool_results = ev["tool_results"]
                    # final_text 非空才表示正文已在循环内流式发出；
                    # terminate 路径 final_text=""（收尾话术走兜底文案，未流式）
                    final_streamed = bool(final_text)

    try:
        async for ev in _loop_events():
            yield ev
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"[tool_loop] 工具循环失败: session={request.session_id}, err={e}")
        yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)[:100]}}
        final_text = "提单过程中出现异常，请稍后重试或联系管理员。"
        tool_results = []
        _loop_failed = True

    # 空转纠偏（生产实录 14:46 实锤）：触发轮 LLM 只说了过渡语
    # 「好的，我帮您转工单，我看一下还需要补充哪些信息：」就结束回合，
    # 0 次工具调用——气泡停在冒号上死寂，用户在对话里无法继续提单。
    # 机制层兜底：把空转回合和纠偏指令追加进 messages 重跑一遍循环，
    # 让模型要么调工具、要么直接追问。判断仍在 LLM：代码只发现协议
    # 违约（本轮没调工具）并要求重做，不猜用户意图；放弃轮由 LLM 自己
    # 的固定话术「不转工单」识别（沿用既有协议，与下方 _is_abandon 同源）。
    if (not tool_results and not _loop_failed
            and "不转工单" not in (final_text or "")):
        logger.warning(f"[tool_loop] 本轮零工具调用（疑似空转），注入纠偏重跑: "
                       f"final_text={(final_text or '')[:50]!r}, session={request.session_id}")
        messages.append({"role": "assistant", "content": final_text or ""})
        messages.append({
            "role": "system",
            "content": "你上一条回复只说了话，没有调用 submit_ticket，用户正在等下文。"
                       "用户已经看到你上一条回复——不要再输出过渡语、不要复述任何"
                       "已说过的话（用户会看到两遍）。现在必须实际行动，二选一：\n"
                       "1. 立刻调用 submit_ticket，正文留空（已掌握的信息放进 "
                       "collected_fields，还缺的留给 required_fields）；\n"
                       "2. 直接向用户追问一个还缺的关键信息（完整问句，以问号结尾，"
                       "前面不要加任何过渡语）。\n"
                       "若用户其实没有提单诉求，就正常回答用户的问题。"
                       "绝不能再次只说一句话就结束回合。",
        })
        try:
            async for ev in _loop_events():
                yield ev
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[tool_loop] 纠偏轮也失败: {e}, session={request.session_id}")

    t_loop = round((time.perf_counter() - t0) * 1000)
    logger.info(f"[tool_loop] 循环完成: session={request.session_id}, "
                f"elapsed={t_loop}ms, tool_calls={len(tool_results)}, "
                f"final_text_len={len(final_text)}")
    # _check_required_fields/_save_agent_state 函数内 import：这两个符号在
    # pipeline 模块级，顶部 import 会成环（pipeline 顶部 import 本模块）
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        _check_required_fields, _save_agent_state,
    )
    draft = None
    _prefill_project: Optional[Dict[str, str]] = None
    _draft_ready = any(
        r.get("details", {}).get("status") == "draft_ready" for r in tool_results)
    if _draft_ready:
        # 把工具参数里的信息写入 state，供 _build_ticket 和按钮路径复用
        for r in tool_results:
            if r.get("name") != "submit_ticket" or not r.get("arguments"):
                continue
            args = r["arguments"]
            state.ticket_type = args.get("ticket_type") or state.ticket_type
            state.problem_summary = args.get("problem_summary") or state.problem_summary
            for k, v in (args.get("collected_fields") or {}).items():
                if v and not state.collected_info.get(k):
                    state.collected_info[k] = v
            if args.get("requested_assignee"):
                state.collected_info["requested_assignee"] = args["requested_assignee"]
            # 项目预填：LLM 照抄的列表项做严格校验（防幻觉），命中才进 draft。
            # 不写 state/collected_info、不参与判缺——单向管道：工具参数 →
            # draft → 弹窗（用户可改），confirm_submit 用 overrides 覆盖优先。
            _prefill_project = pipe._match_project_choice(
                args.get("project_choice", ""), _user_projects)
            if args.get("project_choice") and not _prefill_project:
                logger.info(
                    f"[tool_loop] project_choice 未命中用户项目列表，忽略: "
                    f"{args.get('project_choice')!r}, session={request.session_id}")
            # 歧义挂起期间的臆断防线（0829）：与主协议快路径同规则
            if _prefill_project and not pipe._choice_supported_by_amb(
                    request.query, _prefill_project,
                    state.ambiguous_project_candidates):
                logger.warning(
                    f"[tool_loop] 歧义挂起期间 project_choice 无原话支撑，"
                    f"拒收臆断: {args.get('project_choice')!r}")
                _prefill_project = None
            # 首次生成草稿（非补充）时才信任本轮声明——那一轮是真实校验过的。
            # 补充轮不覆盖：覆盖后会让 confirm_submit 的 _assess_ticket_readiness
            # 重新校验出偏差。
            if not _supplement:
                rf = args.get("required_fields") or {}
                if rf and isinstance(rf, dict):
                    state.required_fields = dict(rf)
            break
        try:
            draft = await pipe._build_ticket(request.session_id, state, memory,
                                             prefill_project=_prefill_project)
        except Exception as e:
            logger.warning(f"[tool_loop] _build_ticket 失败: {e}")
            draft = None

    if draft is not None:
        draft["ticket_seq"] = state.ticket_seq + 1
        check = _check_required_fields(draft)
        draft["missing_fields"] = check["missing"]
        memory.metadata["ticket_draft"] = draft
        state.ticket_collecting = []
        state.tool_loop_active = False  # 草稿就绪，退出工具循环收集
        _save_agent_state(memory, state)
        await pipe._memory_manager.save_memory(memory)
        logger.info(f"[tool_loop] 草稿就绪，发 review 弹窗: session={request.session_id}")
        yield {"event": "status", "data": {
            "stage": "review",
            "draft": draft,
            "missing_fields": check["missing"],
            "force_submit": False,
        }}
        # 草稿结果已通过弹窗/工单卡片完整展示，对话气泡不再回填播报话术，
        # 避免与弹窗和工具调用前的过渡语重复。
        _msg = "工单草稿已生成，请在弹窗中核对。"
        result_data = await pipe._finalize_diagnosis(
            request.session_id, state,
            thinking="", action="answer", message=_msg, streaming=True)
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}
        yield {"event": "result", "data": result_data}
        return

    # 未生成草稿，分两种情况：
    # ① LLM 调了工具但缺字段 → 还在收集，标记粘性续接
    # ② LLM 没调工具（tool_calls=0）→ 需区分「补充回话」与「显式放弃」：
    #    - 补充回话（如「好的，我来记录」「还差XX」）：LLM 先回一句不调工具，
    #      下一轮才调 submit_ticket。若一律判取消 → 清草稿 + 写 cancelled →
    #      用户补充信息被拦（实测把上午的补充逻辑弄坏）。此时保留状态。
    #    - 显式放弃：system prompt 让 LLM 在用户说「算了/不转工单」时输出固定
    #      话术「好的，不转工单…」且不调工具。识别 LLM 自己的结论（非服务端
    #      关键词抢判断），命中才销毁 + 写 cancelled 标记。
    if not tool_results:
        _abandon_text = final_text or ""
        _is_abandon = "不转工单" in _abandon_text
        if _is_abandon:
            logger.info(f"[tool_loop] LLM 判用户显式放弃提单，清空状态: session={request.session_id}")
            # 取消标记写入 last_submitted_ticket：让 _can_submit 拦截「放弃后立刻
            # 再点按钮」。仅当无已有记录时写入；清空 problem_summary 前记录 topic。
            if not state.last_submitted_ticket:
                state.last_submitted_ticket = {
                    "ticket_id": "cancelled",
                    "title": "取消的草稿",
                    "topic": state.problem_summary or "",
                    "submitted_at": int(time.time()),
                }
                state.user_spoke_after_submit = False  # 取消即重新武装闸门
            state.tool_loop_active = False
            state.collected_info = {}
            state.problem_summary = ""
            state.ticket_type = ""
            state.ticket_collecting = []
            state.required_fields = None
            state.collect_rounds = 0
            state.field_ask_rounds = {}
            state.ticket_ref_context = ""
            memory.metadata.pop("ticket_draft", None)
        else:
            logger.info(f"[tool_loop] 本轮无工具调用（补充/回话），保留收集状态: session={request.session_id}")
            # 空转/回话轮仍在提单流程中：置粘性续接，下一轮直接回工具循环。
            # 生产实录：空转轮不置位，用户补完 4 个字段后被意图分类掉进
            # 旧状态机（tool_loop_active 粘性续接正是为短句回答误判
            # diagnosis 设计的，见 _agent_think_stream 的续接入口）。
            # 草稿已存在时不置——草稿轮的设计就是由意图分类路由补充/取消
            # （草稿就绪时已显式置 False）。
            if not memory.metadata.get("ticket_draft"):
                state.tool_loop_active = True
        _save_agent_state(memory, state)
        await pipe._memory_manager.save_memory(memory)
        if final_text and not final_streamed:
            yield {"event": "token", "data": final_text}
        result_data = await pipe._finalize_diagnosis(
            request.session_id, state,
            thinking="", action="answer", message=final_text or "好的，有需要随时找我。",
            streaming=True)
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}
        yield {"event": "result", "data": result_data}
        return

    # ① 收集轮：渐进写回工具参数（含 requested_assignee），
    # 否则下一轮 draft_ready 时 LLM 不再重复带 assignee，_build_ticket 总结
    # 出来的描述里就丢了「提给贾爽」。
    for r in tool_results:
        if r.get("name") != "submit_ticket" or not r.get("arguments"):
            continue
        args = r["arguments"]
        if args.get("ticket_type"):
            state.ticket_type = args["ticket_type"]
        if args.get("problem_summary"):
            state.problem_summary = args["problem_summary"]
        for k, v in (args.get("collected_fields") or {}).items():
            if v and not state.collected_info.get(k):
                state.collected_info[k] = v
        if args.get("requested_assignee"):
            state.collected_info["requested_assignee"] = args["requested_assignee"]
    state.tool_loop_active = True
    _save_agent_state(memory, state)
    await pipe._memory_manager.save_memory(memory)
    if final_text and not final_streamed:
        yield {"event": "token", "data": final_text}
    result_data = await pipe._finalize_diagnosis(
        request.session_id, state,
        thinking="", action="answer", message=final_text or "请稍后重试。",
        streaming=True)
    if result_data.get("title"):
        yield {"event": "title", "data": {"title": result_data["title"]}}
    yield {"event": "result", "data": result_data}


async def diagnosis_tool_loop_branch(pipe, request, state, memory):
    """诊断轮走工具循环：LLM 可调 search_kb（查知识库）+ submit_ticket（提单）。

    与旧诊断 prompt 路径的区别：
    - 不再服务端强制检索：LLM 自己决定查不查、查什么、查几次
    - 查完知识库可以继续追问、回答、或顺势提单（提交工单工具也在）
    - thinking 默认开启（诊断需要深度推理）；AI_DIAGNOSIS_THINKING=0 时关闭
      （提速 A/B 开关：中转站慢时每轮可省数秒，质量略降）

    无工具调用（纯回答/闲聊）→ 直接输出 LLM 回复。
    """
    from ai.agents.AiDiagnosisPlatform.search_tool import SEARCH_KB_SCHEMA, make_search_result, make_search_error
    from ai.agents.AiDiagnosisPlatform.ticket_tool import TOOL_SCHEMA, execute_submit_ticket
    from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop

    yield {"event": "status", "data": {"stage": "analyzing", "round": state.diagnosis_rounds}}
    t0 = time.perf_counter()

    # 项目预填数据源：该用户名下项目列表（拉不到/为空 → 不注入，行为同旧版）
    _user_projects = await pipe._get_user_projects(request.created_by)

    # 构造 messages：system + 最近对话 + 本轮用户消息
    _conv = pipe._format_conversation(
        memory, from_turn=state.context_start, max_turns=8)
    system_prompt = (
        "你是「摇人吧」微信服务号的 AI 诊断助手 U老师，面向 AGV/AMR 行业，"
        "像一位经验丰富的现场工程师在微信上帮用户解决问题。\n"
        "你有两个工具：\n"
        "1. search_kb：检索知识库（操作手册/FAQ/排查手册/错误码）。"
        "回答操作步骤、错误码含义、故障排查等问题前，先查知识库；"
        "检索结果不相关就换关键词再查；多次查不到就如实说手册未覆盖，不要编造。\n"
        "2. submit_ticket：用户表达提单诉求（转工单/提单/派单）时调用。\n"
        "语气与风格：\n"
        "- 语气自然、口语化，先一句话回应问题本身，再给具体内容，不要公文腔\n"
        "- 步骤要具体可执行：说清在哪个页面、点什么、填什么\n"
        "- 禁止开发内部术语：不要出现 commit、函数名、参数名、模块名、分支、回滚等词\n"
        "- 结尾自然收尾，不要每条回复都以「建议转工单」结尾\n"
        "规则：\n"
        "- 不要问项目名称（项目由用户在确认弹窗里选择）\n"
        "- 用户可以一边咨询一边提单：先查知识库回答，用户不满意要提单时再调 submit_ticket\n"
        "- 查知识库后要基于检索内容回答，禁止编造步骤；"
        "检索内容必须真的包含问题所问的定义/步骤/参数才能作答，"
        "话题沾边但没给出所问内容时如实说手册没写，不要推测编造；"
        "界面位置同理——指引用户去哪个页面/菜单查看或操作时，该位置必须出自检索原文，"
        "检索内容说的是无界面的后台模块时不能指引用户去任何页面查看，"
        "资料没写位置就直说没有，禁止推测页面路径\n"
        "- 开场不要复述用户问题里已知的前提，直接进入步骤或关键区分\n"
        "- 知识库对不同的角色/身份/前提给出不同步骤时（如「USP研发」「实施」、"
        "自研车/第三方车），必须按原文的角色名称分开列出各自的完整步骤，"
        "禁止合并成一套步骤，禁止改写角色名称\n"
        "- 知识库对同一功能给出多种模式/方案时（如车随梯/车不随梯），"
        "分别列出各模式的要点并说明差异，不要只给一套通用步骤\n"
        "- 用户省略式追问（「然后呢」「第一步好了」）时，"
        "承接最近对话的进度继续讲下一步，不要当成全新问题、不要说未收录\n"
        "- 知识库内容中的 ![](url) 是操作界面截图：与当前问题直接相关的截图，"
        "用 ![说明](url) 引用到对应步骤下面；介绍产品/车型时知识库有图必须引用，不要省略\n"
        "- 进入提单收集后，已收集的信息不得重复追问；不要每轮新增一项可有可无的信息。"
        "已有问题概述、设备型号、现象、期望效果、版本、站点等足以让工程师初判时，"
        "应调用 submit_ticket 生成草稿，不要继续追问。\n"
        "- 用户明确说某个信息没有/不知道/不方便提供，或说「直接提单」「就这些信息」时，"
        "把该字段按「没有」写入 collected_fields 后调用 submit_ticket，"
        "绝不要反复追问同一项；追问最多 2-3 次就必须完成提单。\n"
        "- 调用 submit_ticket 时不要输出过渡语：草稿结果由系统以弹窗统一展示，"
        "对话气泡里不需要预告或交代，直接发起工具调用即可；"
        "禁止说「已提交」「工单已生成」等完成时话术，不要播报项目预填情况。\n"
        f"当前上下文（非空说明用户在提单流程中）：问题={state.problem_summary or '无'}，"
        f"已收集={json.dumps(state.collected_info, ensure_ascii=False) if state.collected_info else '无'}\n"
    )
    if _user_projects:
        _proj_lines = "\n".join(
            f"- {p['name']}（编号: {p['code']}）" for p in _user_projects)
        system_prompt += (
            f"\n用户名下项目列表（仅这些可选）：\n{_proj_lines}\n"
            "项目预填规则：用户在对话中明确提到要给其中某个项目提单时，"
            "把该项目名称从上面列表**原样照抄**进 submit_ticket 的 project_choice "
            "参数；没提到或对不上就省略该参数。绝不向用户追问项目名称、不主动推荐项目。\n"
            "不要在对话中播报项目预填情况——预填结果由系统校验后在草稿生成时统一"
            "告知用户，弹窗中也会展示。\n"
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"以下是最近对话（供参考）：\n{_conv}\n\n本轮用户消息：{request.query}"},
    ]
    logger.info(f"[diag_tool] 诊断工具循环 prompt: system={len(system_prompt)} "
                f"user={len(messages[1]['content'])} "
                f"合计={len(system_prompt) + len(messages[1]['content'])} chars: "
                f"session={request.session_id}")

    # search_kb 执行器：调现有检索服务
    async def _search_executor(params):
        try:
            query = (params.get("query") or "").strip()
            if not query:
                return make_search_error("query 为空")
            result_text = await asyncio.wait_for(
                pipe._retrieve_inner(request.session_id, state,
                                     query_override=query),
                timeout=20.0,
            )
            return make_search_result(result_text)
        except Exception as e:
            logger.warning(f"[diag_tool] search_kb 失败: {e}")
            return make_search_error(str(e))

    # 诊断后转提单时，本轮模型通常只会传新增字段。必须合并状态中
    # 已收集的信息，否则 execute_submit_ticket 会把历史答案误判为缺失，
    # 导致重复追问甚至无限扩展追问项。
    async def _ticket_executor(params):
        merged = dict(state.collected_info)
        merged.update({
            k: v for k, v in (params.get("collected_fields") or {}).items() if v
        })
        return execute_submit_ticket(
            {**params, "collected_fields": merged}, make_draft=None)

    final_text = ""
    tool_results = []
    final_streamed = False  # 循环内已把 final_text 逐 token 流式发出 → 结束时不得整段重发
    # 循环内实际流出的正文累计：terminate 轮（草稿就绪）done 事件 final_text
    # 恒为空，但过渡语可能已流式说出——收尾据此判断「用户已经看到什么」，
    # 已流出的只补尾巴，绝不整段重发（重发 = 气泡里同一段话出现两遍）。
    _streamed_text = ""
    try:
        from ai.agents.AiDiagnosisPlatform.tool_loop import run_tool_loop_stream
        async with asyncio.timeout(90.0):
            async for ev in run_tool_loop_stream(
                    pipe._llm_client, messages,
                    [SEARCH_KB_SCHEMA, TOOL_SCHEMA],
                    {"search_kb": _search_executor, "submit_ticket": _ticket_executor},
                    # 诊断默认开思考（深度推理）；AI_DIAGNOSIS_THINKING=0 关闭提速。
                    thinking=None if os.getenv("AI_DIAGNOSIS_THINKING", "1") == "1" else False,
            ):
                if ev["event"] == "token":
                    _streamed_text += ev.get("data") or ""
                    yield ev
                elif ev["event"] == "done":
                    final_text = ev["final_text"]
                    tool_results = ev["tool_results"]
                    # final_text 非空才表示正文已在循环内流式发出；
                    # terminate 路径 final_text=""（收尾话术走兜底文案，未流式）
                    final_streamed = bool(final_text)
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"[diag_tool] 诊断工具循环失败: session={request.session_id}, err={e}")
        yield {"event": "status", "data": {"stage": "submit_failed", "error": str(e)[:100]}}
        final_text = "诊断过程中出现异常，请稍后重试或联系管理员。"
        tool_results = []

    t_loop = round((time.perf_counter() - t0) * 1000)
    logger.info(f"[diag_tool] 循环完成: session={request.session_id}, "
                f"elapsed={t_loop}ms, tool_calls={len(tool_results)}, "
                f"final_text_len={len(final_text)}")

    from ai.agents.AiDiagnosisPlatform.pipeline import (
        _check_required_fields, _save_agent_state,
    )
    # 提单工具被调用了 → 复用提单分支的草稿处理逻辑
    draft = None
    _prefill_project: Optional[Dict[str, str]] = None
    _draft_ready = any(
        r.get("details", {}).get("status") == "draft_ready" for r in tool_results)
    if _draft_ready:
        for r in tool_results:
            if r.get("name") != "submit_ticket" or not r.get("arguments"):
                continue
            args = r["arguments"]
            state.ticket_type = args.get("ticket_type") or state.ticket_type
            state.problem_summary = args.get("problem_summary") or state.problem_summary
            for k, v in (args.get("collected_fields") or {}).items():
                if v and not state.collected_info.get(k):
                    state.collected_info[k] = v
            if args.get("requested_assignee"):
                state.collected_info["requested_assignee"] = args["requested_assignee"]
            # 项目预填：同 ticket_tool_loop_branch——严格校验后进 draft，
            # 不写 state/collected_info、不参与判缺，弹窗仍可改。
            _prefill_project = pipe._match_project_choice(
                args.get("project_choice", ""), _user_projects)
            if args.get("project_choice") and not _prefill_project:
                logger.info(
                    f"[diag_tool] project_choice 未命中用户项目列表，忽略: "
                    f"{args.get('project_choice')!r}, session={request.session_id}")
            # 歧义挂起期间的臆断防线（0829）：与主协议快路径同规则
            if _prefill_project and not pipe._choice_supported_by_amb(
                    request.query, _prefill_project,
                    state.ambiguous_project_candidates):
                logger.warning(
                    f"[diag_tool] 歧义挂起期间 project_choice 无原话支撑，"
                    f"拒收臆断: {args.get('project_choice')!r}")
                _prefill_project = None
            rf = args.get("required_fields") or {}
            if rf and isinstance(rf, dict):
                state.required_fields = dict(rf)
            break
        try:
            draft = await pipe._build_ticket(request.session_id, state, memory,
                                             prefill_project=_prefill_project)
        except Exception as e:
            logger.warning(f"[diag_tool] _build_ticket 失败: {e}")
            draft = None

    if draft is not None:
        draft["ticket_seq"] = state.ticket_seq + 1
        check = _check_required_fields(draft)
        draft["missing_fields"] = check["missing"]
        memory.metadata["ticket_draft"] = draft
        state.ticket_collecting = []
        state.tool_loop_active = False
        _save_agent_state(memory, state)
        await pipe._memory_manager.save_memory(memory)
        yield {"event": "status", "data": {
            "stage": "review",
            "draft": draft,
            "missing_fields": check["missing"],
            "force_submit": False,
        }}
        # 草稿结果已通过弹窗/工单卡片完整展示，对话气泡不再回填播报话术。
        _msg = "工单草稿已生成，请在弹窗中核对。"
        result_data = await pipe._finalize_diagnosis(
            request.session_id, state,
            thinking="", action="answer", message=_msg, streaming=True)
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}
        yield {"event": "result", "data": result_data}
        return

    # submit_ticket 被调了但字段不齐（collecting）→ 标记粘性续接，否则下一轮
    # 会重新走意图分类/检索，把刚收集到的信息全部忘掉（日志实锤：诊断循环里
    # 提单收集中途，下一轮被误判回 diagnosis，重新查知识库，提单不了了之）。
    _submit_called = any(r.get("name") == "submit_ticket" for r in tool_results)
    if _submit_called:
        for r in tool_results:
            if r.get("name") != "submit_ticket" or not r.get("arguments"):
                continue
            args = r["arguments"]
            if args.get("ticket_type"):
                state.ticket_type = args["ticket_type"]
            if args.get("problem_summary"):
                state.problem_summary = args["problem_summary"]
            for k, v in (args.get("collected_fields") or {}).items():
                if v and not state.collected_info.get(k):
                    state.collected_info[k] = v
            if args.get("requested_assignee"):
                state.collected_info["requested_assignee"] = args["requested_assignee"]
        state.tool_loop_active = True
        _save_agent_state(memory, state)
        await pipe._memory_manager.save_memory(memory)
        if final_text and not final_streamed:
            yield {"event": "token", "data": final_text}
        result_data = await pipe._finalize_diagnosis(
            request.session_id, state,
            thinking="", action="answer", message=final_text or "请稍后重试。",
            streaming=True)
        if result_data.get("title"):
            yield {"event": "title", "data": {"title": result_data["title"]}}
        yield {"event": "result", "data": result_data}
        return

    # 未提单：纯诊断回答（可能查过知识库）
    if final_text and not final_streamed:
        yield {"event": "token", "data": final_text}
    result_data = await pipe._finalize_diagnosis(
        request.session_id, state,
        thinking="", action="answer", message=final_text or "请稍后重试。",
        streaming=True)
    if result_data.get("title"):
        yield {"event": "title", "data": {"title": result_data["title"]}}
    yield {"event": "result", "data": result_data}
