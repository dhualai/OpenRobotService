"""附件解析器：日志 / ZIP / 文件夹 / 图片 → 诊断上下文

支持的附件类型：
    - 日志文件 (.txt/.log/.csv)          → ERROR/WARN 提取 + 时间范围
    - ZIP 压缩包 (.zip)                 → 解压 → 遍历 → 识别日志 → 提取
    - 文件夹（多文件多级目录）           → 遍历目录树 → 识别日志 → 提取
    - 图片 (.jpg/.png/.jpeg/.bmp)       → 提取文件名列表（OCR 后续迭代）
    - 回放文件                           → 预留入口，解析逻辑未实现

设计原则：
    - 无附件不报错，静默跳过
    - 解析失败不阻塞主流程
    - 文本截断 100KB/2000 字，防止撑爆 LLM context
    - ZIP 解压最多遍历 50 个文件，防止炸弹
"""

import base64
import io
import os
import tempfile
import zipfile
from typing import List, Optional, Tuple, Set
import httpx
from pathlib import Path

from ai.agents.AiTaskPlatform.schemas import AttachmentAnalysis
from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# MIME type 映射
_EXT_TO_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".bmp": "image/bmp",
    ".gif": "image/gif", ".webp": "image/webp",
}

# 单次最多分析几张图片（控制 token/时间）
_VISION_MAX_IMAGES = 3



# ── 常量 ──────────────────────────────────────────────────────

_TEXT_CHUNK_LIMIT = 100_000      # 单个文本文件最大字节
_EXTRACT_SUMMARY_LIMIT = 2000   # 日志摘要最多 2000 字
_ZIP_MAX_FILES = 50              # ZIP 最多遍历文件数
_DIR_MAX_FILES = 50              # 文件夹最多遍历文件数
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
_LOG_EXTS = {".txt", ".log", ".csv"}


# ============================================================
# 对外入口
# ============================================================

async def parse_attachments(attachments: list) -> AttachmentAnalysis:
    """解析附件列表 → 分析摘要。

    单个附件：直接按类型处理。
    多个附件：逐个处理，日志摘要合并，图片列表合并。
    """
    result = AttachmentAnalysis()
    if not attachments:
        return result

    log_texts: list[str] = []
    image_names: list[str] = []

    for att in attachments:
        if not isinstance(att, dict):
            continue
        filename = att.get("filename") or att.get("name") or ""
        path = att.get("path") or att.get("url") or ""

        if not filename and not path:
            continue

        # ── 日志文件 ──
        if _is_log_file(filename, path):
            try:
                content = await _read_content(att)
                if content:
                    log_texts.append(f"--- {filename} ---\n{content}")
            except Exception:
                pass

        # ── ZIP 压缩包 ──
        elif _is_zip_file(filename, path):
            try:
                zip_logs = await _parse_zip(att)
                log_texts.extend(zip_logs)
            except Exception:
                pass

        # ── 文件夹（path 指向本地目录）──
        elif _is_directory(path):
            try:
                dir_logs = _traverse_directory(path)
                log_texts.extend(dir_logs)
            except Exception:
                pass

        # ── 图片 ──
        elif _is_image_file(filename, path):
            image_names.append(filename or Path(path).name)

        # ── 回放 ──
        elif _is_replay_file(filename, path):
            result.has_replay = True

        # ── 未知类型 → 尝试当文本读（可能是无扩展名的日志）──
        else:
            try:
                content = await _read_content(att)
                if content:
                    log_texts.append(f"--- {filename or '未命名附件'} ---\n{content}")
            except Exception:
                pass

    # ── 汇总 ──
    if log_texts:
        result.has_logs = True
        combined = "\n".join(log_texts)
        result.log_summary = _extract_log_errors(combined)

    if image_names:
        result.has_screenshots = True
        result.log_summary = (
            f"{result.log_summary}\n\n[附图片 {len(image_names)} 张："
            f"{', '.join(image_names[:10])}{' ...' if len(image_names) > 10 else ''}]"
        ).strip()

    return result


# ============================================================
# ZIP 解析
# ============================================================

async def _parse_zip(att: dict) -> list[str]:
    """解压 ZIP → 遍历内部文件 → 对日志型文件提取文本。

    处理方式：
        - 内存解压（不落盘），单个文件 ≤100KB
        - 最多遍历 50 个文件
        - 嵌套 ZIP 不递归（安全）

    返回: ["--- outer.zip/filename.log ---\\n<content>", ...]
    """
    zip_bytes = await _read_bytes(att)
    if not zip_bytes:
        return []

    results: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            count = 0
            for name in zf.namelist():
                if count >= _ZIP_MAX_FILES:
                    results.append(f"--- ZIP({len(zf.namelist())}文件,仅展示前{_ZIP_MAX_FILES}) ---")
                    break
                count += 1

                # 跳过目录项
                if name.endswith("/"):
                    continue

                if _is_log_file(name, ""):
                    try:
                        content = zf.read(name).decode("utf-8", errors="replace")[:_TEXT_CHUNK_LIMIT]
                        label = f"--- {Path(name).name} (ZIP) ---"
                        results.append(f"{label}\n{content}")
                    except Exception:
                        pass
    except zipfile.BadZipFile:
        pass
    except Exception:
        pass

    return results


def _is_directory(path: str) -> bool:
    """判断路径是否为本地目录"""
    if not path:
        return False
    p = Path(path)
    return p.is_dir()


# ============================================================
# 文件夹遍历
# ============================================================

def _traverse_directory(dir_path: str) -> list[str]:
    """遍历本地文件夹 → 识别日志/文本文件。

    返回: ["--- subdir/file.log ---\\n<content>", ...]
    """
    results: list[str] = []
    root = Path(dir_path)
    if not root.is_dir():
        return results

    count = 0
    for entry in root.rglob("*"):
        if count >= _DIR_MAX_FILES:
            break
        if not entry.is_file():
            continue
        count += 1

        if _is_log_file(entry.name, ""):
            try:
                content = entry.read_text(encoding="utf-8", errors="replace")[:_TEXT_CHUNK_LIMIT]
                # 相对路径作为标签
                label = f"--- {entry.relative_to(root)} ---"
                results.append(f"{label}\n{content}")
            except Exception:
                pass

    return results


# ============================================================
# 文件类型判断
# ============================================================

def _is_log_file(filename: str, path: str) -> bool:
    name = (filename or path).lower()
    return any(name.endswith(ext) for ext in _LOG_EXTS)


def _is_zip_file(filename: str, path: str) -> bool:
    name = (filename or path).lower()
    return name.endswith(".zip")


def _is_image_file(filename: str, path: str) -> bool:
    name = (filename or path).lower()
    return any(name.endswith(ext) for ext in _IMAGE_EXTS)


def _is_replay_file(filename: str, path: str) -> bool:
    name = (filename or path).lower()
    return "replay" in name or "回放" in name


# ============================================================
# 内容读取（文本 / 二进制）
# ============================================================

async def _read_content(att: dict) -> str:
    """读取附件为文本字符串。"""
    path = att.get("path") or att.get("url", "")
    if not path:
        return ""

    try:
        if path.startswith("http://") or path.startswith("https://"):
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(path)
                if resp.status_code == 200:
                    return resp.text[:_TEXT_CHUNK_LIMIT]

        local = Path(path)
        if local.is_absolute() and local.exists():
            return local.read_text(encoding="utf-8", errors="replace")[:_TEXT_CHUNK_LIMIT]

        from pathlib import Path as _Path
        project_local = _Path(__file__).resolve().parent.parent.parent / path
        if project_local.exists():
            return project_local.read_text(encoding="utf-8", errors="replace")[:_TEXT_CHUNK_LIMIT]

    except Exception as e:
        logger.warning(f"Failed to read {path}: {e}")

    return ""


async def _read_bytes(att: dict) -> Optional[bytes]:
    """读取附件为二进制（用于 ZIP 解压等）。"""
    path = att.get("path") or att.get("url", "")
    if not path:
        return None

    try:
        if path.startswith("http://") or path.startswith("https://"):
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(path)
                if resp.status_code == 200:
                    return resp.content

        local = Path(path)
        if local.is_absolute() and local.exists():
            return local.read_bytes()

        from pathlib import Path as _Path
        project_local = _Path(__file__).resolve().parent.parent.parent / path
        if project_local.exists():
            return project_local.read_bytes()

    except Exception as e:
        logger.warning(f"Failed to read bytes {path}: {e}")

    return None


# ============================================================
# 日志 ERROR/WARN 提取
# ============================================================

def _extract_log_errors(text: str) -> str:
    """从日志文本提取 ERROR/WARN/异常行 + 时间线上下文。

    提取策略：
        1. 扫描所有 ERROR/WARN/EXCEPTION/FAIL/FATAL/Traceback 行
        2. 取前 20 条
        3. 附加日志首尾时间戳范围

    Returns:
        摘要文本（≤2000 chars）
    """
    lines = text.split("\n")

    error_keywords = ("ERROR", "WARN", "EXCEPTION", "FAIL", "FATAL", "Traceback")
    error_lines = []
    for line in lines:
        upper = line.upper()
        if any(kw in upper for kw in error_keywords):
            error_lines.append(line.strip()[:200])

    first_ts_line = next((l for l in lines if _has_timestamp(l)), "")
    last_ts_line = next((l for l in reversed(lines) if _has_timestamp(l)), "")

    if not error_lines:
        return (
            f"日志 {len(lines)} 行，无明显错误。"
            + (f" 时间范围: {first_ts_line.strip()[:80]} ~ {last_ts_line.strip()[:80]}"
               if first_ts_line or last_ts_line else "")
        )

    parts = [
        f"日志 {len(lines)} 行，提取到 {len(error_lines)} 条异常：",
        *(error_lines[:20]),
    ]
    if first_ts_line or last_ts_line:
        parts.append(
            f"时间范围: {first_ts_line.strip()[:60]} ~ {last_ts_line.strip()[:60]}"
        )

    return "\n".join(parts)[:_EXTRACT_SUMMARY_LIMIT]


def _has_timestamp(line: str) -> bool:
    """检测行是否包含时间戳（HH:MM:SS 格式）。"""
    import re
    return bool(re.search(r"\d{2}:\d{2}:\d{2}", line))


# ============================================================
# 图片分析（视觉 LLM）
# ============================================================

async def analyze_images(attachments: list, task_context: dict = None) -> str:
    """两阶段图片分析：VLM 看图描述 → 文本模型推理分析

    Stage 1 — VLM (GPT-4o): 逐张描述画面内容、关键数据、异常信号、人工标注
    Stage 2 — 文本模型 (DeepSeek): 结合工单背景，分析描述中的线索和关键发现

    Returns:
        完整分析文本（描述 + 推理），失败返回空字符串
    """
    tc = task_context or {}

    # 1. 筛选图片附件
    image_atts = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        filename = att.get("filename") or att.get("name") or ""
        path = att.get("path") or att.get("url") or ""
        if _is_image_file(filename, path):
            image_atts.append(att)

    if not image_atts:
        return ""

    # 2. 读取图片 → base64 data URIs
    data_uris = []
    for att in image_atts[:_VISION_MAX_IMAGES]:
        fname = att.get("filename") or att.get("name") or ""
        try:
            img_bytes = await _read_bytes(att)
            if img_bytes and len(img_bytes) < 10 * 1024 * 1024:
                ext = Path(fname).suffix.lower()
                mime = _EXT_TO_MIME.get(ext, "image/png")
                b64 = base64.b64encode(img_bytes).decode()
                data_uris.append((fname, f"data:{mime};base64,{b64}"))
        except Exception:
            logger.warning(f"Failed to read image: {fname}")

    if not data_uris:
        return ""

    # ── Stage 1: VLM 看图描述 ──
    try:
        from ai.core import get_llm_client
        llm = await get_llm_client()

        names = ", ".join(f"{n}" for n, _ in data_uris)
        images = [uri for _, uri in data_uris]

        desc_prompt = _build_vision_prompt(names, tc)
        raw_descriptions = await llm.complete_vision(
            prompt=desc_prompt,
            images=images,
            system_prompt="你是 AGV/AMR 调度系统的操作专家。仔细看图，客观描述画面内容、关键数据、异常信号和人工标注。不诊断，不下结论。",
            max_tokens=600,
            temperature=0.2,
        )
    except Exception as e:
        logger.error(f"Stage 1 VLM failed: {e}")
        return f"[图片分析失败（VLM）: {e}]"

    # ── Stage 2: 文本模型推理 ──
    try:
        analysis_prompt = _build_analysis_prompt(names, tc, raw_descriptions)
        analysis = await llm.complete(
            prompt=analysis_prompt,
            system_prompt="你是 AGV/AMR 领域的技术支持专家。基于图片描述和工单背景，分析线索、关联问题、提取关键发现。简洁直接。",
            max_tokens=400,
            temperature=0.3,
        )
    except Exception as e:
        logger.warning(f"Stage 2 text analysis failed, fallback to VLM only: {e}")
        return f"[图片分析 {len(data_uris)} 张：{names}]\n{raw_descriptions.strip()}"

    return (
        f"[图片分析 {len(data_uris)} 张：{names}]\n"
        f"📷 画面描述：\n{raw_descriptions.strip()}\n\n"
        f"🔍 线索分析：\n{analysis.strip()}"
    )


def _build_vision_prompt(names: str, task_context: dict = None) -> str:
    """Stage 1 VLM prompt — 纯看图描述，不下结论"""
    tc = task_context or {}
    ctx_parts = []
    if tc.get("title"): ctx_parts.append(f"标题：{tc['title']}")
    if tc.get("problem_summary"): ctx_parts.append(f"问题概述：{tc['problem_summary']}")
    elif tc.get("description"): ctx_parts.append(f"描述：{tc['description'][:150]}")
    if tc.get("hypotheses"): ctx_parts.append(f"推测方向：{'/'.join(tc['hypotheses'])}")
    if tc.get("fault_code"): ctx_parts.append(f"故障码：{tc['fault_code']}")
    if tc.get("robot_type"): ctx_parts.append(f"车型：{tc['robot_type']}")
    ctx_text = "\n".join(ctx_parts) if ctx_parts else "（无工单背景信息）"

    return (
        f"工单背景：\n{ctx_text}\n\n"
        f"现在看图片 {names}。这是 AGV/AMR 调度系统的操作截图。"
        "请仔细观察，按以下要点描述（≥300字）：\n\n"
        "**1. 画面内容** — 这是什么界面？\n"
        "**2. 关键数据** — 任务编号、车辆编号、地图名称、状态码、时间戳、坐标等字段值\n"
        "**3. 异常信号** — 红色/橙色高亮、报错弹窗、状态异常（离线/故障/取消/超时）\n"
        "**4. 人工标注** — 红色框、箭头、圈注、画笔标记、文字批注及其指向位置\n\n"
        "客观描述，不诊断，不下结论。"
    )


def _build_analysis_prompt(names: str, task_context: dict, descriptions: str) -> str:
    """Stage 2 文本模型 prompt — 基于描述做推理"""
    tc = task_context or {}
    ctx_parts = []
    if tc.get("title"): ctx_parts.append(f"工单：{tc['title']}")
    if tc.get("description"): ctx_parts.append(f"描述：{tc['description'][:150]}")
    if tc.get("problem_summary"): ctx_parts.append(f"诊断概述：{tc['problem_summary']}")
    if tc.get("hypotheses"): ctx_parts.append(f"推测方向：{'/'.join(tc['hypotheses'])}")
    ctx_text = "\n".join(ctx_parts) if ctx_parts else "（无）"

    return (
        f"工单背景：\n{ctx_text}\n\n"
        f"以下是 {names} 的画面描述：\n\n"
        f"{descriptions}\n\n"
        "请基于以上描述，分析：\n"
        "1. 这些截图和工单问题有什么关联？\n"
        "2. 图中有什么值得关注的线索（异常值、状态矛盾、人工标注指向）？\n"
        "3. 下一步应该重点排查什么方向？\n\n"
        "简洁直接，工程师口吻，≤300字。"
    )
