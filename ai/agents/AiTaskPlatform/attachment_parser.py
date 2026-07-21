"""附件解析器：日志 → 关键错误提取 + 回放 → 路径/状态分析

设计原则：
    - 无附件不报错，静默跳过
    - 解析失败不阻塞主流程
    - 文本截断 100KB，防止大文件撑爆 context
"""

from typing import List, Optional
import httpx
from pathlib import Path

from ai.agents.AiTaskPlatform.schemas import AttachmentAnalysis


# ============================================================
# 对外入口
# ============================================================

async def parse_attachments(attachments: list) -> AttachmentAnalysis:
    """解析附件列表 → 分析摘要。

    Args:
        attachments: 附件列表 [{"filename":"...", "path":"...", ...}, ...]

    Returns:
        AttachmentAnalysis: 含日志摘要和回放摘要
    """
    result = AttachmentAnalysis()
    if not attachments:
        return result

    for att in attachments:
        if not isinstance(att, dict):
            continue
        filename = att.get("filename") or att.get("name") or ""
        path = att.get("path") or att.get("url") or ""

        if not filename and not path:
            continue

        # 判断文件类型
        if _is_log_file(filename, path):
            try:
                content = await _read_content(att)
                if content:
                    result.has_logs = True
                    result.log_summary = _extract_log_errors(content)
            except Exception:
                pass

        elif _is_replay_file(filename, path):
            result.has_replay = True
            # 回放解析后续实现

    return result


# ============================================================
# 文件类型判断
# ============================================================

def _is_log_file(filename: str, path: str) -> bool:
    """判断是否为日志文件"""
    name = (filename or path).lower()
    return name.endswith((".txt", ".log", ".csv"))


def _is_replay_file(filename: str, path: str) -> bool:
    """判断是否为回放文件"""
    name = (filename or path).lower()
    return "replay" in name or "回放" in name


# ============================================================
# 内容读取
# ============================================================

async def _read_content(att: dict) -> str:
    """读取附件文本内容。

    支持本地文件路径和远程 HTTP URL，超过 100KB 自动截断。
    """
    path = att.get("path") or att.get("url", "")
    if not path:
        return ""

    try:
        # HTTP URL
        if path.startswith("http://") or path.startswith("https://"):
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(path)
                if resp.status_code == 200:
                    text = resp.text
                    # 用 errors="replace" 跳过乱码字符
                    return text[:100_000]

        # 本地文件路径
        local = Path(path)
        if local.is_absolute() and local.exists():
            return local.read_text(encoding="utf-8", errors="replace")[:100_000]

        # 相对于项目根目录
        from pathlib import Path as _Path
        project_local = _Path(__file__).resolve().parent.parent.parent / path
        if project_local.exists():
            return project_local.read_text(encoding="utf-8", errors="replace")[:100_000]

    except Exception as e:
        print(f"  [attachment-parser] Failed to read {path}: {e}")

    return ""


# ============================================================
# 日志解析
# ============================================================

def _extract_log_errors(text: str) -> str:
    """从日志文本提取 ERROR/WARN/异常行 + 时间线上下文。

    提取策略：
        1. 扫描所有 ERROR/WARN/EXCEPTION/FAIL/FATAL 行
        2. 按时间排序
        3. 取前 20 条
        4. 附加日志首尾时间戳范围

    Returns:
        摘要文本（≤2000 chars）
    """
    lines = text.split("\n")

    # 提取错误/异常行
    error_keywords = ("ERROR", "WARN", "EXCEPTION", "FAIL", "FATAL", "Traceback")
    error_lines = []
    for line in lines:
        upper = line.upper()
        if any(kw in upper for kw in error_keywords):
            error_lines.append(line.strip()[:200])

    # 找出首尾包含时间戳的行（给工程师时间范围参考）
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

    return "\n".join(parts)[:2000]


def _has_timestamp(line: str) -> bool:
    """检测行是否包含时间戳（支持常见格式）。

    支持: YYYY-MM-DD HH:MM:SS, [YYYY-MM-DD HH:MM:SS],
           HH:MM:SS.mmm, ISO 8601
    """
    import re
    return bool(re.search(r"\d{2}:\d{2}:\d{2}", line))
