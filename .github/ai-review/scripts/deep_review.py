#!/usr/bin/env python3
"""AI Review 第二层：LLM 深度检视（人工触发：PR 加 ai-review 标签或手动 dispatch）。

用法：
  CI：  python3 deep_review.py --rules-root docs/ai-review --out report.md
        env: REPO / PR_NUMBER / GH_TOKEN / AI_REVIEW_API_KEY / AI_REVIEW_BASE_URL
             AI_REVIEW_MODEL(可选) / AI_REVIEW_FALLBACK_API_KEY / AI_REVIEW_FALLBACK_BASE_URL
             AI_REVIEW_FALLBACK_MODEL(可选) / HEAD_SHA(可选)
  本地回放（历史事故回归测试，无需 GH_TOKEN）：
        python3 deep_review.py --diff-file x.diff --title "PR 标题" --out report.md

设计要点：
- 规则文件（rules/pitfalls/prompt 模板）从 checkout 的 base 分支读取，
  PR 自身无法篡改检视规则；
- diff 排除 lock 文件，总量截断（超限时优先保留命中快筛规则的文件）；
- LLM 主备降级：主超时/5xx/429 重试一次后切备；双双失败输出「检视未完成」报告，
  退出码 2（不阻塞任何流程）；
- 密钥只从环境变量读取，日志仅打印通道与耗时，绝不回显。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from ai_review_common import (
    fetch_pr_files, fetch_pr_meta, finding_fingerprint, gh_api,
    guess_line_number, is_lock_file, now_display_str, parse_new_side_lines,
    split_code_and_docs, strip_patch_noise,
)
from quick_screen import screen

EXIT_OK, EXIT_LLM_FAIL = 0, 2
MAX_DIFF_LINES = 2400          # 送入 LLM 的 diff 总行数上限
MAX_PATCH_PER_FILE = 300       # 单文件 patch 行数上限，超出截断
LLM_TIMEOUT = 120
# LLM 净耗时累加（多次调用/重试合并），供报告尾部耗时明细使用
_LLM_SECONDS = 0.0
# 推理模型的 reasoning_content 同样计入 max_tokens，budget 给足，
# 否则 content 会被截成空（finish_reason=length），表现为「调用成功但无输出」。
# 普通 chat 模型输出上限通常 8192，请求 16000 会被 400 拒绝（非 transient，
# 双通道同挂即「未完成」）——按是否启用 reasoning 自适应。
MAX_TOKENS = 16000 if os.environ.get("AI_REVIEW_REASONING_EFFORT") else 8192


# ---------------------------------------------------------------- diff 构造

def build_diff_text(files, important_paths):
    """把 files 列表拼成送审 diff 文本；超限优先保留 important_paths。"""
    code, _docs = split_code_and_docs(files)
    entries = []
    for f in code:
        path = f.get("filename", "")
        patch = f.get("patch") or ""
        lines = patch.splitlines()
        clipped = False
        if len(lines) > MAX_PATCH_PER_FILE:
            lines = lines[:MAX_PATCH_PER_FILE]
            clipped = True
        body = "\n".join(lines)
        if clipped:
            body += f"\n...（该文件变更过大，剩余部分已截断）"
        stat = f.get("status", "modified")
        entries.append({
            "path": path,
            "text": f"--- a/{path}\n+++ b/{path}（{stat}）\n{body}",
            "important": path in important_paths,
            "changes": f.get("changes", 0),
        })
    # 排序：命中规则的文件优先，其后按变更量降序
    entries.sort(key=lambda e: (not e["important"], -e["changes"]))
    out, used = [], 0
    for e in entries:
        n = e["text"].count("\n") + 1
        if used + n > MAX_DIFF_LINES and used > 0:
            out.append(f"### {e['path']}（因总长度限制未展开，仅列文件名）")
            continue
        out.append(e["text"])
        used += n
    return "\n".join(out), len(entries)


def important_paths_from(result):
    return {f["path"] for f in result["blockers"] + result["warnings"]}


# ---------------------------------------------------------------- LLM 调用

def _chat(base_url, key, model, messages):
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    # 推理模型可用 low 档压 reasoning 消耗（vars: AI_REVIEW_REASONING_EFFORT）
    effort = os.environ.get("AI_REVIEW_REASONING_EFFORT", "")
    if effort:
        payload["reasoning_effort"] = effort
    payload = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        body = json.loads(resp.read(1024 * 1024).decode("utf-8", "replace"))
    global _LLM_SECONDS
    _LLM_SECONDS += time.time() - t0
    choice = body["choices"][0]
    content = choice["message"].get("content")
    if not content:
        # content 为空：多为 token 预算被 reasoning 吃光或安全拦截
        raise ValueError(
            f"content 为空（finish_reason={choice.get('finish_reason')}，"
            f"usage={body.get('usage', {}).get('completion_tokens')}）")
    return content, time.time() - t0


def call_llm_with_fallback(messages):
    """主通道（重试 1 次）→ 备通道 → 失败返回 None。日志不回显密钥。"""
    channels = []
    main_key = os.environ.get("AI_REVIEW_API_KEY", "")
    if main_key:
        channels.append(("主", os.environ.get("AI_REVIEW_BASE_URL", ""),
                         main_key, os.environ.get("AI_REVIEW_MODEL", "deepseek-chat")))
    fb_key = os.environ.get("AI_REVIEW_FALLBACK_API_KEY", "")
    if fb_key:
        channels.append(("备", os.environ.get("AI_REVIEW_FALLBACK_BASE_URL", ""),
                         fb_key, os.environ.get("AI_REVIEW_FALLBACK_MODEL", "")))
    if not channels:
        print("未配置 AI_REVIEW_API_KEY，深度检视跳过", file=sys.stderr)
        return None, "未配置 LLM 密钥"

    last_err = ""
    for name, base, key, model in channels:
        for attempt in (1, 2):
            if not base:
                last_err = f"{name}通道未配置 BASE_URL"
                break
            try:
                content, secs = _chat(base, key, model, messages)
                print(f"LLM {name}通道（{model}）成功，耗时 {secs:.1f}s")
                return content, model
            except urllib.error.HTTPError as exc:
                last_err = f"{name}通道 HTTP {exc.code}"
                print(f"LLM 调用失败：{last_err}")
                if exc.code not in (429, 500, 502, 503, 504):
                    break          # 非 transient 错误，重试无意义
            except (urllib.error.URLError, TimeoutError, OSError,
                    KeyError, ValueError) as exc:
                last_err = f"{name}通道 {type(exc).__name__}: {exc}"
                print(f"LLM 调用失败：{last_err}")
    return None, last_err


def parse_llm_json(content):
    """从 LLM 输出中提取 JSON 对象（容忍 ```json 围栏）。"""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError("输出中未找到 JSON 对象")
    return json.loads(m.group(0))


def normalize_parsed(raw):
    """把 LLM 输出适配为统一结构。

    部分通道会忽略 response_format，自作主张输出另一种 schema
    （如 issues[{severity,file,message}]），这里做兼容映射；
    已符合约定 schema 的原样返回。
    """
    if not isinstance(raw, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    if "blockers" in raw or "warnings" in raw:
        return raw
    issues = raw.get("issues") or []
    norm = {"blockers": [], "warnings": [], "infos": []}
    for it in issues:
        if not isinstance(it, dict):
            continue
        severity = str(it.get("severity", "")).lower()
        loc = it.get("file") or it.get("location") or "未知位置"
        if it.get("line"):
            loc = f"{loc}:{it['line']}"
        item = {
            "location": loc,
            "issue": (it.get("message") or it.get("issue") or "").strip(),
            "suggestion": (it.get("suggestion") or it.get("fix") or "").strip(),
        }
        if severity in ("high", "critical", "blocking"):
            norm["blockers"].append(item)
        elif severity in ("medium", "moderate"):
            norm["warnings"].append(item)
        else:
            norm["infos"].append(item)
    # 结论按条目计数硬推导，不信任 LLM 自报（出现过 🔴1 却自称「未发现」）
    if norm["blockers"]:
        norm["conclusion"] = "高风险"
    elif norm["warnings"]:
        norm["conclusion"] = "有建议关注项"
    else:
        norm["conclusion"] = "未发现明显风险"
    return norm


# ---------------------------------------------------------------- 报告渲染

# 三档分级（用户定稿语义）：红=必须采纳 → 黄=选择采纳 → 绿=可优化，固定排序
SECTIONS = (
    ("blockers", "🔴 必须采纳（高危）"),
    ("warnings", "🟡 选择采纳（建议关注）"),
    ("infos", "🟢 可优化（不必采纳）"),
)
LEVEL_EMOJI = {"高风险": "🔴", "有建议关注项": "🟡", "未发现明显风险": "🟢"}


def _clip(text, limit):
    """仅用于企微文案（4000 字硬限）；PR 报告内容不截断。"""
    text = (text or "").strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def render_report(parsed, quick_result, pr_number, sha, model_label,
                  inline_paths=None):
    """文档化排版报告：标题 + 计数表格 + 红→黄→绿固定排序 + 建议默认展开。

    inline_paths：已发行内评论的意见指纹集合，从总结里剔除避免重复。
    注：GitHub 评论过滤自定义 CSS（style/bgcolor 均被剥离），真实背景色
    无法实现，以排版层级与分隔线保证「一份完整文档」的观感。
    """
    def _fp(it):
        """意见指纹：与 main 构造 inline_items 的口径严格一致。"""
        loc = it.get("location") or it.get("path") or "未知位置"
        return finding_fingerprint(loc, it.get("issue"))

    # 只展示「必须采纳」一档（用户定稿：黄绿不再展示）；快筛命中归入红组置顶。
    # 表格计数与正文分组共用同一份剔除后条目（口径不分裂）。
    blocker_items = [it for it in ((parsed or {}).get("blockers") or [])
                     if _fp(it) not in (inline_paths or set())]
    fps = [_fp(it) for it in blocker_items]
    lines = [
        "<!-- ors-ai-review:report -->",
        "<!-- ors-ai-review:fp=" + ",".join(fps) + " -->",
        "# 🤖 AI 检视报告",
        "",
        f"**PR #{pr_number}**" + (f" · `{sha[:8]}`" if sha else "")
        + f" · {now_display_str()}",
        "<!-- ors-ai-review:delta -->",
        "",
    ]
    if parsed is None:
        lines += [
            "⚠️ **本次深度检视未能完成**：原因 " + str(model_label)
            + "。请人工把关，或重新添加 ai-review 标签重试。",
            "",
            "---",
            "🪵 本报告不阻塞合并 · 由人工复核决定",
        ]
        return "\n".join(lines)

    n_quick = (len(quick_result.get("blockers") or [])
               + len(quick_result.get("warnings") or [])) if quick_result else 0
    n_inline = len(inline_paths or set())
    if not blocker_items and not n_quick and not n_inline:
        lines += [
            "🟢 **未发现必须采纳的高危改动**——数据库/迁移、部署链路、密钥等"
            "确定性规则与 AI 深度检视均未命中，可放心合并。",
            "",
            "---",
            "🪵 本报告不阻塞合并 · 由人工复核决定 · " + str(model_label),
        ]
        return "\n".join(lines)

    # blocker 全部转行内时，总结必须如实反映（否则「未发现」与行内矛盾，
    # 用户实测反馈过的口径分裂）
    if not blocker_items and not n_quick and n_inline:
        lines.append(f"🔴 **发现 {n_inline} 项必须采纳的问题**——"
                     f"已逐条钉到代码行，见 Files changed 标签页对应位置。")
        lines.append("")

    lines.append(f"🔴 **发现 {len(blocker_items) + n_quick} 项必须采纳的问题**"
                 f"（AI {len(blocker_items)} 项 + 规则快筛 {n_quick} 项）")
    lines.append("")

    def render_items(items):
        out = []
        for it in items:
            loc = it.get("location") or it.get("path") or "未知位置"
            issue = (it.get("issue") or "").strip()
            sug = (it.get("suggestion") or "").strip()
            out.append(f"- `{loc}`：{issue}" if issue else f"- `{loc}`")
            if sug and sug != issue:
                out += ["    <details open><summary>💡 小D的建议</summary>",
                        "",
                        f"    {sug}",
                        "",
                        "    </details>"]
        return out

    # 红组置顶：确定性规则快筛命中（100% 准确）优先于 AI 判断
    if n_quick:
        lines.append("### 🔴 规则快筛命中（确定性）")
        lines.append("")
        for it in quick_result["blockers"] + quick_result["warnings"]:
            lines.append(f"- `{it['path']}`：{it['label']}——{it['reason']}")
        lines.append("")

    if blocker_items:
        lines.append("### 🔴 必须采纳")
        lines.append("")
        lines += render_items(blocker_items)
        lines.append("")

    lines += ["---",
              "🪵 本报告不阻塞合并 · 由人工复核决定 · " + str(model_label)]
    return "\n".join(lines)


# ---------------------------------------------------------------- 进度可视化

class Progress:
    """PR 对话区的一条 sticky 进度评论，随阶段就地更新（打勾 + 已进行秒数）。

    - 仅 CI 模式可用（REPO/PR_NUMBER/GH_TOKEN 齐全），本地回放自动关闭；
    - 使用与最终报告相同的 marker：落评论阶段 post_comment 会把同一条
      评论 PATCH 成正式报告，进度条无缝变成报告，不堆楼；
    - 任何失败只打日志，绝不影响检视本身。
    """

    STEPS = ("拉取变更", "LLM 深度检视", "渲染报告")
    MARKER = "ors-ai-review:report"

    def __init__(self, repo, pr, token, t0):
        self.repo, self.pr, self.token, self.t0 = repo, pr, token, t0
        self.enabled = bool(repo and str(pr).isdigit() and token)
        self.done = set()

    def mark_done(self, step):
        self.done.add(step)

    def update(self, current):
        if not self.enabled:
            return
        try:
            from post_comment import find_comment
            elapsed = int(time.time() - self.t0)
            rows = []
            for s in self.STEPS:
                if s in self.done:
                    rows.append(f"- ✅ {s}")
                elif s == current:
                    rows.append(f"- ⏳ **{s}…**")
                else:
                    rows.append(f"- ⬜ {s}")
            md = "\n".join([
                f"<!-- {self.MARKER} -->",
                "# 🤖 AI 检视报告",
                "",
                f"**PR #{self.pr}** · {now_display_str()}",
                "",
                f"⏳ **AI 检视进行中** — 已进行 {elapsed} 秒",
                "",
                *rows,
                "",
                "> 完成后本条评论将自动替换为检视报告（预计 1~2 分钟）。",
            ])
            existing = find_comment(self.repo, self.pr, self.token, self.MARKER)
            if existing:
                gh_api(self.repo, f"issues/comments/{existing}", self.token,
                       method="PATCH", body={"body": md})
            else:
                gh_api(self.repo, f"issues/{self.pr}/comments", self.token,
                       method="POST", body={"body": md})
        except Exception as exc:
            print(f"进度更新失败（不影响检视）: {exc}", file=sys.stderr)


# ---------------------------------------------------------------- 主流程

def load_text(path, default=""):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def render_wecom(parsed, quick_result, repo, pr_number, title):
    """有高危项时生成企微文案（返回 None 表示无需推送）。"""
    items = []
    for it in (parsed or {}).get("blockers") or []:
        items.append((it.get("location", "") or "").strip() + " " +
                     (it.get("issue", "") or "").strip())
    for it in (quick_result or {}).get("blockers") or []:
        items.append(f"[规则] {it['path']} {it['label']}")
    if not items:
        return None
    lines = [f"**AI 检视发现高危项**",
             f"PR #{pr_number}《{title[:40]}》合入前发现 {len(items)} 项高危："]
    for it in items[:4]:
        lines.append(f"▪ {it[:80]}")
    if len(items) > 4:
        lines.append(f"▪ …共 {len(items)} 项")
    if repo and str(pr_number).isdigit():
        lines.append(f"详情：https://github.com/{repo}/pull/{pr_number}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules-root", default="docs/ai-review")
    parser.add_argument("--out", default=None, help="报告 markdown 输出路径")
    parser.add_argument("--out-wecom", default=None,
                        help="存在高危项时企微文案输出路径（无高危不写文件）")
    parser.add_argument("--out-inline", default=None,
                        help="行内评论数据 JSON 输出路径（可落 diff 行的意见）")
    parser.add_argument("--diff-file", help="本地回放模式：从文件读 unified diff")
    parser.add_argument("--title", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--sha", default=os.environ.get("HEAD_SHA", ""))
    args = parser.parse_args()

    repo = os.environ.get("REPO", "")
    pr = os.environ.get("PR_NUMBER", "")
    token = os.environ.get("GH_TOKEN", "")

    t0 = time.time()
    progress = Progress(repo, pr, token, t0)
    progress.update("拉取变更")

    # 1. 取变更文件（CI）或本地 diff
    title = args.title
    if args.diff_file:
        diff_text = load_text(args.diff_file)
        files = []
        title = args.title
    else:
        if not (repo and pr and token):
            print("缺少 REPO / PR_NUMBER / GH_TOKEN", file=sys.stderr)
            return EXIT_OK          # 拉不到上下文不算事故，静默退出
        status, files = fetch_pr_files(repo, pr, token)
        if status != 200:
            print(f"拉取 PR 文件失败：HTTP {status}", file=sys.stderr)
            return EXIT_OK
        progress.mark_done("拉取变更")
        progress.update("LLM 深度检视")
        if not title:
            meta_status, meta = fetch_pr_meta(repo, pr, token)
            if meta_status == 200 and isinstance(meta, dict):
                title = meta.get("title", "")
                args.body = meta.get("body") or args.body

    # 2. 规则快筛（文件模式下由 diff 合成伪 files）
    if files:
        quick_result = screen(files)
        important = important_paths_from(quick_result)
        diff_text, _n = build_diff_text(files, important)
    else:
        quick_result = {"skipped": True, "blockers": [], "warnings": []}
        important = set()
        diff_text = args.diff_file and load_text(args.diff_file) or ""
        if len(diff_text.splitlines()) > MAX_DIFF_LINES:
            diff_text = "\n".join(diff_text.splitlines()[:MAX_DIFF_LINES]) \
                + "\n...（diff 过长已截断）"

    # 3. 组装 prompt（规则资产来自 base checkout，防 PR 篡改）
    root = args.rules_root
    system = load_text(os.path.join(root, "prompt-template.md"))
    rules = load_text(os.path.join(root, "rules.md"))
    pitfalls = load_text(os.path.join(root, "pitfalls.md"))
    user = (
        f"# PR 信息\n标题：{title or '（未知）'}\n\n"
        f"{('# 描述\n' + args.body[:1500] + '\n') if args.body else ''}"
        f"# 检视规则（逐条对照）\n{rules}\n\n"
        f"# 项目踩坑知识库（历史事故，优先对照）\n{pitfalls}\n\n"
        f"# 变更 diff\n```diff\n{diff_text}\n```\n\n"
        "请按系统指令输出 JSON 检视报告。"
    )

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    parsed, last_raw, model_label = None, "", "LLM 不可用"
    # 截断的「成功响应」不抛异常，解析失败也要给一次重调机会
    for attempt in (1, 2):
        content, model_label = call_llm_with_fallback(messages)
        if not content:
            break
        try:
            parsed = normalize_parsed(parse_llm_json(content))
            break
        except Exception as exc:   # 结构异常（TypeError 等）同样降级，保证必出报告
            last_raw = content
            print(f"LLM 输出解析失败（第 {attempt} 次）：{exc}", file=sys.stderr)
            model_label = f"输出异常（{model_label}）"
    progress.mark_done("LLM 深度检视")
    t_llm_done = time.time()
    progress.update("渲染报告")
    if not parsed and last_raw and args.out:
        # 原始输出落盘供诊断（LLM 文本非密钥）；报告降级为「未完成」
        with open(args.out + ".raw", "w", encoding="utf-8") as fh:
            fh.write(last_raw)

    # 行内评论落点：blocker/warning 且行号落在该文件 diff 新侧行集合内才可钉行；
    # 命中的从总结评论剔除，其余保留（绿色提示不进 Files changed，避免打扰）
    inline_paths = set()
    inline_items = []
    if files and parsed:
        side = {f.get("filename", ""): parse_new_side_lines(f.get("patch"))
                for f in files}
        for key in ("blockers",):
            for it in list(parsed.get(key) or []):
                loc = it.get("location", "") or ""
                path, _, ln = loc.rpartition(":")
                if path in side and ln.isdigit() and int(ln) in side[path]:
                    inline_paths.add(finding_fingerprint(loc, it.get("issue")))
                    issue_full = (it.get("issue") or "").strip()
                    sug_full = (it.get("suggestion") or "").strip()
                    inline_items.append({
                        "path": path, "line": int(ln), "level": key,
                        "body": ("🔴" if key == "blockers" else "🟡")
                                + f" **AI 检视**：{issue_full}"
                                + (f"\n\n> 🧸 小D的建议：{sug_full}"
                                   if sug_full else ""),
                    })
    if args.out_inline:
        with open(args.out_inline, "w", encoding="utf-8") as fh:
            json.dump(inline_items, fh, ensure_ascii=False, indent=2)
        print(f"行内评论落点：{len(inline_items)} 条（其余保留在总结评论）")

    report = render_report(parsed, quick_result, pr or "本地回放", args.sha,
                           model_label or "LLM 不可用", inline_paths)
    if progress.enabled:
        import deep_review as _self
        llm_sec = int(_self._LLM_SECONDS)
        other_sec = max(int(t_llm_done - t0) - llm_sec, 0)
        total = int(time.time() - t0)
        report = report.replace(
            "\n---\n🪵",
            f"\n⏱ 总耗时 {total} 秒（准备 {other_sec}s · LLM {llm_sec}s）\n\n---\n🪵", 1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
    else:
        print(report)
    if args.out_wecom:
        wecom = render_wecom(parsed, quick_result, repo, pr, title)
        if wecom:
            with open(args.out_wecom, "w", encoding="utf-8") as fh:
                fh.write(wecom)
            print("存在高危项，已生成企微文案")
        else:
            print("无高危项，跳过企微提醒")
    return EXIT_OK if parsed else EXIT_LLM_FAIL


if __name__ == "__main__":
    sys.exit(main())
