# -*- coding: utf-8 -*-
"""日志分析只读 CLI：白名单命令在日志目录内解释执行，不调用系统 shell。

允许：grep / head / tail / sed(-n) / awk(print) / wc / cat / sort / uniq / cut
禁止：写文件、删除、重定向、命令替换、白名单外程序。
路径只能落在当前日志所在目录或 log_cache 根目录。
"""
from __future__ import annotations

import re
import shlex
import time
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

from ai.core.log_cache import LOG_CACHE_ROOT

ALLOWED_CMDS = frozenset({
    "grep", "head", "tail", "sed", "awk", "wc", "cat", "sort", "uniq", "cut",
})
TIMEOUT_SEC = 10.0
MAX_PROCESS_LINES = 20000
MAX_OUTPUT_CHARS = 1500
MAX_HEAD = 200

_FORBIDDEN = re.compile(r"[;`]|&&|\|\||>>|[<>]|\$\(|\$\{")
_SED_N = re.compile(r"^(\d+)(?:,(\d+))?p$")


class ShellDenied(Exception):
    """白名单或路径校验失败。"""


class ShellTimeout(Exception):
    """超过 TIMEOUT_SEC。"""


def run_readonly_shell(cmd: str, log_path: str) -> tuple[bool, str, int]:
    """执行一条只读命令。

    Returns:
        (ok, text, line_count) 失败时 ok=False，text 是给 LLM 看的原因。
    """
    t0 = time.monotonic()
    try:
        stages = _parse_pipeline(cmd)
        roots = _allowed_roots(log_path)
        default_file = Path(log_path).resolve()
        stdin: Optional[list[str]] = None
        last_count = 0
        for i, stage in enumerate(stages):
            _check_time(t0)
            out = _run_stage(stage, stdin, default_file, roots, t0, first=(i == 0))
            last_count = len(out)
            stdin = out
        text = "\n".join(stdin or [])
        return True, _truncate_output(text, last_count), last_count
    except ShellDenied as e:
        return False, f"（命令被拒绝）{e}", 0
    except ShellTimeout:
        return False, f"（命令超时 {int(TIMEOUT_SEC)}s，请收窄 grep/时间范围）", 0
    except FileNotFoundError:
        return False, "（文件不存在，只能用当前这份日志的文件名）", 0
    except Exception as e:
        return False, f"（命令执行失败）{type(e).__name__}", 0


def _parse_pipeline(cmd: str) -> list[list[str]]:
    raw = (cmd or "").strip()
    if not raw:
        raise ShellDenied("空命令")
    if _FORBIDDEN.search(raw):
        raise ShellDenied("禁止重定向、串联、命令替换或写文件")
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError as e:
        raise ShellDenied(f"命令无法解析: {e}") from e
    if not tokens:
        raise ShellDenied("空命令")
    stages: list[list[str]] = []
    cur: list[str] = []
    for tok in tokens:
        if tok == "|":
            if not cur:
                raise ShellDenied("管道为空")
            stages.append(cur)
            cur = []
            continue
        cur.append(tok)
    if not cur:
        raise ShellDenied("管道末段为空")
    stages.append(cur)
    for st in stages:
        name = st[0]
        if name not in ALLOWED_CMDS:
            raise ShellDenied(f"命令不在白名单: {name}")
        if name == "sed" and "-i" in st[1:]:
            raise ShellDenied("sed 禁止 -i")
        if name == "awk" and any("system" in a.lower() or "getline" in a.lower() for a in st[1:]):
            raise ShellDenied("awk 只允许 print")
    return stages


def _allowed_roots(log_path: str) -> list[Path]:
    roots: list[Path] = []
    p = Path(log_path).resolve()
    roots.append(p.parent if p.is_file() else (p if p.is_dir() else p.parent))
    try:
        roots.append(LOG_CACHE_ROOT.resolve())
    except Exception:
        pass
    return roots


def _resolve_file(name: str, default_file: Path, roots: list[Path]) -> Path:
    raw = Path(name)
    if raw.name in {".", ".."} or name.strip() in {".", ".."}:
        raise ShellDenied("路径非法")
    if raw.is_absolute():
        cand = raw
    else:
        cand = default_file.parent / raw
    try:
        resolved = cand.resolve()
    except Exception as e:
        raise ShellDenied("路径无法解析") from e
    if resolved == default_file:
        return resolved
    for root in roots:
        try:
            resolved.relative_to(root)
            if resolved.is_file():
                return resolved
            raise ShellDenied(f"不是文件: {raw.name}")
        except ValueError:
            continue
    raise ShellDenied(f"路径超出日志目录: {raw.name}")


def _check_time(t0: float) -> None:
    if time.monotonic() - t0 > TIMEOUT_SEC:
        raise ShellTimeout()


def _iter_file_lines(path: Path, t0: float) -> Iterable[tuple[int, str]]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            _check_time(t0)
            if i > MAX_PROCESS_LINES:
                break
            yield i, line.rstrip("\n\r")


def _file_args(args: list[str], default_file: Path, roots: list[Path], first: bool) -> list[Path]:
    files = [a for a in args if not a.startswith("-")]
    if files:
        return [_resolve_file(a, default_file, roots) for a in files]
    if first:
        return [default_file]
    return []


def _run_stage(
    stage: list[str],
    stdin: Optional[list[str]],
    default_file: Path,
    roots: list[Path],
    t0: float,
    first: bool,
) -> list[str]:
    name, *rest = stage
    if name == "grep":
        return _grep(rest, stdin, default_file, roots, t0, first)
    if name == "head":
        return _head(rest, stdin, default_file, roots, t0, first)
    if name == "tail":
        return _tail(rest, stdin, default_file, roots, t0, first)
    if name == "cat":
        return _cat(rest, stdin, default_file, roots, t0, first)
    if name == "wc":
        return _wc(rest, stdin, default_file, roots, t0, first)
    if name == "sort":
        return _sort(rest, stdin, default_file, roots, t0, first)
    if name == "uniq":
        return _uniq(rest, stdin)
    if name == "cut":
        return _cut(rest, stdin)
    if name == "sed":
        return _sed(rest, stdin, default_file, roots, t0, first)
    if name == "awk":
        return _awk(rest, stdin, default_file, roots, t0, first)
    raise ShellDenied(f"命令不在白名单: {name}")


def _split_opts(args: list[str]) -> tuple[list[str], list[str]]:
    opts, rest = [], []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            rest.extend(args[i + 1:])
            break
        if a.startswith("-") and a != "-":
            opts.append(a)
            if a in ("-n", "-A", "-B", "-C", "-e", "-d", "-f") and i + 1 < len(args) and not args[i + 1].startswith("-"):
                # grep -n is a flag without value; head -n 20 has value
                if a in ("-A", "-B", "-C", "-e", "-d", "-f") or (
                    a == "-n" and i + 1 < len(args) and re.fullmatch(r"\d+", args[i + 1] or "")
                ):
                    opts.append(args[i + 1])
                    i += 2
                    continue
            i += 1
            continue
        rest.append(a)
        i += 1
    return opts, rest


def _source_lines(
    rest_files: list[str],
    stdin: Optional[list[str]],
    default_file: Path,
    roots: list[Path],
    t0: float,
    first: bool,
) -> Iterable[tuple[int, str]]:
    files = _file_args(rest_files, default_file, roots, first=first and not stdin)
    if files:
        for fp in files:
            yield from _iter_file_lines(fp, t0)
        return
    if stdin is None:
        yield from _iter_file_lines(default_file, t0)
        return
    for i, line in enumerate(stdin, 1):
        _check_time(t0)
        yield i, line


def _grep(args, stdin, default_file, roots, t0, first) -> list[str]:
    ignore = False
    invert = False
    count_only = False
    line_num = False
    literal = False
    pattern = ""
    files: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-i",):
            ignore = True
        elif a in ("-v",):
            invert = True
        elif a in ("-c",):
            count_only = True
        elif a in ("-n",):
            line_num = True
        elif a in ("-F",):
            literal = True
        elif a in ("-E",):
            pass
        elif a in ("-e",) and i + 1 < len(args):
            pattern = args[i + 1]
            i += 1
        elif a.startswith("-"):
            raise ShellDenied(f"grep 不支持 {a}")
        elif not pattern:
            pattern = a
        else:
            files.append(a)
        i += 1
    if not pattern:
        raise ShellDenied("grep 需要模式")
    flags = re.I if ignore else 0
    rx = re.compile(re.escape(pattern) if literal else pattern, flags)
    hits = []
    n = 0
    for ln, line in _source_lines(files, stdin, default_file, roots, t0, first):
        ok = bool(rx.search(line))
        if invert:
            ok = not ok
        if not ok:
            continue
        n += 1
        if count_only:
            continue
        hits.append(f"{ln}:{line}" if line_num else line)
        if len(hits) >= MAX_PROCESS_LINES:
            break
    if count_only:
        return [str(n)]
    return hits


def _parse_n(opts_and_rest: list[str], default: int) -> tuple[int, list[str]]:
    n = default
    rest = []
    i = 0
    args = opts_and_rest
    while i < len(args):
        a = args[i]
        if a in ("-n", "--lines") and i + 1 < len(args):
            n = int(args[i + 1])
            i += 2
            continue
        if re.fullmatch(r"-\d+", a):
            n = int(a[1:])
            i += 1
            continue
        rest.append(a)
        i += 1
    n = max(1, min(n, MAX_HEAD))
    return n, rest


def _head(args, stdin, default_file, roots, t0, first) -> list[str]:
    n, rest = _parse_n(args, 10)
    out = []
    for _ln, line in _source_lines(rest, stdin, default_file, roots, t0, first):
        out.append(line)
        if len(out) >= n:
            break
    return out


def _tail(args, stdin, default_file, roots, t0, first) -> list[str]:
    n, rest = _parse_n(args, 10)
    buf: deque[str] = deque(maxlen=n)
    for _ln, line in _source_lines(rest, stdin, default_file, roots, t0, first):
        buf.append(line)
    return list(buf)


def _cat(args, stdin, default_file, roots, t0, first) -> list[str]:
    _, rest = _split_opts(args)
    out = []
    chars = 0
    for _ln, line in _source_lines(rest, stdin, default_file, roots, t0, first):
        out.append(line)
        chars += len(line) + 1
        if chars >= MAX_OUTPUT_CHARS * 4 or len(out) >= MAX_HEAD:
            break
    return out


def _wc(args, stdin, default_file, roots, t0, first) -> list[str]:
    want_l = "-l" in args or not any(a in args for a in ("-l", "-c", "-w"))
    want_c = "-c" in args
    want_w = "-w" in args
    rest = [a for a in args if not a.startswith("-")]
    n_lines = n_words = n_chars = 0
    for _ln, line in _source_lines(rest, stdin, default_file, roots, t0, first):
        n_lines += 1
        n_chars += len(line) + 1
        n_words += len(line.split())
    parts = []
    if want_l:
        parts.append(str(n_lines))
    if want_w:
        parts.append(str(n_words))
    if want_c:
        parts.append(str(n_chars))
    return [" ".join(parts)]


def _sort(args, stdin, default_file, roots, t0, first) -> list[str]:
    rest = [a for a in args if not a.startswith("-")]
    lines = [line for _ln, line in _source_lines(rest, stdin, default_file, roots, t0, first)]
    reverse = "-r" in args
    return sorted(lines, reverse=reverse)


def _uniq(args, stdin) -> list[str]:
    if stdin is None:
        raise ShellDenied("uniq 需要管道输入")
    out = []
    prev = None
    for line in stdin:
        if line != prev:
            out.append(line)
            prev = line
    return out


def _cut(args, stdin) -> list[str]:
    if stdin is None:
        raise ShellDenied("cut 需要管道输入")
    delim = "\t"
    fields = [1]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-d" and i + 1 < len(args):
            delim = args[i + 1] or "\t"
            i += 2
            continue
        if a.startswith("-d") and len(a) > 2:
            delim = a[2:] or "\t"
            i += 1
            continue
        if a == "-f" and i + 1 < len(args):
            fields = _parse_fields(args[i + 1])
            i += 2
            continue
        if a.startswith("-f") and len(a) > 2:
            fields = _parse_fields(a[2:])
            i += 1
            continue
        if a.startswith("-"):
            raise ShellDenied(f"cut 不支持 {a}")
        i += 1
    out = []
    for line in stdin:
        cols = line.split(delim)
        picked = []
        for f in fields:
            if 1 <= f <= len(cols):
                picked.append(cols[f - 1])
        out.append(delim.join(picked))
    return out


def _parse_fields(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if re.fullmatch(r"\d+", part):
            out.append(int(part))
        elif re.fullmatch(r"\d+-\d+", part):
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            raise ShellDenied(f"cut 字段非法: {spec}")
    if not out:
        raise ShellDenied("cut 需要 -f")
    return out[:20]


def _sed(args, stdin, default_file, roots, t0, first) -> list[str]:
    if "-n" not in args:
        raise ShellDenied("sed 只允许 -n 打印")
    script = ""
    rest = []
    skip_n = False
    for a in args:
        if skip_n:
            skip_n = False
            continue
        if a == "-n":
            continue
        if a == "-e":
            skip_n = True
            continue
        if not script and _SED_N.match(a):
            script = a
            continue
        if a.startswith("-"):
            raise ShellDenied(f"sed 不支持 {a}")
        rest.append(a)
    m = _SED_N.match(script or "")
    if not m:
        raise ShellDenied("sed 只允许 -n 'Np' 或 -n 'N,Mp'")
    start = int(m.group(1))
    end = int(m.group(2) or start)
    if end < start or end - start > 500:
        raise ShellDenied("sed 范围过大")
    out = []
    for ln, line in _source_lines(rest, stdin, default_file, roots, t0, first):
        if start <= ln <= end:
            out.append(line)
        if ln > end:
            break
    return out


def _awk(args, stdin, default_file, roots, t0, first) -> list[str]:
    script = ""
    rest = []
    for a in args:
        if a.startswith("-"):
            raise ShellDenied(f"awk 不支持 {a}")
        if not script:
            script = a.strip()
        else:
            rest.append(a)
    compact = re.sub(r"\s+", "", script or "")
    low = compact.lower()
    if not (low.startswith("{print") or low.startswith("print")):
        raise ShellDenied("awk 只允许 {print} 或 {print $1,$2}")
    if "system" in low or "getline" in low or ">" in compact or "<" in compact:
        raise ShellDenied("awk 只允许 print")
    inner = re.sub(r"^\{|\}$", "", script).strip()
    inner = re.sub(r"^print\s*", "", inner, flags=re.I).strip()
    fields = [p.strip() for p in inner.split(",") if p.strip()] if inner else []
    out = []
    for _ln, line in _source_lines(rest, stdin, default_file, roots, t0, first):
        cols = line.split()
        if not fields:
            out.append(line)
            continue
        picked = []
        for f in fields:
            if f == "$NF":
                picked.append(cols[-1] if cols else "")
            elif f.startswith("$") and f[1:].isdigit():
                idx = int(f[1:])
                picked.append(cols[idx - 1] if 1 <= idx <= len(cols) else "")
            else:
                raise ShellDenied("awk 只允许 $N / $NF")
        out.append(" ".join(picked))
    return out


def _truncate_output(text: str, line_count: int) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        header = f"shell 输出({line_count} 行)"
        return f"{header}:\n{text}" if text else f"{header}: （空）"
    lines = text.splitlines()
    keep = lines[:8] + (["…"] if len(lines) > 12 else []) + lines[-4:]
    body = "\n".join(keep)
    return (
        f"shell 输出({line_count} 行)，已截断，禁止据此假装读完全量。\n"
        + body[:MAX_OUTPUT_CHARS]
    )
