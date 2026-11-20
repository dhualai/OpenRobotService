"""界面图鉴：VLM 只标难懂控件并提问（不写全页说明书）。"""

from __future__ import annotations

import base64
import io
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from ai.core.logging import get_logger

logger = get_logger("UI_ATLAS")

SCAN_SYSTEM = (
    "你是工业软件 UI 图鉴标注助手。"
    "宁缺毋滥：只标真正难懂、无文案、容易猜错业务含义的控件；"
    "框必须紧紧包住目标；提问里的方位必须与框在图上的真实位置一致。"
)

SCAN_PROMPT = """请看这张产品界面截图。

【任务】
1. 可选：一句 page_caption（哪一页、主区干什么）。
2. 只标「难懂」控件：无文字标签且业务含义不明的数字/图标/色条（典型：车卡上无「电量」二字的百分比条）。
3. 每个点：紧紧框住该控件（不要框整行、整栏、整块地图）；question 用中文提问，且方位描述必须与框的真实位置一致（框在左下就不要写右下）。
4. 最多输出 5 个难懂点；没有把握就少标或 regions=[]。

【不要框（常见误标）】
- 地图视口本身、路径网格、车体图标轮廓
- 地图工具条：缩放百分比、2D/3D、复位、坐标读数（光标/视口 XY）
- 顶栏角标数字、省略号「…」、通用图标按钮（帮助/通知）
- 已有中文标签的菜单、按钮、状态徽章（在线/自动/工作等）
- 装饰线、空白、整页大框

【坐标】
相对整图归一化：x,y 为框左上角，w,h 为宽高，均在 0～1；框尽量小而准。

只输出 JSON：
{"page_caption":"……","regions":[{"x":0.12,"y":0.71,"w":0.08,"h":0.04,"question":"……"}]}
"""

EXPLAIN_SYSTEM = (
    "你是工业软件界面专家。"
    "只根据图片里红框/放大图中实际可见的文字、颜色、图标判断业务含义，"
    "禁止根据坐标数字臆测位置，禁止说「空白/滚动条」除非放大图里确实什么都没有。"
)


def _parse_json_obj(raw: Optional[str]) -> Dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    m = re.search(r"\{[\s\S]*\}", str(raw).strip())
    if not m:
        return {}
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_scan_json(raw: str) -> Dict[str, Any]:
    data = _parse_json_obj(raw)
    if not data:
        if raw and str(raw).strip():
            logger.warning("ui-atlas scan JSON 解析失败: %s", str(raw)[:200])
        return {"page_caption": None, "regions": []}
    caption = data.get("page_caption")
    if caption is not None:
        caption = str(caption).strip() or None
    regions_in = data.get("regions") or []
    regions: List[Dict[str, Any]] = []
    if isinstance(regions_in, list):
        for i, r in enumerate(regions_in[:8]):
            if not isinstance(r, dict):
                continue
            try:
                x = float(r.get("x", 0))
                y = float(r.get("y", 0))
                w = float(r.get("w", 0))
                h = float(r.get("h", 0))
            except (TypeError, ValueError):
                continue
            q = str(r.get("question") or "").strip()
            if not q or w <= 0 or h <= 0:
                continue
            if w > 0.45 or h > 0.35:
                continue
            x = max(0.0, min(1.0, x))
            y = max(0.0, min(1.0, y))
            w = max(0.01, min(1.0 - x, w))
            h = max(0.01, min(1.0 - y, h))
            regions.append({
                "id": f"r{i+1}",
                "x": round(x, 4),
                "y": round(y, 4),
                "w": round(w, 4),
                "h": round(h, 4),
                "question": q,
                "answer": None,
                "status": "pending",
            })
    return {"page_caption": caption, "regions": regions}


def _iou(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def merge_scan_regions(
    existing: List[Dict[str, Any]],
    newly: List[Dict[str, Any]],
    iou_thresh: float = 0.35,
) -> List[Dict[str, Any]]:
    """保留已有标注，只追加与已有框重叠不高的新难懂点。"""
    kept = list(existing or [])
    for n in newly or []:
        if any(_iou(n, e) >= iou_thresh for e in kept):
            continue
        item = dict(n)
        item["id"] = f"n{uuid.uuid4().hex[:8]}"
        item.setdefault("answer", None)
        item.setdefault("status", "pending")
        kept.append(item)
    return kept


def _existing_block(existing: Optional[List[Dict[str, Any]]]) -> str:
    rows = []
    for e in existing or []:
        if not isinstance(e, dict):
            continue
        try:
            x, y, w, h = float(e["x"]), float(e["y"]), float(e["w"]), float(e["h"])
        except (KeyError, TypeError, ValueError):
            continue
        ans = (e.get("answer") or "").strip()
        q = (e.get("question") or "").strip()
        note = f"已标注「{ans}」" if ans else (f"待答：{q}" if q else "已有框")
        rows.append(f"- 框({x:.3f},{y:.3f},{w:.3f},{h:.3f}) {note}")
    if not rows:
        return ""
    return (
        "\n【已有标注——请保留，不要重复框这些区域】\n"
        + "\n".join(rows)
        + "\n只找出尚未覆盖的、新的难懂点；若没有新增，regions 返回 []。\n"
    )


def _load_pil_image(image_url: str):
    """从 data URL 或 http(s) 加载 PIL Image（RGB）。"""
    from PIL import Image

    raw = str(image_url).strip()
    if raw.startswith("data:"):
        if "," not in raw:
            raise ValueError("无效的 data URL")
        b64 = raw.split(",", 1)[1]
        data = base64.b64decode(b64)
        img = Image.open(io.BytesIO(data))
    elif raw.startswith("http://") or raw.startswith("https://"):
        req = Request(raw, headers={"User-Agent": "OpenRobotService-ui-atlas/1.0"})
        with urlopen(req, timeout=30) as resp:  # noqa: S310 — 仅服务端可信图鉴 URL
            data = resp.read()
        img = Image.open(io.BytesIO(data))
    else:
        raise ValueError("image_url 需为 data URL 或 http(s)")
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    return img


def _to_data_url(img, fmt: str = "JPEG", quality: int = 85) -> str:
    buf = io.BytesIO()
    save_kw: Dict[str, Any] = {}
    if fmt.upper() == "JPEG":
        save_kw["quality"] = quality
        save_kw["optimize"] = True
        if img.mode != "RGB":
            img = img.convert("RGB")
    img.save(buf, format=fmt, **save_kw)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    mime = "image/jpeg" if fmt.upper() == "JPEG" else "image/png"
    return f"data:{mime};base64,{b64}"


def _box_to_pixels(
    img_w: int, img_h: int, x: float, y: float, w: float, h: float, pad_ratio: float = 0.08,
) -> Tuple[int, int, int, int]:
    """归一化框 → 像素裁剪框（含少量 padding），并夹紧到图内。"""
    pad_x = max(2, int(w * img_w * pad_ratio))
    pad_y = max(2, int(h * img_h * pad_ratio))
    left = max(0, int(x * img_w) - pad_x)
    top = max(0, int(y * img_h) - pad_y)
    right = min(img_w, int((x + w) * img_w) + pad_x)
    bottom = min(img_h, int((y + h) * img_h) + pad_y)
    if right - left < 4:
        right = min(img_w, left + 4)
    if bottom - top < 4:
        bottom = min(img_h, top + 4)
    return left, top, right, bottom


def prepare_explain_images(
    image_url: str, x: float, y: float, w: float, h: float,
) -> List[str]:
    """
    为手动画框解释准备视觉输入：
    1) 整图 + 红框高亮（给上下文）
    2) 框选区域放大图（让模型看清文字/徽章）
    失败时回退为原图。
    """
    try:
        from PIL import ImageDraw

        img = _load_pil_image(image_url)
        iw, ih = img.size
        left, top, right, bottom = _box_to_pixels(iw, ih, x, y, w, h, pad_ratio=0.12)

        marked = img.copy()
        draw = ImageDraw.Draw(marked)
        bx1 = int(x * iw)
        by1 = int(y * ih)
        bx2 = int((x + w) * iw)
        by2 = int((y + h) * ih)
        for t in range(3):
            draw.rectangle(
                [bx1 - t, by1 - t, bx2 + t, by2 + t],
                outline=(227, 77, 89),
            )
        marked_url = _to_data_url(marked)

        crop = img.crop((left, top, right, bottom))
        cw, ch = crop.size
        scale = max(1, min(4, int(280 / max(cw, ch, 1))))
        if scale > 1:
            crop = crop.resize((cw * scale, ch * scale))
        crop_url = _to_data_url(crop)
        return [marked_url, crop_url]
    except Exception as e:
        logger.warning("ui-atlas explain 裁剪失败，回退原图: %s", e)
        return [str(image_url).strip()]


async def scan_ui_atlas_image(
    image_url: str,
    existing_regions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """对标准界面图扫难懂区域；可带已有框，只找增量。"""
    if not image_url or not str(image_url).strip():
        raise ValueError("image_url 不能为空")
    from ai.core import get_llm_client

    prompt = SCAN_PROMPT + _existing_block(existing_regions)
    llm = await get_llm_client()
    raw = await llm.complete_vision(
        prompt=prompt,
        images=[str(image_url).strip()],
        system_prompt=SCAN_SYSTEM,
        max_tokens=1200,
        temperature=0.05,
    )
    return _parse_scan_json(raw or "")


async def explain_ui_atlas_region(
    image_url: str,
    box: Dict[str, Any],
) -> Dict[str, Any]:
    """对手动画框：猜测框内控件含义（红框整图 + 放大裁剪，避免纯坐标幻觉）。"""
    if not image_url or not str(image_url).strip():
        raise ValueError("image_url 不能为空")
    try:
        x, y = float(box["x"]), float(box["y"])
        w, h = float(box["w"]), float(box["h"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("box 需要 x,y,w,h") from e

    images = prepare_explain_images(str(image_url).strip(), x, y, w, h)
    prompt = (
        "下面有两张图（若只有一张则只看该图）：\n"
        "1）整页截图，红色矩形标出人工框选区域；\n"
        "2）该区域的放大图。\n"
        "请只描述红框/放大图里实际看到的内容：文字、颜色、图标、控件类型，"
        "并用一句中文说明业务含义（例如「车状态徽章：待命」）。\n"
        "不要提及或依赖任何数字坐标；不要说空白/滚动条，除非放大图里确实没有任何可见元素。\n"
        '只输出 JSON：{"guess":"……","confidence":"high|low"}'
    )
    from ai.core import get_llm_client

    llm = await get_llm_client()
    raw = await llm.complete_vision(
        prompt=prompt,
        images=images,
        system_prompt=EXPLAIN_SYSTEM,
        max_tokens=300,
        temperature=0.05,
    )
    data = _parse_json_obj(raw)
    guess = str(data.get("guess") or "").strip()
    if not guess and raw:
        guess = str(raw).strip()[:200]
    conf = str(data.get("confidence") or "low").strip().lower()
    if conf not in ("high", "low"):
        conf = "low"
    return {"guess": guess or "不确定", "confidence": conf}
