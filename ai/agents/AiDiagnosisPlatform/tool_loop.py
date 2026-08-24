"""诊断工具循环运行器。

普通诊断路径只向模型暴露 ``search_kb``：LLM 可自主决定是否检索，
工具结果回填后继续生成回答。提单由 pipeline 的固定字段状态机负责，
本模块不负责提单意图、字段收集或草稿生成。

参考 Pi Agent 的 agent-loop 内循环：
  LLM 调用（带 tools）→ 有 tool_calls 就执行 → 结果回填 messages → 再调 LLM
  → 直到 LLM 输出纯文本（或达到安全上限）。
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 工具循环安全上限：防止 LLM 无限调工具。
# 5 → 8：诊断循环需要「查资料→发现不相关→换角度再查→交叉验证」的深层推理，
# 5 轮限制会截断合法的多步检索链。8 轮足够容纳 3-4 次检索 + 追问 + 提单。
_MAX_ITERATIONS = 8


async def run_tool_loop_stream(
    llm,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    tool_executors: Dict[str, Any],
    max_iterations: int = _MAX_ITERATIONS,
    thinking: Optional[bool] = None,
):
    """工具循环的流式版本：yield {"event": "token", "data": "..."} 供前端实时渲染。

    内部逻辑与 run_tool_loop 完全一致，唯一区别是 LLM 的正文 token 直接
    yield 出去（工具调用轮无正文，不 yield）。最终用 StopAsyncIteration
    前 yield 一个 {"event": "done", "final_text": ..., "tool_results": [...]}。

    thinking: 透传给 LLM 的思考开关。提单收集等结构化任务传 False 可大幅
    降低首 token 等待（DeepSeek/中转站 Claude 均生效），诊断检索任务保持
    None（走 LLM 默认，深度推理）。

    调用方式：
      async for ev in run_tool_loop_stream(...):
          if ev["event"] == "token": 转发前端
          elif ev["event"] == "done": 取 final_text / tool_results
    """
    results: List[Dict[str, Any]] = []
    _empty_search_streak = 0
    _search_call_count = 0

    # 消费器：外层把 run_tool_loop_stream 产出的 token 直接 yield 给前端。
    # 注意：token 不能等整轮结束再统一发——工具调用轮正文为空，但一轮包含
    # thinking + 工具执行，耗时 5-8 秒；若等轮结束才发，前端表现为「卡几秒
    # 突然一坨」（非流式）。这里 LLM 每产出一个 token 就立即转发。
    async def _llm_round():
        """一轮 LLM 调用：yield token 事件（随到随发），最后 yield
        {"_final": True, "content": ..., "tool_calls": ...} 哨兵事件。

        async generator 不能带值 return（语法非法），所以用哨兵事件
        传回 (content, tool_calls)。

        流式铁律：token 到一批就 yield 一批，**绝不攒到列表等整轮流结束再回放**。
        攒缓冲 = 前端卡几秒后突然一坨（实测用户反馈「非流式」「先出一个字再卡」）。
        工具调用轮的正文（过渡语）也会显示——无法预知本轮是否调工具，
        抑制只能靠缓冲（破坏流式），所以过渡语照常流式，重复由工具结果的
        防复述提示（[不要复述] 注入）在下一轮消除。
        """
        stream_fn = getattr(llm, "stream_with_tools", None)
        if stream_fn is not None and callable(stream_fn):
            final_ev = None
            async for ev in stream_fn(messages=messages, tools=tools,
                                      max_tokens=1200, temperature=0.2,
                                      thinking=thinking):
                if ev.get("type") == "token" and ev.get("content"):
                    yield {"event": "token", "data": ev["content"]}
                else:
                    final_ev = ev
            if final_ev is None:
                yield {"_final": True, "content": "", "tool_calls": [],
                       "reasoning": ""}
                return
            yield {"_final": True,
                   "content": final_ev.get("content") or "",
                   "tool_calls": final_ev.get("tool_calls") or [],
                   "reasoning": final_ev.get("reasoning_content") or ""}
            return
        resp = await llm.complete_with_tools(
            messages=messages, tools=tools, max_tokens=1200, temperature=0.2,
            thinking=thinking)
        content = resp.get("content") or ""
        if content:
            yield {"event": "token", "data": content}
        yield {"_final": True, "content": content, "tool_calls": resp.get("tool_calls") or [],
               "reasoning": resp.get("reasoning") or ""}

    for _ in range(max_iterations):
        content = ""
        tool_calls = []
        reasoning = ""
        async for ev in _llm_round():
            if ev.get("_final"):
                content = ev["content"]
                tool_calls = ev["tool_calls"]
                reasoning = ev.get("reasoning") or ""
            else:
                yield ev

        if not tool_calls:
            yield {"event": "done", "final_text": content, "tool_results": results}
            return

        _assistant_msg = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
                }
                for tc in tool_calls
            ],
        }
        # DeepSeek 协议：tools + 思考开启时，中间 assistant 的 reasoning_content
        # 必须回传给后续请求（否则官方文档声明返回 400）。空时不写字段，
        # 避免对额外字段敏感的中转后端出错。
        if reasoning:
            _assistant_msg["reasoning_content"] = reasoning
        messages.append(_assistant_msg)

        terminate = False
        unknown_tool = False
        for tc in tool_calls:
            name = tc["name"]
            executor = tool_executors.get(name)
            if executor is None:
                unknown_tool = True
                tool_result = {
                    "content": f"工具名无效：{name}。可用工具只有：{', '.join(tool_executors)}。请不要重试工具，直接用中文回答用户。",
                    "details": {"status": "error", "error": f"unknown tool {name}"},
                    "terminate": False,
                }
            else:
                try:
                    result = executor(tc["arguments"])
                    if asyncio.iscoroutine(result):
                        result = await result
                    tool_result = result
                except Exception as e:
                    logger.warning(f"[tool_loop] 工具 {name} 执行失败: {e}")
                    tool_result = {
                        "content": f"工具执行失败：{e}",
                        "details": {"status": "error", "error": str(e)},
                        "terminate": False,
                    }
            results.append({"name": name, "arguments": tc["arguments"], **tool_result})
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(tool_result["content"], ensure_ascii=False),
            })
            if tool_result.get("terminate"):
                terminate = True

            if name == "search_kb":
                _search_call_count += 1
                if "暂无匹配" in (tool_result.get("content") or ""):
                    _empty_search_streak += 1
                else:
                    _empty_search_streak = 0
            if _empty_search_streak >= 2 or _search_call_count >= 3:
                logger.warning(f"[tool_loop] 检索 {_search_call_count} 次（连续无结果 "
                               f"{_empty_search_streak} 次），移除所有工具，强制进入文本回答")
                # 仅允许诊断检索循环：达到上限后移除 search_kb，
                # 下一轮直接生成有界文本回答。
                tools = [tool for tool in tools
                         if tool.get("function", {}).get("name") != "search_kb"]
                messages.append({
                    "role": "system",
                    "content": "知识库检索已达上限（多次检索均无强相关结果，或结果相关度很低）。"
                               "不要再尝试检索。请基于已有检索结果 + 你的 AGV/AMR 领域知识给用户一个"
                               "**有界分析**：可能的通用原因和排查方向，"
                               "明确说明「这是通用分析，具体操作请以现场工程师确认为准」，"
                               "并可建议转工单。不要编造「手册里有XX步骤」这类具体指引。",
                })
                _empty_search_streak = 0

        if terminate:
            yield {"event": "done", "final_text": "", "tool_results": results}
            return

    logger.warning(f"[tool_loop] 达到最大迭代次数 {max_iterations}，强制结束")
    yield {"event": "done", "final_text": "", "tool_results": results}


async def run_tool_loop(
    llm,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    tool_executors: Dict[str, Any],
    max_iterations: int = _MAX_ITERATIONS,
    on_token=None,
    thinking: Optional[bool] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """运行工具循环。

    messages: OpenAI 格式消息历史（含 system）。
    tools: 工具 schema 列表（给 LLM 看）。
    tool_executors: {tool_name: async callable(params) -> {content, details, terminate}}。
    on_token: 可选回调 async fn(token_str)——LLM 流式输出正文时逐 token 调用
      （用于前端实时渲染；非流式路径整段调用一次）。
    thinking: 透传给 LLM 的思考开关（None=LLM 默认；结构化任务传 False 提速）。

    返回 (final_text, tool_results)：
    - final_text: 最终回复文本（LLM 在工具结束后输出的正文）
    - tool_results: 历次工具执行结果列表（含 details，供调用方取 draft 等）

    异常策略：工具执行抛错 → 回填错误消息给 LLM，让它知道并继续；LLM 调用
    失败 → 向上抛（调用方处理超时降级）。
    """
    results: List[Dict[str, Any]] = []
    _empty_search_streak = 0  # 连续「知识库暂无匹配」次数——死磕保险
    _search_call_count = 0  # 检索总次数——即便结果非空但弱相关，也强制封顶

    async def _llm_round():
        """一轮 LLM 调用：优先流式（逐 token 回调），回退非流式。

        与 run_tool_loop_stream 同一策略：token 随到随回调 on_token，绝不缓冲。
        """
        stream_fn = getattr(llm, "stream_with_tools", None)
        if stream_fn is not None and callable(stream_fn):
            final_ev = None
            async for ev in stream_fn(messages=messages, tools=tools,
                                      max_tokens=1200, temperature=0.2,
                                      thinking=thinking):
                if ev.get("type") == "token" and ev.get("content"):
                    if on_token:
                        r = on_token(ev["content"])
                        if asyncio.iscoroutine(r):
                            await r
                else:
                    final_ev = ev
            if final_ev is None:
                return "", [], ""
            return (final_ev.get("content") or "", final_ev.get("tool_calls") or [],
                    final_ev.get("reasoning_content") or "")
        # 非流式回退
        resp = await llm.complete_with_tools(
            messages=messages, tools=tools, max_tokens=1200, temperature=0.2,
            thinking=thinking)
        content = resp.get("content") or ""
        if content and on_token:
            r = on_token(content)
            if asyncio.iscoroutine(r):
                await r
        return content, resp.get("tool_calls") or [], resp.get("reasoning") or ""

    for _ in range(max_iterations):
        content, tool_calls, reasoning = await _llm_round()

        if not tool_calls:
            # LLM 不再调工具：循环结束，返回正文
            return content, results

        # 记录 assistant 消息（含 tool_calls，保持 OpenAI 对话格式）
        _assistant_msg = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
                }
                for tc in tool_calls
            ],
        }
        # DeepSeek 协议：tools + 思考开启时中间 assistant 的 reasoning_content
        # 必须回传（空时不写字段，避免对额外字段敏感的中转后端出错）
        if reasoning:
            _assistant_msg["reasoning_content"] = reasoning
        messages.append(_assistant_msg)

        # 逐个执行工具
        terminate = False
        unknown_tool = False
        for tc in tool_calls:
            name = tc["name"]
            executor = tool_executors.get(name)
            if executor is None:
                unknown_tool = True
                tool_result = {
                    "content": f"工具名无效：{name}。可用工具只有：{', '.join(tool_executors)}。请不要重试工具，直接用中文回答用户。",
                    "details": {"status": "error", "error": f"unknown tool {name}"},
                    "terminate": False,
                }
            else:
                try:
                    result = executor(tc["arguments"])
                    if asyncio.iscoroutine(result):
                        result = await result
                    tool_result = result
                except Exception as e:
                    logger.warning(f"[tool_loop] 工具 {name} 执行失败: {e}")
                    tool_result = {
                        "content": f"工具执行失败：{e}",
                        "details": {"status": "error", "error": str(e)},
                        "terminate": False,
                    }
            results.append({"name": name, "arguments": tc["arguments"], **tool_result})
            # 回填 tool 结果消息（OpenAI 格式）
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(tool_result["content"], ensure_ascii=False),
            })
            if tool_result.get("terminate"):
                terminate = True

            # 死磕保险：连续 2 次「知识库暂无匹配」，或累计检索满 3 次（即便每次
            # 都有弱相关结果）→ 物理移除 search_kb 并明确指令。
            # 纯 prompt 约束「最多查 2-3 次」不同模型遵守程度不同：DeepSeek 大体
            # 会照做，但换成 Claude（中转站）后实测会无视这条约束、每轮换个说法
            # 继续查，8 轮打满仍未答复用户（final_text 为空，用户看到的是纯沉默）。
            # 只靠"暂无匹配"文本匹配防不住这种情况——弱相关结果永远不是空，所以
            # 额外用调用次数计数兜底，与具体模型是否守约束无关。
            if name == "search_kb":
                _search_call_count += 1
                if "暂无匹配" in (tool_result.get("content") or ""):
                    _empty_search_streak += 1
                else:
                    _empty_search_streak = 0  # 有命中就重置
            if _empty_search_streak >= 2 or _search_call_count >= 3:
                logger.warning(f"[tool_loop] 检索 {_search_call_count} 次（连续无结果 "
                               f"{_empty_search_streak} 次），移除所有工具，强制进入文本回答")
                # 仅允许诊断检索循环：达到上限后移除 search_kb，
                # 下一轮直接生成有界文本回答。
                tools = [tool for tool in tools
                         if tool.get("function", {}).get("name") != "search_kb"]
                messages.append({
                    "role": "system",
                    "content": "知识库检索已达上限（多次检索均无强相关结果，或结果相关度很低）。"
                               "不要再尝试检索。请基于已有检索结果 + 你的 AGV/AMR 领域知识给用户一个"
                               "**有界分析**：可能的通用原因和排查方向，"
                               "明确说明「这是通用分析，具体操作请以现场工程师确认为准」，"
                               "并可建议转工单。不要编造「手册里有XX步骤」这类具体指引。",
                })
                _empty_search_streak = 0  # 重置，防重复插入

        if terminate:
            # 工具要求终止（草稿已生成）：不再让 LLM 说收尾——收尾话术是
            # 固定文案，由调用方兜底（如「工单草稿已生成…」）。
            # 之前这里再调一次 LLM（thinking 开启 ~2s）纯属浪费。
            return "", results

        if unknown_tool:
            # Claude/中转站偶尔会返回自造的工具名（例如 mcp__local__search_kb）。
            # 继续把错误回填给模型会触发「纠正工具名→再次调用→再纠正」死循环。
            # 已经把错误说明回填，下一轮不再提供工具，强制模型输出最终答复。
            tools = []
            messages.append({
                "role": "system",
                "content": "检测到无效工具调用。现在禁止调用任何工具，请直接用中文回答用户原问题。"
                           "如果知识库结果不足，请给出基于 AGV/AMR 通用原理的有界分析，"
                           "明确说明具体操作以现场工程师确认为准。",
            })
            continue
    logger.warning(f"[tool_loop] 达到最大迭代次数 {max_iterations}，强制结束")
    return "", results
