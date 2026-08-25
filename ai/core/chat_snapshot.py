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
import base64
import io
import logging
import re
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


def _fmt_created_at(iso: str) -> str:
    """iso 字符串 → 'MM-DD HH:MM'；解析失败返回空串（时间戳省略显示）。"""
    if not iso:
        return ""
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
    except Exception:
        return ""


# 内嵌图片总大小上限（base64 累计字符数）：防止附件 md 膨胀过大
_EMBED_BUDGET = 2 * 1024 * 1024

_IMG_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
}


def _turns_to_markdown(
    turns: List[Dict[str, str]],
    title: str = "AI 诊断对话记录",
    session_id: str = "",
    user_images: Optional[List[dict]] = None,
    img_budget: int = _EMBED_BUDGET,
) -> tuple:
    """把对话 turns 转录成 Markdown 文本（工单附件用，可复制/可检索/可被下游解析）。

    格式：每轮以水平分隔线隔开，角色粗体 + emoji 前缀（附件预览是 ReactMarkdown
    渲染，分隔线/粗体/emoji 均生效，用户/AI 视觉区分明显）：

        ---

        👤 **【用户】** 08-25 10:30

        内容

    created_at 仅 MySQL 源的 turn 有（memory.turns 没有），缺失时省略时间戳。
    相邻内容完全相同的 turn 视为重复记录（上传回执等），只保留一条。
    图片描述（「我上传了 N 个文件：…」）原样保留在用户消息里。

    user_images：_prepare_user_images 的产物（已下载的图片 bytes）。上传轮的
    content 含文件名（router 写入「我上传了 N 个文件：['x.jpg']…」），按文件名
    匹配把原图 base64 内联在该轮内容后面——图片出现在它被发送的对话位置；
    匹配不到的条目（上传轮被窗口截掉等）追加末尾「对话中的图片」节（含 desc
    引用）兜底。返回 (md 文本, 剩余图片预算)——预算与 KB 图内嵌共享接力。
    """
    pending = {e["filename"]: e for e in (user_images or [])}
    lines = [f"# {title}", ""]
    _prev = None
    for turn in turns:
        role = (turn.get("role") or "user").lower()
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if _prev == (role, content):
            continue  # 相邻重复（如上传后的重复回执），跳过
        _prev = (role, content)
        if role == "assistant":
            tag = "🤖 **【U老师】**"
        else:
            tag = "👤 **【用户】**"
        header = f"{tag} {_fmt_created_at(turn.get('created_at', ''))}".rstrip()
        lines.append("---")
        lines.append("")
        lines.append(header)
        lines.append("")
        lines.append(content)
        # 用户上传的图片内联：content 含文件名（「我上传了…['x.jpg']」）→ 原图随轮展示
        if role != "assistant" and pending:
            hit = [fn for fn in list(pending) if fn and fn in content]
            for fn in hit:
                img_md, img_budget = _render_image_md(pending.pop(fn), img_budget)
                lines.append("")
                lines.append(img_md)
        lines.append("")
    if len(lines) > 2:
        lines.append("---")
        lines.append("")
    # 兜底：没匹配到上传轮的条目（窗口截掉/改名）放末尾节，desc 引用保留现场描述
    if pending:
        parts = []
        for entry in pending.values():
            img_md, img_budget = _render_image_md(entry, img_budget, with_desc=True)
            parts.append(img_md)
        lines.append("## 📷 对话中的图片")
        lines.append("")
        lines.append("\n\n---\n\n".join(parts))
        lines.append("")
    if user_images:
        _ok = sum(1 for e in user_images if e.get("data"))
        logger.info(f"[chat_markdown] 用户图片内嵌 {_ok}/{len(user_images)} 张（内联对话位置优先）")
    return "\n".join(lines).rstrip() + "\n", img_budget


def _embed_kb_images(md_text: str, budget: int = _EMBED_BUDGET) -> tuple:
    """把 md 里的 KB 图片 URL（{media_url_prefix}/kb/.../media/x.png）替换为
    base64 data URL——附件 md 自包含，脱离 AI 服务（backend 前端预览 / 下载
    本地打开 / 转发）都能渲染，不再裂图。

    文件缺失/读取失败保留原 URL 降级；累计内嵌体积超预算后剩余图片保留原 URL。
    只影响附件 md，不改 turns 原文。返回 (新文本, 剩余预算)——预算与用户上传
    图片内嵌共享（_append_user_images 接力消耗同一额度）。
    """
    try:
        from ai.config import get_ai_config, _KB_DIR
        prefix = get_ai_config().media_url_prefix.rstrip("/")
    except Exception:
        return md_text, budget

    pat = re.compile(
        r'!\[([^\]]*)\]\(' + re.escape(prefix) + r'/kb/([^/]+)/([^)]*?)/media/([^)/]+\.\w+)\)')
    skipped = 0

    def _repl(m):
        nonlocal budget, skipped
        alt, domain, sub, fname = m.group(1), m.group(2), m.group(3), m.group(4)
        local = _KB_DIR / domain / sub / "media" / fname
        ext = local.suffix.lstrip(".").lower()
        mime = _IMG_MIME.get(ext)
        if not mime:
            return m.group(0)
        try:
            data = local.read_bytes()
        except Exception:
            return m.group(0)  # 文件不存在等：保留原 URL 降级
        b64 = base64.b64encode(data).decode("ascii")
        if len(b64) > budget:
            skipped += 1
            return m.group(0)
        budget -= len(b64)
        return f'![{alt}](data:{mime};base64,{b64})'

    result = pat.sub(_repl, md_text)
    if skipped:
        logger.info(f"[chat_markdown] 内嵌图片超预算降级保留原URL: {skipped} 张")
    return result, budget


_USER_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def is_image_entry(entry: dict) -> bool:
    """附件条目是否是图片（按 filename/object_path 扩展名判断）。"""
    name = (entry.get("filename") or entry.get("object_path") or "").lower()
    return Path(name).suffix in _USER_IMG_EXTS


def _prepare_user_images(entries: List[dict]) -> List[dict]:
    """下载用户上传图片条目（MinIO → bytes），返回
    [{filename, desc, mime, data(bytes|None)}]——data=None 表示下载失败，
    渲染时降级为文字。同步函数（fget_object 阻塞），调用方用 to_thread 包。
    """
    if not entries:
        return []
    try:
        import tempfile
        from ai.core.minio_client import minio_client
    except Exception:
        return []

    prepared: List[dict] = []
    for e in entries:
        object_path = (e.get("object_path") or "").strip()
        fname = e.get("filename") or object_path.rsplit("/", 1)[-1] or "图片"
        ext = Path(fname).suffix.lstrip(".").lower()
        mime = _IMG_MIME.get(ext, "image/jpeg")
        data = None
        if object_path:
            try:
                with tempfile.TemporaryDirectory() as td:
                    local = Path(td) / "img"
                    if minio_client.fget_object(object_path, str(local)):
                        data = local.read_bytes()
            except Exception:
                data = None
        prepared.append({
            "filename": fname,
            "desc": (e.get("desc") or "").strip(),
            "mime": mime,
            "data": data,
        })
    return prepared


def _render_image_md(entry: dict, budget: int, with_desc: bool = False) -> tuple:
    """单张已下载图片 → (md 片段, 剩余预算)。内联成功扣预算；下载失败/超预算
    降级为文件名说明文字（不阻塞其余图片）。with_desc 时附 desc 引用块
    （仅末尾兜底节用——内联位置的 desc 已在用户消息 content 里，不重复）。"""
    fname = entry["filename"]
    data = entry.get("data")
    if data:
        b64 = base64.b64encode(data).decode("ascii")
        if len(b64) > budget:
            return f"**{fname}**（图片过大未内嵌，见工单附件）", budget
        desc_line = f"\n\n> {entry['desc']}" if with_desc and entry.get("desc") else ""
        prefix = f"**{fname}**{desc_line}\n\n" if with_desc else ""
        return f"{prefix}![{fname}](data:{entry['mime']};base64,{b64})", budget - len(b64)
    return f"**{fname}**（图片下载失败，见工单附件）", budget


async def create_chat_markdown_attachment(
    session_id: str,
    turns: List[Dict[str, str]],
    title: str = "",
    user_images: Optional[List[dict]] = None,
) -> Optional[dict]:
    """生成对话记录 Markdown 文档并上传 MinIO，返回 attachments 元素 {path, filename, size}。

    替代旧版 PNG 截图附件：md 可复制、可检索、文件更小，且下游系统可直接解析。
    user_images: 本单周期内用户上传的图片附件条目（object_path/filename/desc），
    先从 MinIO 下载（to_thread），再按上传轮内联到 md 对应位置（见
    _turns_to_markdown 的 user_images 参数说明）。
    任何失败都返回 None（不抛异常）——附件是锦上添花，不能拖垮提单主流程。
    """
    try:
        import asyncio
        import time as _t
        from ai.core.minio_client import minio_client

        prepared = []
        if user_images:
            prepared = await asyncio.to_thread(_prepare_user_images, user_images)
        md_text, budget = _turns_to_markdown(
            turns,
            title=title or "AI 诊断对话记录",
            session_id=session_id,
            user_images=prepared,
        )
        if not md_text.strip():
            return None
        md_text, _ = _embed_kb_images(md_text, budget)
        md_bytes = md_text.encode("utf-8")

        # AI 独立进程不能 import backend 的 app.utils.minio_client，
        # 用 AI 自己的 minio_client；bucket 与 backend settings.COMMENT_BUCKET 对齐
        # （附件统一放 helpdesk-comment 桶）。
        bucket = "helpdesk-comment"
        object_path = f"{bucket}/chat_records/{session_id}/{int(_t.time())}.md"
        ok = minio_client.upload_bytes(
            file_bytes=md_bytes,
            object_path=object_path,
            content_type="text/markdown; charset=utf-8",
        )
        if not ok:
            logger.warning(f"[chat_markdown] MinIO 上传失败，降级无附件: session={session_id}")
            return None

        fname = "对话记录_" + _t.strftime("%Y%m%d") + ".md"
        logger.info(f"[chat_markdown] 对话记录已上传: {object_path} ({len(md_bytes)}B)")
        return {"path": object_path, "filename": fname, "size": len(md_bytes)}
    except Exception as e:
        logger.warning(f"[chat_markdown] 生成失败，降级无附件: session={session_id}, err={e}")
        return None


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
        from ai.core.minio_client import minio_client
        from PIL import Image  # noqa: F401  提前触发 Pillow 缺失报错，走统一降级

        png_bytes = render_chat_snapshot(
            turns,
            title=title or "AI 诊断对话记录",
            session_id=session_id,
        )
        if not png_bytes:
            return None

        # AI 独立进程用 ai.core.minio_client；bucket 与 backend COMMENT_BUCKET 对齐
        bucket = "helpdesk-comment"
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
