"""知识库浏览（ORS-716）：后台「数据资源 → 知识库」的只读接口。

读服务器上的 kb 目录（默认 /data/apps/OpenRobotService-Data/kb，可用 KB_DIR 覆盖）：
目录树（只看目录与 .md）、md 原文、图片。
所有路径都是相对 kb 根的相对路径，逐个做穿越校验（resolve 后必须仍在根内）。

图片不走鉴权外的直链：前端把 md 里的相对图片路径改写成 /api/admin/kb/media?path=...，
这样手机端预览时图片和接口同一个域、同一个登录态，不会 401。
"""
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

kb_browse_router = APIRouter(prefix="/kb", tags=["admin-kb"])

KB_ROOT = Path(os.getenv("KB_DIR") or "/data/apps/OpenRobotService-Data/kb").resolve()

_TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}

# 目录中文名：目录结构是英文的，后台面向业务同事展示要能看懂。
# 车型/品牌名保留原样（XQE、XFL201、VDA5050 这类不翻译）。
DIR_LABELS = {
    # 顶层
    "company": "公司知识",
    "team": "团队知识",
    "industry": "行业知识",
    "project": "项目知识",
    "personal": "个人知识",
    "sink_cards": "工单沉淀",
    # 公司知识
    "product_catalog": "产品目录",
    "vehicle_models": "车型专题",
    "vehicle": "车端资料",
    "common": "通用资料",
    "huarui": "华睿",
    "ksec": "科钛",
    "xmover": "自研车",
    "vehicle_errors": "车端错误码",
    "vehicle_io": "车端 IO",
    "vehicle_motion": "运动控制",
    "vehicle_calibration": "车端标定",
    "vehicle_implementation": "车端实施",
    "vda5050_protocol": "VDA5050 协议",
    "inacs": "iNACS 充电机",
    # 团队知识
    # ORS / USP 保留英文缩写（ORS=服务号、USP=平台名），业务同事认这两个缩写
    "manual": "操作手册",
    "faq": "常见问题",
    "error_codes": "错误码",
    "terminology": "术语表",
    "translation": "翻译表",
    "troubleshooting": "故障排查",
    "overview": "模块概览",
    "ui_pages": "页面导航",
    "diagnosis_cards": "诊断卡",
    "chat_qa": "知识补充",
    # 诊断卡分域
    "map": "地图",
    "monitor": "监控",
    "peripheral": "外设",
    "robot": "机器人",
    "simulator": "仿真",
    "system": "系统",
    "task": "任务",
    "warehousing": "仓储",
    # 行业知识
    "navigation": "导航规范",
    "standards": "行业标准",
    # 其他
    "media": "图片",
}

# 文件名中文映射（键为原文件名，区分大小写；查找时再兜底试小写）。
# 车型/型号文档（product_catalog、XQE 目录下）与诊断卡编号（KB-XXX-001）不在此表，保持原名。
FILE_LABELS = {
    # 通用
    "index.md": "目录索引",
    "readme.md": "说明",
    "_README.md": "说明",
    "overview.md": "模块概览",
    # 团队知识 - 服务号
    "manual_ops.md": "服务号平台手册",
    "product_features.md": "产品功能说明",
    # 团队知识 - USP 手册与资料
    "manual.md": "操作手册",
    "product.md": "产品功能",
    "faq.md": "常见问题",
    "faq_custom_robot.md": "自研车常见问题",
    "terminology.md": "术语表",
    "translation.md": "翻译表",
    "usp_enums.md": "USP 枚举中英对照",
    "ui_pages.md": "页面导航",
    # USP 模块概览
    "core_overview.md": "核心模块概览",
    "data_analysis_overview.md": "数据分析概览",
    "device_platform_overview.md": "设备平台概览",
    "license_platform_overview.md": "授权平台概览",
    "map_editor_overview.md": "地图编辑器概览",
    "map_platform_overview.md": "地图平台概览",
    "monitor_platform_overview.md": "监控平台概览",
    "robot_adapter_overview.md": "车辆适配概览",
    "simulator_overview.md": "仿真概览",
    "storage_platform_overview.md": "存储平台概览",
    "taskflow_overview.md": "任务流概览",
    # USP 错误码按系统分册
    "base.md": "错误码·基础",
    "deviceplatform.md": "错误码·设备平台",
    "mapplatform.md": "错误码·地图平台",
    "monitorinternal.md": "错误码·监控内部",
    "monitorplatform.md": "错误码·监控平台",
    "simulatorplatform.md": "错误码·仿真平台",
    "storageplatform.md": "错误码·存储平台",
    "taskflowplatform.md": "错误码·任务流平台",
    "vehicle.md": "错误码·车辆",
    # USP 故障排查
    "charging_issues.md": "排查·充电",
    "cross_module_issues.md": "排查·跨模块",
    "map_and_localization.md": "排查·地图与定位",
    "pick_place_and_detection.md": "排查·取放货与识别",
    "task_and_routing.md": "排查·任务与路径",
    "vehicle_hardware_and_params.md": "排查·车辆硬件与参数",
    "vehicle_runtime_errors.md": "排查·车端运行错误",
    # 公司知识 - 车端
    "inacs_charger_spec.md": "iNACS 充电机规格",
    "cheduan_errors.md": "车端错误码",
    "io_definitions.md": "IO 定义",
    "vda5050_protocol.md": "VDA5050 协议",
    "vehicle_calibration.md": "车端标定手册",
    "cheduan_implementation.md": "车端实施手册",
    "ep_localization.md": "EP 定位",
    "pms_mapping.md": "PMS 映射",
    "xfl201_implementation.md": "XFL201 实施手册",
    "xna_implementation.md": "XNA 实施手册",
    "xqe_implementation.md": "XQE 实施手册",
    "motion_control_errors.md": "运动控制错误码",
    "vda5050_params.md": "VDA5050 参数",
    "VDA5050-V2.1.0-2025-01-1.md": "VDA5050 协议 V2.1.0",
    # 行业知识标准
    "agv_navigation.md": "AGV 导航规范",
    "gbt30029_2023_agv_design.md": "GB/T 30029-2023 AGV 设计",
    "gbt30030_2023_agv_terminology.md": "GB/T 30030-2023 AGV 术语",
    "gbt45750_2025_agv_safety.md": "GB/T 45750-2025 AGV 安全",
}


# 入库时为防重名给文件名加的 UUID 尾巴（工单707_a1e88289-665.md），展示时去掉。
# 不改真实文件名——它参与入库去重与 source_file 溯源。
_UUID_TAIL = re.compile(r"_[0-9a-f]{8}-[0-9a-f]{3}$", re.I)


_CARD_FILE = re.compile(r"^KB-[A-Z]+-\d+\.md$", re.I)


def _card_title(p: Path) -> str:
    """诊断卡取人类可读标题（KB-MAP-001 这种编号顾名思义不了）。

    卡片的 frontmatter 里有 `title:` 字段（如「应用地图时提示失败」），优先用它；
    没有就退回正文 H1。只读文件头部，取不到返回空（调用方回退编号）。
    """
    try:
        with open(p, encoding="utf-8") as fh:
            head = fh.read(1500)
    except OSError:
        return ""
    m = re.search(r"^title:\s*(.+)$", head, re.M)
    if m:
        return m.group(1).strip().strip("\"'")[:60]
    m = re.search(r"^#\s+(.+)$", head, re.M)
    return m.group(1).strip()[:60] if m else ""


def _label(name: str) -> str:
    """展示名：映射表（先原样、再小写）→ 去掉 UUID 尾巴 → 原名兜底。"""
    if name in DIR_LABELS:
        return DIR_LABELS[name]
    if name in FILE_LABELS:
        return FILE_LABELS[name]
    if name.lower() in FILE_LABELS:
        return FILE_LABELS[name.lower()]
    base = name[:-3] if name.lower().endswith(".md") else name
    return _UUID_TAIL.sub("", base) or name


def _skip_dir(name: str) -> bool:
    """备份目录与纯图片目录不展示（图片在 md 预览里看）。"""
    return name.startswith(".") or name == "media" or "_bak_" in name


def _resolve(rel: str) -> Path:
    """相对路径 → 绝对路径，越界一律 400。"""
    rel = (rel or "").strip().lstrip("/")
    p = (KB_ROOT / rel).resolve()
    if p != KB_ROOT and KB_ROOT not in p.parents:
        raise HTTPException(status_code=400, detail="非法路径")
    return p


def _count_md(d: Path) -> int:
    n = 0
    for root, _, files in os.walk(d):
        n += sum(1 for f in files if f.endswith(".md"))
    return n


@kb_browse_router.get("", summary="知识库根信息")
def kb_root_info() -> Dict[str, Any]:
    if not KB_ROOT.exists():
        raise HTTPException(status_code=500, detail=f"知识库目录不存在: {KB_ROOT}")
    return {"code": 0, "data": {"root": str(KB_ROOT), "md_count": _count_md(KB_ROOT)}}


def _crumbs(rel: str) -> List[Dict[str, str]]:
    """面包屑（中文）：知识库 / 公司知识 / 产品目录。"""
    out: List[Dict[str, str]] = [{"label": "知识库", "path": ""}]
    acc = ""
    for part in (rel.split("/") if rel else []):
        acc = f"{acc}/{part}" if acc else part
        out.append({"label": _label(part), "path": acc})
    return out


@kb_browse_router.get("/tree", summary="目录树（只看目录与 md）")
def kb_tree(path: str = Query("", description="相对 kb 根的目录路径")) -> Dict[str, Any]:
    d = _resolve(path)
    if not d.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not d.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")

    dirs: List[Dict[str, Any]] = []
    files: List[Dict[str, Any]] = []
    try:
        entries = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), _label(x.name)))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="无权限读取") from e

    for e in entries:
        if e.name.startswith("."):
            continue  # .ingest_state 等内部状态目录不展示
        if e.is_dir() and _skip_dir(e.name):
            continue  # 备份目录与 media 不展示
        rel = e.relative_to(KB_ROOT).as_posix()
        if e.is_dir():
            dirs.append({"name": e.name, "label": _label(e.name), "path": rel,
                         "md_count": _count_md(e)})
        elif e.suffix.lower() == ".md":
            label = _label(e.name)
            if _CARD_FILE.match(e.name):
                title = _card_title(e)
                if title:
                    label = title
            files.append({"name": e.name, "label": label, "path": rel,
                          "size": e.stat().st_size})
    return {"code": 0, "data": {"path": path, "crumbs": _crumbs(path),
                                "dirs": dirs, "files": files}}


@kb_browse_router.get("/file", summary="读取 md 原文")
def kb_file(path: str = Query(..., description="md 相对路径")) -> Dict[str, Any]:
    p = _resolve(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if p.suffix.lower() not in _TEXT_EXT:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="utf-8", errors="replace")
    return {"code": 0, "data": {
        "path": p.relative_to(KB_ROOT).as_posix(),
        "name": p.name,
        "label": _label(p.name),
        "size": p.stat().st_size,
        "content": text,
    }}


@kb_browse_router.get("/media", summary="读取 kb 目录内图片/附件")
def kb_media(path: str = Query(..., description="相对 kb 根的路径")):
    p = _resolve(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    return FileResponse(p, media_type=mime, filename=p.name,
                        headers={"Cache-Control": "private, max-age=86400"})
