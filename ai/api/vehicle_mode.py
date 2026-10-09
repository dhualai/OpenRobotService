# -*- coding: utf-8 -*-
"""车型定制模式：扫码入口的模式确认接口（XQE 试点）。

链路：车体二维码（URL 带车型/项目/客户）→ 前端「我要摇人」界面弹信息确认
弹窗 → 用户确认后前端调 POST /mode/confirm → 此处校验 vehicles 档案表：
  - 有档案 → 该 session 注册为车型定制模式（memory.metadata["vehicle_mode"]），
    返回该车型 SOP 手册文档清单（md 链接，前端拉正文渲染）
  - 无档案 → code=1 报错拦住（实验阶段不降级常规模式）

铁律：定制模式是「加法」——常规链路（不调本接口的会话）一个字节不变；
后续 pipeline 的检索域限定 / prompt 注入 / 错误码直查全部以
metadata["vehicle_mode"] 存在为唯一开关。

幂等：同一 session 重复调用 = 覆盖重注册（前端刷新即重调，天然安全）。
"""
import time
import asyncio
import random
import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ai.core.logging import get_logger
from ai.core.memory import MemoryManager

logger = get_logger(__name__)

# ---- 开场大方向引导题（0930 定稿：服务端直出，不经 LLM）----
OPENING_QUESTION = "您遇到了什么问题？"
OPENING_HINT = "若是其他情况，请在下方输入框描述"
_OPENING_KEEP_SUBSTR = "故障码"   # 保底类目（重要大类，随机时必选）
_OPENING_LIMIT = 5


def parse_fork_tree_choices(model: str, kb_root: Optional[Path] = None) -> List[str]:
    """读 kb/company/{车型}/manual/ 下文件名含「分叉树」的 md，解析顶层 ## 标题。

    只收**带数字序号前缀**的顶层节点（`## 1. xxx`）——分叉树大方向类目天然
    编号；「附：…」「待用户确认」等文档管理章节无序号，天然排除（v0.1 实测
    混进过「待用户确认」）。剥序号后返回类目列表；文件不存在/无标题返回 []
    （开场题是可选能力，空库自动退化为纯输入框，不阻塞模式注册）。
    kb_root 参数供测试注入临时目录。
    """
    sub = model_to_subpath(model)
    if not sub:
        return []
    root = (kb_root or _default_kb_root()) / "company" / sub / "manual"
    tree = None
    if root.is_dir():
        for f in sorted(root.glob("*.md")):
            if "分叉树" in f.stem:
                tree = f
                break
    if tree is None:
        return []
    try:
        lines = tree.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        logger.warning(f"[vehicle_mode] 分叉树读取失败: {tree.name}: {e}")
        return []
    out: List[str] = []
    for ln in lines:
        m = re.match(r"^##\s+(\d+)\s*[.、．]\s*(.+?)\s*$", ln)
        if not m:
            continue
        t = m.group(2).strip()
        if t and t not in out:
            out.append(t)
    return out


def build_opening(top_levels: List[str], rng: Optional[random.Random] = None) -> Optional[Dict]:
    """开场题数据：故障码类保底 + 其余随机抽满 5（0930 定稿）。

    随机是用户拍板（固定前 5 会让其余类永无曝光）；故障码是重要大类保底
    不随机。不足 2 类不出题（None）。
    """
    if not top_levels:
        return None
    rng = rng or random
    keep = [c for c in top_levels if _OPENING_KEEP_SUBSTR in c]
    rest = [c for c in top_levels if _OPENING_KEEP_SUBSTR not in c]
    rng.shuffle(rest)
    choices = (keep + rest)[:_OPENING_LIMIT]
    if len(choices) < 2:
        return None
    return {"question": OPENING_QUESTION, "choices": choices, "hint": OPENING_HINT}


class ModeConfirmRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID（前端先建会话再调本接口绑定模式）")
    model: str = Field(..., min_length=1, max_length=64, description="车型（如 XQE），上游 URL 带来、用户已在弹窗确认")
    project_name: str = Field(default="", max_length=128, description="项目名（上游 URL 带来）")
    customer_name: str = Field(default="", max_length=128, description="客户名（上游 URL 带来）")
    vehicle_code: str = Field(default="", max_length=64, description="唯一车号（可选；传了按车号精确校验，不传按车型+项目匹配）")


# ============================================================
# 车辆档案表（惰性幂等建表，进程级只建一次）
# ============================================================
_ensure_table_lock = asyncio.Lock()
_ensure_table_done = False


async def _ensure_vehicle_table() -> None:
    """幂等建 vehicles 表（AI 侧自有表，create_all 只建它自己，不碰既有表）。"""
    global _ensure_table_done
    if _ensure_table_done:
        return
    async with _ensure_table_lock:
        if _ensure_table_done:
            return
        from ai.core.database import Base, Vehicle, engine  # noqa: F401

        def _create() -> None:
            Base.metadata.create_all(engine, tables=[Vehicle.__table__])

        await asyncio.to_thread(_create)
        _ensure_table_done = True


async def _lookup_vehicle(model: str, project_name: str, vehicle_code: str):
    """查车辆档案。返回 Vehicle 行或 None（调用方报错拦住）。

    匹配规则（0930 放宽：车型是白名单键，项目名/客户名仅展示不参与匹配——
    车辆档案与二维码录入信息是两套数据，靠项目名字符串相等关联太脆，实锤
    卡「未建档」）：
      1. model 大小写归一必匹配（xqe/XQE 等价；知识库目录也按 upper 归一）
      2. vehicle_code 非空 → 车号大小写归一精确匹配
    只认 active 档案；多条命中取第一条（初版手工建档保证不重复）。
    """
    from ai.core.database import Vehicle, engine
    from sqlalchemy import func
    from sqlalchemy.orm import Session as DBSession

    m = (model or "").strip().upper()
    code = (vehicle_code or "").strip().upper()
    if not m:
        return None

    def _query():
        with DBSession(engine) as s:
            q = s.query(Vehicle).filter(
                Vehicle.status == "active",
                func.upper(Vehicle.model) == m)
            if code:
                q = q.filter(func.upper(Vehicle.vehicle_code) == code)
            return q.first()

    return await asyncio.to_thread(_query)


# ============================================================
# 车型 → 知识库目录映射 + 手册清单
# ============================================================
def model_to_subpath(model: str) -> str:
    """车型 → company 域下的知识库子目录名。XQE → XQE；未来新车型零改动。

    车型知识不建独立顶层域（0930 用户定稿）：kb/company/{车型}/ 入库归
    company 域，payload sub_domain="{车型}/manual"，检索按此过滤。"""
    return (model or "").strip().upper()


def model_to_domain(model: str) -> str:
    """车型 → 所属 kb 域名。车型知识统一挂 company 域。"""
    return "company"


# 引导设施文档（不走 SOP 外显按钮）：分叉树=对话引导题的内容源、故障码表=
# 输码直查的内容源——它们的入口在对话里，不作为文档气泡重复露出（0930 定稿：
# 除引导设施外，一个文件一个 SOP 气泡按钮）
_GUIDE_DOC_KEYWORDS = ("分叉树", "故障码表")


def list_manual_docs(model: str, kb_root: Optional[Path] = None,
                     media_prefix: str = "/api/ai/media",
                     exclude_guide_docs: bool = False) -> List[Dict[str, str]]:
    """扫 kb/company/{车型}/manual/*.md 生成手册文档清单（title + 正文 URL）。

    exclude_guide_docs=True 时排除引导设施文档（分叉树/故障码表）——confirm
    响应的 manual_docs 用于 SOP 外显气泡，只出可在线查看的操作文档。
    kb 目录/车型目录不存在 → 返回空清单（手册是可选能力，不阻塞模式注册）。
    URL 走 run.py 已挂的静态路由 {media_prefix}/kb/**（前端 fetch 后自行渲染）。
    kb_root 参数供测试注入临时目录。
    """
    sub = model_to_subpath(model)
    if not sub:
        return []
    root = (kb_root or _default_kb_root()) / "company" / sub / "manual"
    if not root.is_dir():
        return []
    docs: List[Dict[str, str]] = []
    for f in sorted(root.glob("*.md")):
        if exclude_guide_docs and any(k in f.stem for k in _GUIDE_DOC_KEYWORDS):
            continue
        docs.append({
            "title": f.stem,
            "path": f"company/{sub}/manual/{f.name}",
            "url": f"{media_prefix}/kb/company/{sub}/manual/{f.name}",
        })
    return docs


def _default_kb_root() -> Path:
    from ai.config import _KB_DIR
    return _KB_DIR


# ============================================================
# 模式注册主流程
# ============================================================
async def register_mode(req: ModeConfirmRequest,
                        memory_manager: Optional[MemoryManager] = None) -> dict:
    """校验车辆档案 → session 注册定制模式 → 返回手册清单。

    返回：{code: 0, data: {confirmed, model, domain, manual_docs}} 或 {code: 1, message}。
    memory_manager 参数供测试注入 mock；缺省用真实 get_memory_manager()。
    """
    model = (req.model or "").strip()
    project_name = (req.project_name or "").strip()
    customer_name = (req.customer_name or "").strip()
    vehicle_code = (req.vehicle_code or "").strip()

    if not model:
        return {"code": 1, "message": "车型不能为空"}

    await _ensure_vehicle_table()
    vehicle = await _lookup_vehicle(model, project_name, vehicle_code)
    if vehicle is None:
        # 实验阶段：报错拦住，不降级常规模式（前端展示报错弹窗）
        logger.warning(
            f"[vehicle_mode] 未建档拦截: model={model!r} project={project_name!r} "
            f"vehicle_code={vehicle_code!r}, session={req.session_id}",
        )
        return {"code": 1, "message": f"车型 {model} 未建档或不在服务范围，请确认扫码信息"}

    domain = model_to_domain(vehicle.model)
    # 车型存归一后的值（= 磁盘目录名大小写）：档案表 model 是录入串，可能小写，
    # 而下游拿它拼检索 filter 的 sub_domain（pipeline._vehicle_mode_domains）、
    # 渲染 prompt 里的「车型 xxx」，都要与 kb 入库同形才对得上。
    model_key = model_to_subpath(vehicle.model)
    mode_info = {
        "model": model_key,
        "domain": domain,
        "vehicle_code": vehicle.vehicle_code,
        "project_name": vehicle.project_name or project_name,
        "customer_name": vehicle.customer_name or customer_name,
        "location": vehicle.location or "",
        "registered_at": int(time.time()),
    }

    mm = memory_manager or await _default_memory_manager()
    memory = await mm.get_memory(req.session_id)
    # SOP 外显清单：排除引导设施文档（分叉树/故障码表的入口在对话里）
    manual_docs = list_manual_docs(vehicle.model, exclude_guide_docs=True)
    opening = build_opening(parse_fork_tree_choices(vehicle.model))
    if opening:
        # 开场类目随模式入 metadata：pipeline 识别「用户点开场气泡」（query
        # == 类目原文）做零 LLM 短接，不必再读分叉树文件
        mode_info["opening_choices"] = opening["choices"]
    # 覆盖重注册 = 幂等（前端刷新即重调本接口）
    memory.metadata["vehicle_mode"] = mode_info
    await mm.save_memory(memory)

    logger.info(
        f"[vehicle_mode] 定制模式注册: session={req.session_id} model={model_key} "
        f"vehicle={vehicle.vehicle_code} domain={domain} manual={len(manual_docs)}篇 "
        f"opening={len(opening['choices']) if opening else 0}类",
    )
    return {
        "code": 0,
        "data": {
            "confirmed": True,
            "model": model_key,
            "domain": domain,
            "manual_docs": manual_docs,
            "opening": opening,
        },
    }


async def _default_memory_manager() -> MemoryManager:
    from ai.core import get_memory_manager
    return await get_memory_manager()


def register_vehicle_mode_routes(qa_router) -> None:
    """把模式确认路由挂到 qa_router（router.py 一行调用，其余逻辑全部隔离在本模块）。"""
    from starlette.concurrency import run_in_threadpool  # noqa: F401（预留同步重活用）

    @qa_router.post("/mode/confirm", summary="车型定制模式确认（扫码入口：校验车辆档案并注册会话模式）")
    async def mode_confirm(req: ModeConfirmRequest):
        return await register_mode(req)
