#!/usr/bin/env python3
"""AI Review 运行台账生成器（ORS-1149 观察期）。

拉取指定时间窗内的 PR 与 AI Review 运行数据（快筛覆盖、深度检视、
红档意见流水），与 docs/ai-review/evaluation.md 的人工判定表融合，
生成/更新 evaluation.md 中的「运行台账」自动区。

用法（本地，需 gh 已登录）：
  python ledger.py --since 2026-11-23T06:00Z
  python ledger.py --days 4

口径：
- 快筛自检率 = 有 AI Review run 的 PR / 时间窗内创建的 PR；
- 深度检视 = PR 存在 ors-ai-review:report 报告评论（labeled 或哨兵触发）；
- 意见流水从评论正文解析（层级：快筛/AI）；准确率只统计已判定条目，
  判定来源为 evaluation.md「观察期意见台账」人工表（权威），未匹配的
  条目标「待判定」。

边界：
- 快筛未命中保持沉默（无评论），覆盖判定依赖 run 记录（分支+时间窗
  匹配，同分支多 PR 按创建时间就近归属，误差可接受）；
- 哨兵触发（历史架构）的 run 挂在 ai-review-go 分支，按 displayTitle
  的 PR#N 归属。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO_DEFAULT = "dhualai/OpenRobotService"
EVAL_DEFAULT = os.path.join("docs", "ai-review", "evaluation.md")
MARK_START = "<!-- ledger:auto:start -->"
MARK_END = "<!-- ledger:auto:end -->"
WORKFLOW = "ai-review.yml"
SENTINEL_BRANCH = "ai-review-go"

RED_TOTAL_RE = re.compile(
    r"🔴 \*\*(?:发现 (\d+) 项必须采纳|规则快筛命中 (\d+) 项高危)")
GREEN_RE = re.compile(r"🟢 \*\*未发现必须采纳的高危改动\*\*")
SENTINEL_PR_RE = re.compile(r"PR#(\d+)")
ITEM_RE = re.compile(r"^- `([^`]+)`：(.+)$", re.M)
# 人工判定表行：| 日期 | #PR | 级别 | 摘要(可含 ×N) | 判定 | 备注 |
LEDGER_ROW_RE = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*#(\d+)\s*\|\s*([^|]*?)\s*\|\s*"
    r"([^|]*?)\s*\|\s*(有效|误报|无法判定|待判定)?[^|]*\|", re.M)
MULTI_RE = re.compile(r"×\s*(\d+)")


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("gh %s: %s" % (" ".join(args), r.stderr[:200]))
    return r.stdout


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_ts(dt):
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")


def fetch(since):
    """拉 PR / runs / 评论，组装成逐 PR 记录。"""
    prs = json.loads(gh([
        "pr", "list", "--repo", os.environ.get("LEDGER_REPO", REPO_DEFAULT),
        "--state", "all", "--limit", "100",
        "--json", "number,title,author,createdAt,state,headRefName"]))
    prs = [p for p in prs if p["createdAt"] >= since]
    prs.sort(key=lambda p: p["createdAt"])

    runs = json.loads(gh([
        "run", "list", "--repo", os.environ.get("LEDGER_REPO", REPO_DEFAULT),
        "--workflow", WORKFLOW, "--limit", "200",
        "--json", "databaseId,createdAt,conclusion,headBranch,displayTitle"]))
    repo = os.environ.get("LEDGER_REPO", REPO_DEFAULT)

    # 哨兵触发的深度检视（历史架构）：run 挂在 ai-review-go 分支，
    # 按 displayTitle 的 PR#N 归属
    sentinel_map = {}
    for r in runs:
        if r["headBranch"] != SENTINEL_BRANCH or r["conclusion"] != "success":
            continue
        hit = SENTINEL_PR_RE.search(r["displayTitle"] or "")
        if hit:
            n = int(hit.group(1))
            sentinel_map[n] = sentinel_map.get(n, 0) + 1

    out = []
    for p in prs:
        # runs 归属：同分支且落在 [本 PR 创建, 下一个同分支 PR 创建) 窗口内
        lo, branch = parse_ts(p["createdAt"]), p["headRefName"]
        later = [parse_ts(q["createdAt"]) for q in prs
                 if q["headRefName"] == branch and q["createdAt"] > p["createdAt"]]
        hi = min(later) if later else datetime.now(timezone.utc)
        n_runs = sum(1 for r in runs if r["headBranch"] == branch
                     and lo <= parse_ts(r["createdAt"]) < hi)

        raw = gh(["api", "repos/%s/issues/%d/comments" % (repo, p["number"])])
        comments = json.loads(raw or "[]")
        has_report = False
        items = []          # [{level, path, desc}]
        for c in comments:
            body = c.get("body", "")
            if "ors-ai-review:report" not in body:
                continue
            has_report = True
            red = sum(int(a or b) for a, b in RED_TOTAL_RE.findall(body))
            # 条目按其上方最近的组标题归层
            level = "AI"
            for line in body.splitlines():
                t = line.strip()
                if t.startswith("### 🔴 规则快筛命中"):
                    level = "快筛"
                elif t.startswith("### 🔴 必须采纳"):
                    level = "AI"
                m = ITEM_RE.match(t)
                if m and not m.group(1).startswith("http"):
                    items.append({"level": level, "path": m.group(1),
                                  "desc": m.group(2)[:110]})
            break   # 只解析最新一份报告（sticky 更新）

        out.append({
            "pr": p["number"], "state": p["state"], "title": p["title"][:38],
            "author": p["author"]["login"], "created": p["createdAt"][:16],
            "runs": n_runs, "report": has_report,
            "sentinel": sentinel_map.get(p["number"], 0),
            "items": items,
        })
    return out


def load_verdicts(eval_path):
    """从 evaluation.md 人工表解析判定：{pr_number: {level: (verdict, n)}}"""
    text = open(eval_path, encoding="utf-8").read() if os.path.exists(eval_path) else ""
    table_m = re.search(r"## 观察期意见台账(.*?)\n## ", text, re.S)
    body = table_m.group(1) if table_m else text
    verdicts = {}
    for date, pr, level, summary, verdict in LEDGER_ROW_RE.findall(body):
        if not verdict:
            continue
        d = verdicts.setdefault(int(pr), {})
        lv = "快筛" if ("快筛" in level or "quick" in level.lower()) else "AI"
        m = MULTI_RE.search(summary)
        d.setdefault(lv, []).append(
            (verdict, int(m.group(1)) if m else 1))
    return verdicts


def render(data, since, verdicts):
    now_cn = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    total = len(data)
    covered = [d for d in data if d["runs"] > 0 or d["sentinel"] > 0]
    deep = [d for d in data if d["report"]]
    items = [(d["pr"], it) for d in data for it in d["items"]]

    # 准确率以人工判定表为权威（按条数 ×N 加权）；流水表仅展示自动提取的意见
    ok = bad = pending = 0
    for d in verdicts.values():
        for rows in d.values():
            for v, n in rows:
                if v == "有效":
                    ok += n
                elif v == "误报":
                    bad += n
                elif v == "待判定":
                    pending += n
    acc = (1.0 * ok / (ok + bad) * 100) if (ok + bad) else None

    # 按天（UTC+8）汇总
    days = {}
    for d in data:
        day = fmt_ts(parse_ts(d["created"]))[:5]
        k = days.setdefault(day, {"prs": 0, "cov": 0, "deep": 0, "items": 0})
        k["prs"] += 1
        k["cov"] += 1 if (d["runs"] > 0 or d["sentinel"] > 0) else 0
        k["deep"] += 1 if d["report"] else 0
        k["items"] += len(d["items"])

    L = []
    L.append("### 📈 运行台账（自动生成，勿手改）")
    L.append("")
    L.append("> 由 `.github/ai-review/scripts/ledger.py` 维护；口径：快筛自检率 ="
             " 有 run 的 PR / 窗内 PR；准确率 = 已判定意见中「有效」占比"
             "（判定以人工台账为准，待判定不计入）。")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append("| 统计窗 | %s 起（%s 生成，共 %d 个 PR） |"
             % (since[:10], now_cn, total))
    L.append("| 快筛自检率 | %d/%d = %d%% |"
             % (len(covered), total,
                round(100.0 * len(covered) / total) if total else 0))
    L.append("| 深度检视（人工打标/哨兵） | %d 个 PR |" % len(deep))
    L.append("| 红档意见 | %d 条（快筛 %d / AI %d） |"
             % (len(items),
                sum(1 for _, it in items if it["level"] == "快筛"),
                sum(1 for _, it in items if it["level"] == "AI")))
    if acc is not None:
        L.append("| 准确率（已判定 %d 条） | 有效 %d / 误报 %d = %d%%；待判定 %d 条 |"
                 % (ok + bad, ok, bad, round(acc), pending))
    else:
        L.append("| 准确率 | 暂无已判定意见 |")
    L.append("")
    L.append("**按天（UTC+8）**：")
    L.append("")
    L.append("| 日期 | PR 数 | 快筛触发 | 深度检视 | 红档意见 |")
    L.append("|---|---|---|---|---|")
    for day in sorted(days):
        k = days[day]
        L.append("| %s | %d | %d | %d | %d |"
                 % (day, k["prs"], k["cov"], k["deep"], k["items"]))
    L.append("")
    gap = [d["pr"] for d in data if not (d["runs"] > 0 or d["sentinel"] > 0)]
    if gap:
        L.append("⚠️ 未触发快筛的 PR：%s（多为上线磨合期；近期若出现需排查触发链路）"
                 % "、".join("#%d" % n for n in gap))
        L.append("")
    if items:
        L.append("**意见流水与判定**：")
        L.append("")
        L.append("| PR | 层 | 条目 | 判定 |")
        L.append("|---|---|---|---|")
        for pr, it in items:
            rows = verdicts.get(pr, {}).get(it["level"])
            vtxt = " / ".join("%s（×%d）" % (v, n) for v, n in rows) \
                if rows else "待判定"
            L.append("| #%d | %s | `%s` %s | %s |"
                     % (pr, it["level"], it["path"], it["desc"], vtxt))
        L.append("")
    return "\n".join(L)


def update_eval(eval_path, block):
    if os.path.exists(eval_path):
        text = open(eval_path, encoding="utf-8").read()
    else:
        text = "# AI Review 误报台账（观察期）\n"
    if MARK_START in text and MARK_END in text:
        pre = text[:text.index(MARK_START)]
        post = text[text.index(MARK_END) + len(MARK_END):]
        text = pre + MARK_START + "\n" + block + "\n" + MARK_END + post
    else:
        anchor = "## 观察期意见台账"
        if anchor in text:
            text = text.replace(anchor,
                                MARK_START + "\n" + block + "\n" + MARK_END
                                + "\n\n" + anchor, 1)
        else:
            text += "\n" + MARK_START + "\n" + block + "\n" + MARK_END + "\n"
    with open(eval_path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    try:   # GBK 控制台打印 emoji 会崩，强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="ISO 时间，如 2026-11-23T06:00Z")
    ap.add_argument("--days", type=int, default=4, help="回看天数（默认 4）")
    ap.add_argument("--eval", default=EVAL_DEFAULT)
    args = ap.parse_args()

    since = args.since or (
        datetime.now(timezone.utc) - timedelta(days=args.days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    data = fetch(since)
    verdicts = load_verdicts(args.eval)
    block = render(data, since, verdicts)
    update_eval(args.eval, block)
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
