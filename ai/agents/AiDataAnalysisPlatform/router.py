"""AI 数据分析平台 - FastAPI 路由

提供数据分析、流式分析、快速对话和健康检查的 HTTP 接口。

挂载方式::

    from ai.agents.AiDataAnalysisPlatform.router import router
    app.include_router(router, prefix="/api/ai/analysis", tags=["AI数据分析"])
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .agent import DataAnalysisAgent
from .logging_config import get_logger
from .schemas import (
    AnalysisRequest,
    AnalysisResult,
    ChatResponse,
    ChatContextMeta,
    HealthResponse,
    QuickChatRequest,
)
from .report_schemas import ReportRequest, ReportResult, ReportPeriod
from .report_generator import ReportGenerator, _parse_date

logger = get_logger("Router")

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


def _merge_chat_context(request: QuickChatRequest) -> dict:
    """按“显式参数优先，context_meta 补空位”合并聊天上下文。"""
    meta: ChatContextMeta | None = request.context_meta
    explicit = getattr(request, "model_fields_set", set())

    def pick(field_name: str):
        if field_name in explicit:
            return getattr(request, field_name)
        if meta is not None:
            return getattr(meta, field_name)
        return getattr(request, field_name)

    context_parts: list[str] = []
    if request.context:
        context_parts.append(request.context)
    if meta is not None:
        if meta.scene:
            context_parts.append(f"当前页面场景：{meta.scene}")
        if meta.project_name:
            context_parts.append(f"当前页面项目：{meta.project_name}")

    return {
        "question": request.question,
        "context": "\n".join(context_parts) if context_parts else None,
        "data": request.data,
        "data_source": request.data_source,
        "analysis_type": pick("analysis_type"),
        "project_code": pick("project_code"),
        "user_id": pick("user_id"),
        "period": pick("period"),
        "date": pick("date"),
    }


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
    """快速对话问答。

    - 仅传 question/context：自动识别是普通聊天还是数据分析。
    - 传 data：执行带数据上下文的分析问答。
    - 不传 data 但传 project_code / user_id：自动查库并分析。
        - 未显式传范围参数时，可由 context_meta 补充页面上下文。
    """
    agent = get_agent()
    try:
        payload = _merge_chat_context(request)
        return await agent.chat(**payload)
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


# -- 报告生成 -------------------------------------------------------


@router.post("/report/generate", summary="生成日报/周报")
async def generate_report_api(request: ReportRequest):
    """生成日报或周报。

    - 非流式（stream=false）：返回结构化 ReportResult。
    - 流式（stream=true）：返回 SSE 文本流。
    """
    agent = get_agent()
    llm_client = agent._llm
    generator = ReportGenerator(llm_client)
    target_date = _parse_date(request.date)

    if request.stream:
        async def stream_generator():
            try:
                async for chunk in generator.generate_stream(
                    period=request.period,
                    target_date=target_date,
                    project_code=request.project_code,
                    user_id=request.user_id,
                ):
                    payload = json.dumps(
                        {"content": chunk}, ensure_ascii=False
                    )
                    yield "data: " + payload + _SSE_NEWLINE
                yield "data: [DONE]" + _SSE_NEWLINE
            except Exception as exc:
                logger.exception("流式报告生成出错")
                err_payload = json.dumps(
                    {"error": str(exc)}, ensure_ascii=False
                )
                yield "data: " + err_payload + _SSE_NEWLINE

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
        )

    try:
        result = await generator.generate(
            period=request.period,
            target_date=target_date,
            project_code=request.project_code,
            user_id=request.user_id,
        )
        return {"code": 0, "data": result.model_dump()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("报告生成失败")
        raise HTTPException(status_code=500, detail="报告生成服务内部错误") from exc


@router.get("/report/health", summary="报告服务健康检查")
async def report_health():
    """检查报告生成服务是否可用。"""
    try:
        agent = get_agent()
        health = agent.health_check()
        return {
            "code": 0,
            "data": {
                "status": "ok",
                "service": "report-generator",
                "provider": health.provider,
                "model": health.model,
            },
        }
    except Exception as exc:
        return {"code": 1, "data": {"status": "error", "error": str(exc)}}
