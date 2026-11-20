"""开发者模式的看图对照试验。

正式对话上传的 VLM 文案在 ai/api/router.py，不要从这里改。
这里只复刻那段原文，给对照的左边当「当前正式效果」。
右边的逐框确认、删无依据解释都是试验；模式确定之前不要写回 router.py。
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import time
from typing import Any, Dict, List, Optional

from ai.core.logging import get_logger

logger = get_logger("VISION_CHAT")

# 与 router.py 正式上传一致，只给对照左边复刻。试验不要改这段。
CHAT_UPLOAD_VISION_SYSTEM = (
    "你是 AGV/AMR 调度系统的图片转述员，只做客观转述、不做分析判断："
    "把画面上真实可见的内容（文字、数值、状态、错误码）逐字抄录成要点，"
    "总字数 ≤ 200 字。看不清的注明「（模糊）」，禁止编造画面上没有的信息，"
    "禁止推测故障原因。最后按 prompt 要求在「【回应】」行用一句话回应用户。"
)

# 第二遍：一块框一次，只回答有或无。不重写转述。
ATLAS_MATCH_SYSTEM = (
    "你只判断两张图是不是同一个控件。"
    "图1是用户现场图，图2是标准界面上裁出的一块，红框里是控件。"
    "只回答一个字：有 或 无。不要解释，不要转述画面，不要改写任何文字。"
)

# 模型爱安到别的数字上的业务词。不在抄录引号里、也不在对上的人话里，就删掉。
_MEANING_WORDS = (
    "缩放比例", "高亮区域", "当前位置", "小黄标", "完成率",
    "电量", "缩放", "高亮", "进度", "负载", "电池",
)


def build_chat_upload_vision_user_prompt(
    image_name: str = "user_shot.png",
    dialog_context: str = "",
    atlas_block: str = "",
) -> str:
    """复刻正式上传的用户 prompt。atlas_block 仅开发者试验可传，正式上传不传。"""
    vlm_context = ""
    ctx = (dialog_context or "").strip()
    if ctx:
        vlm_context = f"以下是最近的对话记录，供你理解图片背景：\n{ctx}\n"
    atlas = (atlas_block or "").strip()
    atlas_part = f"{atlas}\n" if atlas else ""
    return (
        f"分析图片 {image_name}。这是 AGV/AMR 调度系统的现场照片或界面截图。\n"
        f"{vlm_context}"
        f"{atlas_part}"
        f"请用**结构化要点**输出，总字数 ≤ 200 字：\n"
        f"- 画面类型（调度界面截图 / 设备现场照 / 文档表格 / 其他）\n"
        f"- 画面上可见的关键内容：界面名称/页面元素、文字标签、数值（含错误码、机器人ID）、"
        f"指示灯/设备状态——**逐字原样抄录，禁止推测补全**，看不清的写「（模糊）」\n"
        f"- 仅当画面上有明确的错误提示/告警标识时，原样转述该提示文字；画面正常就写「画面无报错提示」\n"
        f"要点之后另起一行写「【回应】」加一句话（≤40 字），结合画面要点和上面的对话背景：\n"
        f"- 背景已明确在排查什么 → 自然接话（如「这个数值确实不对，先记下了」）\n"
        f"- 看不出用户目的 → 一句话问清意图（要查图上的报错，还是问这个界面怎么操作，还是其他情况）\n"
        f"- 画面无报错且没有对话背景 → 问「这是遇到什么问题了，还是想了解这个界面的配置？」\n"
        f"要点部分禁止推测故障原因；回应部分不要给排查步骤、不要下诊断结论。"
    )


def _answered_regions(regions: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """已确认、且带归一化框的标注。最多 8 块，避免第二遍图太大。"""
    out: List[Dict[str, Any]] = []
    for r in regions or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "") != "answered":
            continue
        ans = str(r.get("answer") or "").strip()
        if not ans:
            continue
        try:
            x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if w <= 0.01 or h <= 0.01:
            continue
        out.append({"x": x, "y": y, "w": w, "h": h, "answer": ans[:80]})
        if len(out) >= 8:
            break
    return out


def _cjk_font(size: int):
    from PIL import ImageFont

    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def crop_atlas_region(standard_image_url: str, region: Dict[str, Any]) -> str:
    """标准图上的一块框裁出来，下方只写这一句人话。"""
    from PIL import Image, ImageDraw

    raw = str(standard_image_url or "").strip()
    if not raw.startswith("data:") or "," not in raw:
        raise ValueError("标准图需为 data URL")
    img = Image.open(io.BytesIO(base64.b64decode(raw.split(",", 1)[1]))).convert("RGB")
    iw, ih = img.size
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    pad_x = int(w * iw * 0.12)
    pad_y = int(h * ih * 0.12)
    left = max(0, int(x * iw) - pad_x)
    top = max(0, int(y * ih) - pad_y)
    right = min(iw, int((x + w) * iw) + pad_x)
    bottom = min(ih, int((y + h) * ih) + pad_y)
    if right - left < 8 or bottom - top < 8:
        raise ValueError("框太小")
    crop = img.crop((left, top, right, bottom))
    crop.thumbnail((480, 320))
    font = _cjk_font(18)
    cap_h = 48
    tile = Image.new("RGB", (max(crop.width, 240) + 8, crop.height + cap_h + 8), (255, 255, 255))
    draw = ImageDraw.Draw(tile)
    draw.rectangle((2, 2, crop.width + 5, crop.height + 5), outline=(207, 34, 46), width=3)
    tile.paste(crop, (4, 4))
    draw.text((8, crop.height + 10), str(region.get("answer") or "")[:40], fill=(31, 35, 40), font=font)
    buf = io.BytesIO()
    tile.save(buf, format="JPEG", quality=82, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def build_atlas_match_prompt(answer: str) -> str:
    """一块框一问：用户图里有没有这块控件。"""
    return (
        "图1是用户现场图。图2是标准界面上的一块控件（红框），"
        f"人工说明是：{answer}\n"
        "图1里能看到同一块控件吗？只回答「有」或「无」。"
    )


def verdict_is_match(text: str) -> bool:
    """模型只该回答有或无；含糊当无，避免对不上还采用说明。"""
    t = re.sub(r"\s+", "", text or "")
    t = t.strip("。．.！!，,")
    if not t:
        return False
    if t.startswith(("无", "没", "否", "不")):
        return False
    return t.startswith(("有", "是"))


def _quoted_text(text: str) -> str:
    return "\n".join(re.findall(r"[「“\"']([^」”\"']+)[」”\"']", text or ""))


def strip_ungrounded_meanings(text: str, extra: str = "", with_note: bool = False) -> str:
    """删掉没有依据的业务解释。

    依据只认两处：要点里用引号抄到的画面文字，以及逐框对上的那句人话。
    回应里整句删；要点里只删解释词，保留抄到的数字。
    """
    raw = text or ""
    if "【回应】" in raw:
        body, _, reply = raw.partition("【回应】")
    else:
        body, reply = raw, ""
    grounded = _quoted_text(body) + "\n" + (extra or "")
    dropped: List[str] = []
    for word in _MEANING_WORDS:
        if word in body and word not in grounded:
            body = body.replace(word, "")
            dropped.append(word)
    body = re.sub(r"[，,]{2,}", "，", body)
    body = re.sub(r"[“\"]\s*[”\"]", "", body)
    if reply:
        kept: List[str] = []
        for clause in re.split(r"(?<=[。！？\n])", reply):
            invented = [w for w in _MEANING_WORDS if w in clause and w not in grounded]
            if invented:
                dropped.extend(invented)
                continue
            kept.append(clause)
        reply = "".join(kept).strip()
    out = body.strip()
    if reply:
        out = f"{out}\n【回应】{reply}"
    if with_note and dropped:
        uniq = list(dict.fromkeys(dropped))
        out += "\n（已去掉没有依据的解释：" + "、".join(uniq) + "）"
    return out.strip()


def compose_atlas_side(transcript: str, matched: List[str]) -> str:
    """右边 = 第一遍转述（去掉无依据解释）+ 只贴对上的人话。模型不改写整段。"""
    body = strip_ungrounded_meanings(transcript, extra="\n".join(matched), with_note=True)
    if not matched:
        note = "（逐框对照：没有对上的控件，未加说明）"
    else:
        lines = "\n".join(f"- {a}" for a in matched)
        note = "图鉴对上的控件（只采用这些说明）：\n" + lines
    return f"{body}\n\n{note}"


def format_atlas_block_for_chat(
    product: str,
    iface_name: str,
    page_caption: Optional[str],
    regions: Optional[List[Dict[str, Any]]],
) -> str:
    """已确认说明的纯文本，只给对照页展示；不写入第一遍 prompt。"""
    lines: List[str] = [
        "【界面图鉴参考——只帮助你正确理解图上控件/状态的业务含义；"
        "画面上没有的内容禁止编造；仍以图上可见文字为准】",
        f"标准界面：{product or '?'} · {iface_name or '?'}",
    ]
    cap = (page_caption or "").strip()
    if cap:
        lines.append(f"整页说明：{cap}")
    answered = []
    for r in regions or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "") != "answered":
            continue
        ans = str(r.get("answer") or "").strip()
        if not ans:
            continue
        answered.append(f"- {ans}")
    if answered:
        lines.append("已确认难懂点含义：")
        lines.extend(answered[:12])
    else:
        lines.append("（该标准图尚无已确认标注，仅有界面名可参考）")
    return "\n".join(lines)


async def compare_chat_vlm_with_atlas(
    image_url: str,
    *,
    product: str = "",
    iface_name: str = "",
    page_caption: Optional[str] = None,
    regions: Optional[List[Dict[str, Any]]] = None,
    dialog_context: str = "",
    image_name: str = "similar_shot.png",
    standard_image_url: str = "",
) -> Dict[str, Any]:
    """左：正式对话第一遍只抄画面。右：同一段转述，再用标准图裁剪框对照「不明」。"""
    if not image_url or not str(image_url).strip():
        raise ValueError("image_url 不能为空")

    from ai.core import get_llm_client

    user_image = str(image_url).strip()
    answered = _answered_regions(regions)
    atlas_block = format_atlas_block_for_chat(product, iface_name, page_caption, regions)
    prompt_plain = build_chat_upload_vision_user_prompt(
        image_name=image_name, dialog_context=dialog_context, atlas_block="",
    )
    llm = await get_llm_client()

    async def _one(
        prompt: str,
        images: List[str],
        label: str,
        system_prompt: str,
        max_tokens: int = 800,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            text = await llm.complete_vision(
                prompt=prompt,
                images=images,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=0.3,
            )
            text = (text or "").strip()
            return {
                "ok": True,
                "text": text,
                "ms": int((time.perf_counter() - t0) * 1000),
                "label": label,
            }
        except Exception as e:
            logger.exception("compare chat vlm %s failed: %s", label, e)
            return {
                "ok": False,
                "text": f"（失败）{e}",
                "ms": int((time.perf_counter() - t0) * 1000),
                "label": label,
            }

    without = await _one(
        prompt_plain, [user_image], "不带图鉴（当前正式对话效果）", CHAT_UPLOAD_VISION_SYSTEM,
    )
    raw_transcript = without.get("text") or ""
    if not without.get("ok"):
        with_atlas = {
            "ok": False,
            "text": "第一遍转述失败，未做图鉴对照",
            "ms": 0,
            "label": "带图鉴（逐框对照）",
        }
    elif not str(standard_image_url or "").strip() or not answered:
        with_atlas = {
            "ok": True,
            "text": (without.get("text") or "") + "\n\n（没有标准图或已确认的框，无法对照）",
            "ms": 0,
            "label": "带图鉴（逐框对照，未触发）",
        }
    else:
        standard = str(standard_image_url)

        async def _ask(region: Dict[str, Any]) -> Optional[str]:
            try:
                crop = crop_atlas_region(standard, region)
            except Exception as e:
                logger.warning("图鉴单框裁剪失败: %s", e)
                return None
            verdict = await _one(
                build_atlas_match_prompt(region["answer"]),
                [user_image, crop],
                "图鉴单框",
                ATLAS_MATCH_SYSTEM,
                max_tokens=16,
            )
            if verdict.get("ok") and verdict_is_match(verdict.get("text") or ""):
                return str(region["answer"])
            return None

        t0 = time.perf_counter()
        hits = await asyncio.gather(*[_ask(r) for r in answered])
        matched = [h for h in hits if h]
        with_atlas = {
            "ok": True,
            "text": compose_atlas_side(raw_transcript, matched),
            "ms": int((time.perf_counter() - t0) * 1000),
            "label": f"带图鉴（逐框对照，对上 {len(matched)}/{len(answered)}）",
        }
    return {
        "without_atlas": without,
        "with_atlas": with_atlas,
        "atlas_block": atlas_block,
        "system_prompt": CHAT_UPLOAD_VISION_SYSTEM,
    }
