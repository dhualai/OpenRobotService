# 路径: ai/core/chat_snapshot.py
"""
聊天记录截图附件生成 — 把 AI 诊断对话渲染成一张聊天气泡风格 PNG，
上传 MinIO 后返回 attachments 元素（{path, filename, size}），
随工单入库，前端详情页以缩略图展示、点击灯箱放大。

设计纪律：
1. render_chat_snapshot 纯同步绘制（不碰网络/DB），便于单测。
2. create_chat_snapshot_attachment 惰性 import backend 依赖，整个函数
   try/except —— 任何失败（无字体 / Pillow 缺失 / MinIO 不可达）返回 None，
   绝不阻塞提单主流程。
3. 中文逐字符 getlength 换行，CJK/ASCII 混排精确，不靠宽度估算。
"""
import io
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 字体加载：优先项目自带字体（不依赖服务器装了啥），再回退系统路径 ----
# 部署环境是 Linux，之前只写 Windows 字体路径 → 全找不到 → 回退 load_default()
# 纯 ASCII 字体 → 中文渲染成豆腐块乱码。改为把 msyh.ttc 打包进项目 ai/assets/fonts/，
# 任何环境都能加载；系统候选仅作补充兜底。
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_CANDIDATES = [
    str(_FONT_DIR / "msyh.ttc"),          # 项目自带 微软雅黑（首选）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # Linux Noto CJK
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",  # Linux Droid
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",              # Linux 文泉驿正黑
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",            # Linux 文泉驿微米黑
    "/usr/share/fonts/truetype/arphic/uming.ttc",                # Linux AR PL 明宋
    "C:/Windows/Fonts/msyh.ttc",          # Windows 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",        # Windows 黑体
]
_FONT_BOLD_CANDIDATES = [
    str(_FONT_DIR / "msyh.ttc"),          # 项目自带微软雅黑（粗体时 Pillow 自动合成加粗效果有限，可用）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",       # Linux Noto CJK 粗体
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",        # Windows 微软雅黑粗体
    "C:/Windows/Fonts/simhei.ttf",
]


def _load_font(candidates, size: int):
    """加载第一个存在的字体文件；全失败则用 Pillow 内置默认字体。"""
    from PIL import ImageFont
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# 绘制尺寸常量
_BG_COLOR = (245, 246, 247)          # 浅灰背景
_HEADER_BG = (34, 34, 34)            # 顶部标题条深色
_HEADER_TEXT = (255, 255, 255)
_USER_BUBBLE = (149, 236, 105)       # 用户气泡绿色（靠右）
_ASSIST_BUBBLE = (255, 255, 255)     # 助手气泡白色（靠左）
_TEXT_COLOR = (34, 34, 34)
_LABEL_USER = (58, 150, 20)          # 用户名标签
_LABEL_ASSIST = (120, 130, 140)      # 助手名标签
_IMAGE_BOX_BG = (228, 230, 232)      # 图片描述浅灰虚线框
_IMAGE_BOX_TEXT = (130, 140, 150)

_PADDING = 24          # 画布左右留白
_BUBBLE_PAD_X = 14     # 气泡内左右留白
_BUBBLE_PAD_Y = 10     # 气泡内上下留白
_BUBBLE_GAP = 14       # 相邻气泡间距
_LABEL_GAP = 4         # 名字标签与气泡间距
_RADIUS = 10           # 气泡圆角
_MAX_TURN_CHARS = 500  # 单轮内容截断字数
_MAX_HEIGHT = 16000    # 画布最大高度（超长对话截断兜底）


def _wrap_text(font, text: str, max_width: int) -> List[str]:
    """按像素宽度逐字符换行：CJK/ASCII 混排用 getlength 精确累加。"""
    if not text:
        return [""]
    lines: List[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if font.getlength(cur + ch) > max_width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def render_chat_snapshot(
    turns: List[Dict[str, str]],
    title: str = "AI 诊断对话记录",
    session_id: str = "",
    width: int = 760,
    max_height: int = _MAX_HEIGHT,
) -> bytes:
    """把对话轮次渲染成聊天气泡 PNG，返回 PNG bytes。

    turns: [{"role": "user"|"assistant", "content": "..."}, ...]
    role 非 user 一律当助手处理。
    """
    from PIL import Image, ImageDraw

    font = _load_font(_FONT_CANDIDATES, 16)
    font_bold = _load_font(_FONT_BOLD_CANDIDATES, 16)
    font_small = _load_font(_FONT_CANDIDATES, 13)
    font_tail = _load_font(_FONT_CANDIDATES, 14)

    inner_w = width - 2 * _PADDING
    max_bubble_w = inner_w - 2 * _BUBBLE_PAD_X - 60  # 留出气泡两侧不对称空间

    # ── 第一遍：计算每轮气泡高度，决定画布总高 / 是否截断 ──
    rows: List[dict] = []
    total_h = 64  # 顶部标题条高度
    truncated = False
    for turn in turns[-10:]:
        # role 归一化：历史数据可能存过大写 USER/ASSISTANT，不区分大小写判断
        role = (turn.get("role") or "user").lower()
        is_user = role == "user"
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if len(content) > _MAX_TURN_CHARS:
            content = content[:_MAX_TURN_CHARS] + "…"
        is_image = ("图片主要内容为" in content) or content.startswith("![")
        if is_image:
            content = "[图片] " + content
        lines = _wrap_text(font_tail, content, max_bubble_w)
        line_h = int(font_tail.size * 1.5)
        text_h = line_h * len(lines)
        bubble_h = text_h + 2 * _BUBBLE_PAD_Y
        row_h = 18 + _LABEL_GAP + bubble_h + _BUBBLE_GAP  # 名字标签行 + 气泡 + 间距
        if total_h + row_h > max_height:
            truncated = True
            break
        rows.append({
            "is_user": is_user,
            "lines": lines,
            "bubble_h": bubble_h,
            "row_h": row_h,
        })
        total_h += row_h

    # 底部加截断提示行
    if truncated:
        total_h += 40
    total_h += 24

    img = Image.new("RGB", (width, total_h), _BG_COLOR)
    draw = ImageDraw.Draw(img)

    # ── 顶部标题条 ──
    draw.rectangle([0, 0, width, 56], fill=_HEADER_BG)
    head_title = title[:28] if title else "AI 诊断对话记录"
    draw.text((_PADDING, 16), head_title, font=font_bold, fill=_HEADER_TEXT)
    if session_id:
        tail = session_id[-8:]
        draw.text((_PADDING, 16), "  " + tail, font=font_small, fill=(180, 180, 180))

    y = 72
    for row in rows:
        is_user = row["is_user"]
        lines = row["lines"]
        bubble_h = row["bubble_h"]

        # 名字标签
        label = "用户" if is_user else "助手"
        label_color = _LABEL_USER if is_user else _LABEL_ASSIST
        draw.text((_PADDING, y), label, font=font_small, fill=label_color)
        label_bottom = y + font_small.size

        # 气泡矩形
        bubble_top = label_bottom + _LABEL_GAP
        if is_user:
            x0 = width - _PADDING - max_bubble_w - 2 * _BUBBLE_PAD_X
        else:
            x0 = _PADDING
        x1 = x0 + 2 * _BUBBLE_PAD_X + max_bubble_w
        bubble_bottom = bubble_top + bubble_h
        draw.rounded_rectangle(
            [x0, bubble_top, x1, bubble_bottom], radius=_RADIUS, fill=_USER_BUBBLE if is_user else _ASSIST_BUBBLE,
        )

        # 文字（气泡内左侧起点）
        tx = x0 + _BUBBLE_PAD_X
        ty = bubble_top + _BUBBLE_PAD_Y
        line_h = int(font_tail.size * 1.5)
        for line in lines:
            draw.text((tx, ty), line, font=font_tail, fill=_TEXT_COLOR)
            ty += line_h

        y += row["row_h"]

    if truncated:
        draw.text((_PADDING, y), "…对话过长已截断", font=font_small, fill=(150, 155, 160))
        y += 40

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def create_chat_snapshot_attachment(
    session_id: str,
    turns: List[Dict[str, str]],
    title: str = "",
) -> Optional[dict]:
    """生成对话截图并上传 MinIO，返回 attachments 元素 {path, filename, size}。

    任何失败都返回 None（不抛异常）——截图是锦上添花，不能拖垮提单主流程。
    """
    try:
        import time as _t
        from app.utils.minio_client import minio_client
        from app.core.config import settings
        from PIL import Image  # noqa: F401  提前触发 Pillow 缺失报错，走统一降级

        png_bytes = render_chat_snapshot(
            turns,
            title=title or "AI 诊断对话记录",
            session_id=session_id,
        )
        if not png_bytes:
            return None

        bucket = settings.COMMENT_BUCKET
        object_path = f"{bucket}/chat_snapshots/{session_id}/{int(_t.time())}.png"
        ok = minio_client.upload_bytes(
            file_bytes=png_bytes,
            object_path=object_path,
            content_type="image/png",
        )
        if not ok:
            logger.warning(f"[chat_snapshot] MinIO 上传失败，降级无截图: session={session_id}")
            return None

        fname = "对话记录_" + _t.strftime("%Y%m%d") + ".png"
        logger.info(f"[chat_snapshot] 截图已上传: {object_path} ({len(png_bytes)}B)")
        return {"path": object_path, "filename": fname, "size": len(png_bytes)}
    except Exception as e:
        logger.warning(f"[chat_snapshot] 生成失败，降级无截图: session={session_id}, err={e}")
        return None
