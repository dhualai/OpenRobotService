#!/usr/bin/env python3
"""部署通知卡片图渲染（企业微信「群机器人 image 消息」用）。

由 `notify.py` 在 `NOTIFY_STYLE=image` 时调用：把 `collect()` 的上下文渲染成一张
白底卡片 PNG，再以 base64 + md5 发到群里（企微 image 消息不支持跳转，因此
`notify.py` 会紧接着补一条只含 Actions 链接的文本消息）。

设计取舍：
- 图片宽度 960、按 2x 缩放输出，缩略图里字比企微模板卡片大 2~3 倍，且可点开看原图；
- 图标（勾/叉）用绘图 API 自绘，不依赖彩色 emoji 字体，Linux runner 同样可用；
- 字体优先用随包携带的子集（Noto Sans SC，OFL 许可证），找不到再退回系统字体；
  全部找不到时抛 `NotifyImageError`，由 notify.py 降级回模板卡片，保证通知不丢。

版面（由 notify.py 按 `ctx["layout"]` 选择）：
- standard     默认：状态 + 引用块 + 键值对（test 环境与无发布说明场景）
- release      生产发布成功：环境+日期头 + 分组更新日志（简约风，纯文字组标题 + 小黑点）
- failure-prod 生产发布失败：红色状态 + 告警条 + 编号建议操作 + 键值对
"""
import io
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W = 960
PAD = 48
BG = "#FFFFFF"
INK = "#1F2329"
MID = "#4E5969"
SUB = "#8A9099"
LINE = "#E6E8EB"
GREEN = "#07C160"
RED = "#F5222D"
ORANGE = "#FA8C16"

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# CI 侧 COMPONENTS 只会是 all / frontend / backend / ai（workflow 已做白名单校验）
COMPONENT_LABELS = {
    "all": "frontend + backend + AI",
    "frontend": "frontend",
    "backend": "backend",
    "ai": "AI",
}

# 提交/PR 标题里的 conventional 前缀对群里读者是噪音，展示时剥掉
CONVENTIONAL = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s*")


def _font_candidates():
    """(常规, 粗体) 候选对，按优先级排列；环境变量可覆盖。"""
    env_reg = os.environ.get("NOTIFY_FONT_REG", "").strip()
    env_bold = os.environ.get("NOTIFY_FONT_BOLD", "").strip()
    return [
        (env_reg, env_bold),
        (str(FONT_DIR / "NotoSansSC-Regular.ttf"), str(FONT_DIR / "NotoSansSC-Bold.ttf")),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
         "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    ]


def pick_fonts():
    """返回 (常规字体路径, 粗体字体路径或 None)。"""
    for regular, bold in _font_candidates():
        if regular and Path(regular).is_file():
            bold_path = bold if (bold and Path(bold).is_file()) else None
            return regular, bold_path
    raise NotifyImageError("未找到可用的中文字体（可用 NOTIFY_FONT_REG / NOTIFY_FONT_BOLD 指定）")


class NotifyImageError(RuntimeError):
    """图片渲染不可用（缺依赖 / 缺字体 / 尺寸超限），上游据此降级。"""


def component_text(raw):
    """`backend,ai` → `backend + AI`；未知取值原样保留。"""
    parts = [p for p in re.split(r"[,;\s]+", (raw or "all").strip()) if p]
    labels = []
    for part in parts or ["all"]:
        label = COMPONENT_LABELS.get(part, part)
        if label not in labels:
            labels.append(label)
    return " + ".join(labels)


def clean_title(text):
    """剥掉 `feat(ORS-907):` 这类提交前缀；剥空了就退回原文。"""
    text = " ".join((text or "").split())
    return CONVENTIONAL.sub("", text) or text


def quote_text(ctx):
    """卡片引用块文案：优先 PR 标题，拿不到时退回提交标题。

    回滚场景没有 PR 语义，且分支最新提交与「回滚到哪份备份」无关，因此不用提交标题硬凑。
    """
    quote = clean_title(ctx.get("pr_title") or "")
    if not quote and "回滚" not in (ctx.get("title") or ""):
        quote = clean_title(ctx.get("commit_msg") or "")
    return quote


def _dashed(draw, y, x0, x1, color=LINE, dash=10, gap=8):
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=color, width=2)
        x += dash + gap


def _wrap(draw, text, fnt, max_w):
    """按空格优先断行，单词本身超宽时再按字符硬切。"""
    lines, cur = [], ""
    for word in text.split(" "):
        candidate = f"{cur} {word}".strip()
        if draw.textlength(candidate, font=fnt) <= max_w:
            cur = candidate
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if draw.textlength(cur + ch, font=fnt) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    return lines


def _head_lines(draw, text, fnt, max_w, max_lines=2):
    lines = _wrap(draw, text, fnt, max_w)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        tail = lines[-1]
        while tail and draw.textlength(tail + "…", font=fnt) > max_w:
            tail = tail[:-1]
        lines[-1] = tail + "…"
    return lines


def _rows(ctx):
    """卡片键值对：告警优先，最长 5 行，避免整图太高。"""
    rows = [("部署组件", component_text(ctx.get("components")), INK)]
    if ctx.get("git_ref"):
        rows.append(("代码分支", f"{ctx['git_ref']} @ {ctx.get('sha', '')}".strip(), INK))
    if ctx.get("skip_gate"):
        rows.append(("测试门禁", "已跳过（紧急发布）", ORANGE))
    elif ctx.get("gate_failed"):
        rows.append(("测试门禁", "有失败用例（非阻塞放行）", ORANGE))
    if ctx.get("auto_rollback"):
        rows.append(("自动回滚", "已触发，请人工确认服务", ORANGE))
    if ctx.get("backup_id"):
        rows.append(("回滚目标", str(ctx["backup_id"]), INK))
    if ctx.get("elapsed"):
        rows.append(("流水线耗时", str(ctx["elapsed"]), INK))
    return rows[:5]


def _load_fonts():
    """返回 (font 工厂, bold_stroke)；没有粗体文件时用描边模拟加粗。"""
    regular_path, bold_path = pick_fonts()

    def font(size, bold=False):
        return ImageFont.truetype(bold_path if (bold and bold_path) else regular_path, size)

    return font, (0 if bold_path else 2)


def _canvas():
    """预留足够高度的画布与画笔，画完由 `_finish` 按内容裁剪。"""
    img = Image.new("RGB", (W, 6000), BG)
    return img, ImageDraw.Draw(img)


def _head_row(d, y, f_head, left):
    """顶部标题行（最多 2 行）+ 虚线分隔，返回下一段起始 y。"""
    for line in _head_lines(d, left, f_head, W - 2 * PAD, 2):
        d.text((PAD, y), line, font=f_head, fill=MID)
        y += 34
    y += 10
    _dashed(d, y, PAD, W - PAD)
    return y + 40


def _status_row(d, y, f_title, f_desc, bold_stroke, ok, state, sub):
    """状态方块（自绘勾/叉）+ 大字状态 + 副标题 + 分隔线，返回下一段起始 y。"""
    box = 68
    color = GREEN if ok else RED
    d.rounded_rectangle([PAD, y, PAD + box, y + box], radius=16, fill=color)
    if ok:
        d.line([(PAD + 17, y + 35), (PAD + 30, y + 48)], fill="#FFFFFF", width=7)
        d.line([(PAD + 30, y + 48), (PAD + 52, y + 15)], fill="#FFFFFF", width=7)
    else:
        d.line([(PAD + 20, y + 20), (PAD + 48, y + 48)], fill="#FFFFFF", width=7)
        d.line([(PAD + 48, y + 20), (PAD + 20, y + 48)], fill="#FFFFFF", width=7)

    tx = PAD + box + 24
    d.text((tx, y + 2), state, font=f_title, fill=INK,
           stroke_width=bold_stroke, stroke_fill=INK)
    if sub:
        d.text((tx, y + box), sub, font=f_desc, fill=SUB)
    y += box + 50
    d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
    return y + 30


def _link_row(d, y, f_link, color):
    """底部「查看运行日志」行；图片不可点，真实链接由随后的文本消息给出。"""
    y += 4
    d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
    y += 22
    d.text((PAD, y), "查看运行日志", font=f_link, fill=color)
    d.text((W - PAD - 16, y - 2), ">", font=f_link, fill=color)
    return y + 46


def _finish(img, y):
    """裁剪到内容高度、2x 放大并做体积校验（企微 image 上限 2MB）。"""
    if y > img.height:
        raise NotifyImageError(f"内容过高（{y} > {img.height}），改用其他样式")
    img = img.crop((0, 0, W, y))
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)   # 2x 高清
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    if len(data) > 2 * 1024 * 1024:           # 企微 image 上限 2MB
        raise NotifyImageError(f"渲染结果过大（{len(data)} 字节），改用其他样式")
    return data


def _sub_text(ctx):
    """状态行副标题：操作人 · 触发方式。"""
    return " · ".join(x for x in (ctx.get("actor") or "", ctx.get("event") or "") if x)


def render_png(ctx):
    """按 ctx["layout"] 分派版面，返回 PNG 字节。

    layout:
      release      生产发布成功且带发布说明（见 `_render_release`）
      failure-prod 生产发布失败（见 `_render_failure`）
      standard     默认版面（test / 无发布说明 / 未识别取值）
    """
    layout = ctx.get("layout") or "standard"
    if layout == "release":
        return _render_release(ctx)
    if layout == "failure-prod":
        return _render_failure(ctx)
    return _render_standard(ctx)


def _render_standard(ctx):
    """默认版面：状态 + 引用块（最近一条 PR/提交）+ 键值对。"""
    ok = bool(ctx.get("ok"))
    color = GREEN if ok else RED
    state = ctx.get("title") or ("部署成功" if ok else "部署失败")

    env_name = ctx.get("env_name") or "-"
    pr_no = str(ctx.get("pr_no") or "").strip()
    headline = f"#{pr_no} · {env_name} 环境" if pr_no else f"{env_name} 环境"

    quote = quote_text(ctx)

    sub = _sub_text(ctx)

    font, bold_stroke = _load_fonts()

    f_head = font(23)
    f_title = font(48, bold=True)
    f_desc = font(25)
    f_quote = font(29)
    f_label = font(24)
    f_value = font(26)
    f_link = font(27)

    rows = _rows(ctx)
    value_x = PAD + 300
    line_h, row_gap = 38, 26

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    row_lines = [_wrap(probe, v, f_value, W - PAD - value_x) for _, v, _ in rows]

    img, d = _canvas()
    y = PAD

    # 顶部：PR 号 + 环境（拿不到 PR 号时只显示环境）
    y = _head_row(d, y, f_head, headline)

    y = _status_row(d, y, f_title, f_desc, bold_stroke, ok, state, sub)

    # 引用块：本次部署带上了什么（左竖线 + 标题）
    if quote:
        lines = _head_lines(d, quote, f_quote, W - 2 * PAD - 28, 2)
        block_h = 40 * len(lines) + 10
        d.rectangle([PAD, y, PAD + 6, y + block_h], fill=color)
        for i, line in enumerate(lines):
            d.text((PAD + 26, y + i * 40), line, font=f_quote, fill=INK)
        y += block_h + 30
        d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
        y += 30

    # 键值对
    for (key, _, value_color), lines in zip(rows, row_lines):
        d.text((PAD, y + 4), key, font=f_label, fill=SUB)
        for i, line in enumerate(lines):
            d.text((value_x, y + i * line_h), line, font=f_value, fill=value_color)
        y += len(lines) * line_h + row_gap

    # 底部跳转提示（图片不可点，真实链接由随后的一条文本消息给出）
    if ctx.get("run_url"):
        y = _link_row(d, y, f_link, color)
    y += PAD
    return _finish(img, y)


def _render_release(ctx):
    """生产发布成功：环境+日期头 + 大字成功 + 分组更新日志。

    简约风（对齐 CodeBuddy 更新日志）：纯文字组标题、小黑点 bullet，无图标；
    不显示版本号，只显示日期。
    """
    font, bold_stroke = _load_fonts()
    f_head = font(23, bold=True)
    f_head_r = font(23)
    f_title = font(48, bold=True)
    f_desc = font(25)
    f_sec = font(30, bold=True)
    f_grp = font(27, bold=True)
    f_item = font(25)
    f_meta = font(23)
    f_link = font(27)

    img, d = _canvas()
    y = PAD

    # 头部：环境 + 日期（不显示版本号）
    env_label = ctx.get("env_label") or ctx.get("env_name") or "-"
    left = f"{env_label}环境发布说明"
    date = ctx.get("release_date") or ""
    d.text((PAD, y), left, font=f_head, fill=INK)
    if date:
        d.text((W - PAD - d.textlength(date, font=f_head_r), y), date, font=f_head_r, fill=SUB)
    y += 36
    _dashed(d, y, PAD, W - PAD)
    y += 40

    y = _status_row(d, y, f_title, f_desc, bold_stroke, True,
                    ctx.get("title") or "部署成功", _sub_text(ctx))

    # 更新日志区：标题 + 右侧条目计数
    groups = ctx.get("release_notes") or []
    d.text((PAD, y), "更新日志", font=f_sec, fill=INK)
    hint = f"共 {sum(len(items) for _, items in groups)} 项更新"
    d.text((W - PAD - d.textlength(hint, font=f_meta), y + 6), hint, font=f_meta, fill=SUB)
    y += 62

    indent = 26                      # 条目整体右移：组标题贴左边距，层级一眼可辨
    text_w = W - 2 * PAD - indent
    for gi, (title, items) in enumerate(groups):
        d.text((PAD, y), title, font=f_grp, fill=INK)
        y += 50
        for item in items:
            lines = _wrap(d, item, f_item, text_w)
            for li, line in enumerate(lines):
                if li == 0:
                    d.ellipse([PAD + 4, y + 17, PAD + 10, y + 23], fill=INK)   # 6px 小黑点
                d.text((PAD + indent, y), line, font=f_item, fill=MID if li else INK)
                y += 40
            y += 10
        if gi < len(groups) - 1:
            y += 22

    y += 18
    d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
    y += 32

    meta = " · ".join(x for x in (
        f"部署组件 {component_text(ctx.get('components'))}",
        f"分支 {ctx['git_ref']} @ {ctx.get('sha', '')}".strip() if ctx.get("git_ref") else "",
        f"耗时 {ctx['elapsed']}" if ctx.get("elapsed") else "",
    ) if x)
    d.text((PAD, y), meta, font=f_meta, fill=SUB)
    y += 44

    if ctx.get("run_url"):
        y = _link_row(d, y, f_link, GREEN)
    y += PAD
    return _finish(img, y)


def _render_failure(ctx):
    """生产发布失败：红色状态 + 告警条（自动回滚/跳过门禁）+ 编号建议操作 + 键值对。"""
    font, bold_stroke = _load_fonts()
    f_head = font(23, bold=True)
    f_head_r = font(23)
    f_title = font(48, bold=True)
    f_desc = font(25)
    f_sec = font(30, bold=True)
    f_step = font(25)
    f_warn = font(24)
    f_label = font(24)
    f_value = font(26)
    f_link = font(27)

    img, d = _canvas()
    y = PAD

    env_label = ctx.get("env_label") or ctx.get("env_name") or "-"
    left = f"{env_label}环境 · 发布失败"
    date = ctx.get("release_date") or ""
    d.text((PAD, y), left, font=f_head, fill=RED)
    if date:
        d.text((W - PAD - d.textlength(date, font=f_head_r), y), date, font=f_head_r, fill=SUB)
    y += 36
    _dashed(d, y, PAD, W - PAD)
    y += 40

    y = _status_row(d, y, f_title, f_desc, bold_stroke, False,
                    ctx.get("title") or "部署失败", _sub_text(ctx))

    # 告警条：自动回滚 / 跳过门禁（浅橙底 + 橙左边线）
    for alert in ctx.get("alerts") or []:
        lines = _wrap(d, alert, f_warn, W - 2 * PAD - 48)
        block_h = 26 * len(lines) + 24
        d.rounded_rectangle([PAD, y, W - PAD, y + block_h], radius=10, fill="#FFF7E6")
        d.rectangle([PAD, y, PAD + 6, y + block_h], fill=ORANGE)
        for i, line in enumerate(lines):
            d.text((PAD + 26, y + 12 + i * 26), line, font=f_warn, fill="#AD4E00")
        y += block_h + 30

    # 建议操作：编号步骤
    d.text((PAD, y), "建议操作", font=f_sec, fill=INK)
    y += 52
    step_w = W - 2 * PAD - 48
    for i, step in enumerate(ctx.get("steps") or [], 1):
        lines = _wrap(d, step, f_step, step_w)
        chip = 30
        d.ellipse([PAD, y, PAD + chip, y + chip], fill=RED)
        d.text((PAD + chip / 2, y + chip / 2), str(i), font=font(17, bold=True),
               fill="#FFFFFF", anchor="mm")
        for li, line in enumerate(lines):
            d.text((PAD + chip + 18, y + 3 + li * 32), line, font=f_step,
                   fill=INK if li == 0 else MID)
        y += max(chip, len(lines) * 32) + 16
    y += 8
    d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
    y += 28

    value_x = PAD + 300
    for key, value, value_color in _rows(ctx):
        d.text((PAD, y + 4), key, font=f_label, fill=SUB)
        for i, line in enumerate(_wrap(d, value, f_value, W - PAD - value_x)):
            d.text((value_x, y + i * 36), line, font=f_value, fill=value_color)
        y += 36 + 18
    y += 6

    if ctx.get("run_url"):
        y = _link_row(d, y, f_link, RED)
    y += PAD
    return _finish(img, y)
