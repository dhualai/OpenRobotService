"""知识库检索工具（search_kb）——阶段 2 诊断工具化的第一个工具。

让 LLM 自主决定「要不要查知识库、查什么」：
- LLM 判断需要知识库支撑回答时调用 search_kb(query)
- query 由 LLM 自己组织（用户原话的关键词/改写后的检索词）
- 返回 top chunks 格式化文本（含来源标签 + 图片重写）

与 submit_ticket 并列组成诊断循环的工具集。
"""
from typing import Any, Dict

# ── 给 LLM 看的工具 schema（OpenAI function-calling 格式，DeepSeek 兼容）──
SEARCH_KB_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_kb",
        "description": (
            "检索 AGV/AMR 知识库（操作手册、FAQ、排查手册、车端错误码、产品目录等）。\n"
            "当用户的问题需要知识库支撑（操作步骤、错误码含义、故障排查方法）时调用。\n"
            "检索结果返回后先评估：\n"
            "- 结果是否直接回答用户问题？方向是否一致（用户问「没做该做的」，结果却是「做了不该做的」即方向相反）？\n"
            "- 不相关/方向相反 → 换关键词再查一次（用知识库文档会用的术语）\n"
            "- 相关但信息不足 → 再查一次补充细节，或向用户追问缺失信息\n"
            "- 换关键词重查最多 2-3 次；之后无论结果如何都必须基于现有信息回答，不得继续查\n"
            "- 多次查询仍无相关结果 → 用你自己的领域知识给用户一个**有界分析**："
            "基于 AGV/AMR 通用原理给出可能原因和排查方向，明确说明「这是通用分析，"
            "具体操作请以现场工程师确认为准」，并可建议转工单。"
            "禁止编造「手册里有XX步骤」这类具体指引。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词/问题描述。用与知识库文档匹配的术语（如「上轨锁区」「充电桩点位配置」），不要带语气词。",
                },
            },
            "required": ["query"],
        },
    },
}


def make_search_result(content: str) -> Dict[str, Any]:
    """检索结果包装：content 给 LLM 看，details 给日志/调试。"""
    return {
        "content": content,
        "details": {"status": "ok", "result_len": len(content)},
        "terminate": False,  # 查完知识库不终止循环——LLM 可能还要再查/追问/提单
    }


def make_search_error(error: str) -> Dict[str, Any]:
    return {
        "content": f"检索失败：{error}。请基于现有信息回答，或告知用户稍后重试。",
        "details": {"status": "error", "error": error},
        "terminate": False,
    }
