"""AI 数据分析平台 - FastAPI 路由

提供数据分析、流式分析、快速对话和健康检查的 HTTP 接口。

挂载方式::

    from ai.agents.AiDataAnalysisPlatform.router import router
    app.include_router(router, prefix="/api/ai/analysis", tags=["AI数据分析"])
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .agent import DataAnalysisAgent
from .schemas import (
    AnalysisRequest,
    AnalysisResult,
    ChatResponse,
    HealthResponse,
    QuickChatRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# -- Agent 单例（延迟初始化）---------------------------------------

_agent: DataAnalysisAgent | None = None

# SSE 换行符常量（避免在 f-string 中使用转义序列导致工具截断）
_SSE_NEWLINE = chr(10) + chr(10)


def get_agent() -> DataAnalysisAgent:
    """获取 Agent 单例（延迟初始化）。

    首次调用时从环境变量读取配置并创建实例。
    """
    global _agent
    if _agent is None:
        _agent = DataAnalysisAgent.from_env()
    return _agent


def reset_agent() -> None:
    """重置 Agent 单例（用于测试或配置变更后重新初始化）。"""
    global _agent
    _agent = None


# -- 路由端点 -------------------------------------------------------

@router.get("/health", response_model=HealthResponse, summary="健康检查")
async def health_check() -> HealthResponse:
    """返回平台配置信息，验证 Agent 是否可用。"""
    agent = get_agent()
    return agent.health_check()


@router.post("/analyze", summary="数据分析")
async def analyze_data(request: AnalysisRequest):
    """对提供的数据执行 AI 分析。

    - 非流式（stream=false）：返回结构化 AnalysisResult。
    - 流式（stream=true）：返回 SSE 文本流，逐块返回分析内容。
    """
    agent = get_agent()

    if request.stream:
        async def stream_generator():
            try:
                async for chunk in agent.analyze_stream(
                    data=request.data,
                    data_source=request.data_source,
                    analysis_type=request.analysis_type,
                    question=request.question,
                    context=request.context,
                ):
                    payload = json.dumps(
                        {"content": chunk}, ensure_ascii=False
                    )
                    yield "data: " + payload + _SSE_NEWLINE
                yield "data: [DONE]" + _SSE_NEWLINE
            except Exception as exc:
                logger.exception("流式分析出错")
                err_payload = json.dumps(
                    {"error": str(exc)}, ensure_ascii=False
                )
                yield "data: " + err_payload + _SSE_NEWLINE

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
        )

    try:
        result = await agent.analyze(
            data=request.data,
            data_source=request.data_source,
            analysis_type=request.analysis_type,
            question=request.question,
            context=request.context,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("分析失败")
        raise HTTPException(status_code=500, detail="分析服务内部错误") from exc


@router.post("/chat", response_model=ChatResponse, summary="快速对话")
async def quick_chat(request: QuickChatRequest) -> ChatResponse:
    """快速对话问答（无数据分析，纯文本交互）。"""
    agent = get_agent()
    try:
        return await agent.chat(
            question=request.question,
            context=request.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("对话失败")
        raise HTTPException(status_code=500, detail="对话服务内部错误") from exc


@router.get("/types", summary="分析类型列表")
async def list_analysis_types():
    """返回支持的分析类型枚举。"""
    from .schemas import AnalysisType, DataSource

    return {
        "analysis_types": [
            {"value": t.value, "label": t.name} for t in AnalysisType
        ],
        "data_sources": [
            {"value": s.value, "label": s.name} for s in DataSource
        ],
    }
