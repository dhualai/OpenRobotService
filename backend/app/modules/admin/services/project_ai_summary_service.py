"""AI 项目摘要 —— 读取「项目信息管理」整棵信息树 + 项目基础字段，由大模型总结项目基础情况。

用途：项目详情页（后台管理）→ 项目概况 → 「AI 项目摘要」卡片 → 生成 / 重新生成。

大模型接口：app/core/llm_client.py 的 LLMClient（backend 自维护，不依赖 ai 模块）。
密钥/模型取 backend/.env（settings.LLM_API_KEY / LLM_API_URL / LLM_MODEL_NAME），
与「文件导入（AI 识别）」共用同一配置，默认 DeepSeek flash。

生成结果写回 ext_info.overview.ai_summary（default.yaml 模板的既有字段），响应同时返回
summary 与更新后的 ext_info，前端据此刷新展示；刷新页面后从项目详情接口读回。

异常约定（接口层映射）：ValueError → 400（项目没有信息节点等业务性错误）；
RuntimeError → 503（AI 未配置 / 调用失败）。
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.modules.admin.services.info_node_service import info_node_service

logger = logging.getLogger("admin")

# 摘要参数：与文件识别同为抽取/总结类任务，低温度减少发散
SUMMARY_TEMPERATURE = 0.3
SUMMARY_MAX_TOKENS = 1500
MAX_INFO_CHARS = 12_000  # 送大模型的信息树正文上限（超出截断）

SYSTEM_PROMPT = (
    "你是一个企业项目管理助理，负责根据项目档案资料撰写客观、精炼的「项目概况总结」。"
    "输出为结构化的 Markdown（二级标题分节 + 要点列表），只输出总结本身："
    "不要代码块围栏、不要任何解释或寒暄。"
)

# 项目基础字段（Project 真实列）→ 提示词里的中文标签，按此顺序输出
PROJECT_FIELD_LABELS: List[Tuple[str, str]] = [
    ("name", "项目名称"),
    ("project_code", "项目编号"),
    ("internal_code", "内部编号"),
    ("project_type", "项目类型"),
    ("status", "项目阶段"),
    ("project_region", "项目区域/地点"),
    ("description", "项目描述"),
    ("project_manager", "项目经理"),
    ("contact_person", "对接人"),
    ("sales", "销售"),
    ("pre_sales", "售前"),
    ("field_engineer", "现场工程师"),
    ("total_vehicle_count", "总车数"),
    ("recent_delivery_content", "车型&车数"),
    ("controller_vendor", "控制器选择"),
    ("system_integration", "系统/外设对接"),
    ("server_deployment_status", "服务器部署"),
    ("deployment_date", "部署时间"),
    ("deployment_version", "部署版本"),
    ("recent_delivery_date", "近期交付"),
    ("final_delivery_date", "最终交付"),
    ("special_attention", "特别关注"),
    ("risk_task_description", "风险和任务描述"),
    ("management_strategy", "项目管理策略"),
    ("expected_trend", "预期走向"),
]

_PROMPT_TEMPLATE = """请根据以下项目资料，为该项目写一段结构化的「项目概况总结」。

【项目基础字段】
{fields}

【项目信息管理】
{info}

【输出格式（Markdown，简洁优先）】
1. 每节 1～2 条「- 」要点、每条一句话；全文 250 字以内，宁简勿全：信息多的挑重点，信息少的
   不硬凑；
2. 按下述顺序分节，资料中没有的整节省略（不要写「暂无」「未提供」之类的占位）：
   ## 项目概况 —— 项目类型、当前阶段、部署区域，一句话概括；
   ## 硬件与车型 —— AGV 车型/数量、控制器选型；
   ## 系统与部署 —— 系统/外设对接、服务器部署、现场或网络条件；
   ## 交付与进度 —— 部署时间、交付节点与内容、项目进度；
   ## 风险与关注点 —— 风险、特殊要求；资料明显缺失的重要信息用一句话点到为止；
3. 只使用 ## 标题、- 列表与 **加粗**（加粗要克制：全篇最多 3～4 处，仅限字段名或关键数字），
   不要表格、代码块、链接、图片；
4. 严格依据上述资料书写：不得编造未出现的数字、车型、时间、客户名或系统名称；资料中没有的信息
   宁可不写，也不要推测；
5. 不要用「根据提供的信息」「综上所述」等套话开场，不要逐条复述字段名；同一条信息只写一次，
   不要跨小节重复，直接陈述项目情况。"""


def _decode_select_value(value: Any) -> Optional[str]:
    """下拉节点 value 解码：{"selected": "x", "options": [...]} → x；坏数据返回原文/None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value.strip()
    if isinstance(parsed, dict):
        selected = parsed.get("selected")
        return selected if isinstance(selected, str) else None
    return None


def _display_value(node: Dict[str, Any]) -> str:
    """节点内容 → 展示文本：text 原文（空白折叠）；select 取已选项；file/image 尽量取文件名。"""
    value = node.get("value")
    if value is None or value == "":
        return ""
    content_type = node.get("content_type") or "text"
    if content_type == "select":
        return (_decode_select_value(value) or "").strip()
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                for key in ("name", "file_name", "selected", "text"):
                    picked = parsed.get(key)
                    if isinstance(picked, str) and picked.strip():
                        return picked.strip()
                return ""
        return " ".join(raw.split())
    return " ".join(str(value).split())


def render_project_info(nodes: List[Dict[str, Any]], max_chars: int = MAX_INFO_CHARS) -> Tuple[str, int, int]:
    """信息树 → 提示词正文。

    返回 (正文, 已填写条目数, 未填写可填节点数)。每行「父路径 / 子节点：内容」；
    值为空的节点不输出正文（避免大模型把空节点当真信息），只计入未填写数。

    「可填节点」= 末级字段，或本身带值类型的分组（如「车型1」既是下拉又有「数量」子节点）。
    纯 text 的非末级节点是分组，不计入未填写——否则每个分叉都会虚报一项缺信息。
    """
    lines: List[str] = []
    filled = 0
    empty_leaf = 0

    def walk(items: List[Dict[str, Any]], prefix: str) -> None:
        nonlocal filled, empty_leaf
        for node in items or []:
            title = str(node.get("title") or "").strip() or "未命名节点"
            path = f"{prefix} / {title}" if prefix else title
            text = _display_value(node)
            children = node.get("children") or []
            if text:
                lines.append(f"{path}：{text}")
                filled += 1
            elif not children or (node.get("content_type") or "text") != "text":
                empty_leaf += 1
            walk(children, path)

    walk(nodes, "")
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…（内容过长已截断）"
    return body, filled, empty_leaf


def _format_project_fields(project: Dict[str, Any]) -> str:
    lines: List[str] = []
    for key, label in PROJECT_FIELD_LABELS:
        value = project.get(key)
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            value = "、".join(str(v) for v in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"{label}：{value}")
    return "\n".join(lines) or "（未填写）"


def build_summary_prompt(project: Dict[str, Any], nodes: List[Dict[str, Any]]) -> str:
    """组装提示词（纯函数，供单测）。"""
    info_body, filled, empty_leaf = render_project_info(nodes)
    if filled == 0:
        info_body = "（信息树中的条目均未填写）"
    elif empty_leaf:
        info_body += f"\n（另有 {empty_leaf} 个节点未填写）"
    return _PROMPT_TEMPLATE.format(
        fields=_format_project_fields(project),
        info=info_body,
    )


# ── 大模型客户端（app/core/llm_client.py，backend 自维护）──

from app.core.llm_client import get_llm_client


def _clean_summary(text: str) -> str:
    """清洗模型输出：去掉可能的 Markdown 围栏与首尾引号。"""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text.strip().strip('"').strip()


async def generate_for_project(project: Dict[str, Any]) -> Dict[str, Any]:
    """生成摘要并写回 ext_info.overview.ai_summary。

    project 为 project_service.get_project 的返回（含 ext_info；老项目为空时
    该接口已按模板 lazy 填充 overview+activity，这里据此深拷贝合并，避免丢字段）。
    返回 {summary, model, ext_info}。
    """
    from app.modules.admin.services.project_service import project_service

    project_id = project.get("id") or project.get("project_code")
    nodes = info_node_service.get_tree(str(project_id))
    if not nodes:
        raise ValueError("该项目还没有信息节点，请先在项目信息管理中初始化信息树后再生成摘要")

    prompt = build_summary_prompt(project, nodes)
    client = get_llm_client()
    try:
        summary = await client.complete(
            prompt,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=SUMMARY_MAX_TOKENS,
            temperature=SUMMARY_TEMPERATURE,
            thinking=False,
        )
    except Exception as exc:  # noqa: BLE001 —— 网络/鉴权/超时等统一转用户可读信息
        logger.error("[ai-summary] 大模型调用失败: project_id=%s, error=%s", project_id, exc)
        raise RuntimeError(f"大模型调用失败：{exc}") from exc

    summary = _clean_summary(summary)
    if not summary:
        raise RuntimeError("大模型没有返回内容，请稍后重试")

    ext_info = copy.deepcopy(project.get("ext_info") or {})
    overview = ext_info.get("overview")
    if not isinstance(overview, dict):
        overview = {}
    overview["ai_summary"] = summary
    ext_info["overview"] = overview

    # 不带 version → 跳过乐观锁校验（与企微同步等内部写入同一约定），version 仍会 +1
    updated = project_service.update_project(project_id, {"ext_info": ext_info})
    if not updated:
        raise ValueError("项目不存在或已被删除，摘要未保存")
    return {
        "summary": summary,
        "model": settings.LLM_MODEL_NAME,
        "ext_info": updated.get("ext_info") or ext_info,
    }
