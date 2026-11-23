#!/usr/bin/env python3
"""AI Review：报告落 PR。

两个能力：
1. sticky 总结评论（同一标记只保留一条，更新不堆楼），并对比旧版指纹
   生成「较上次检视：新增/消除」增量行；
2. --inline inline.json：把可定位到 diff 行的意见创建为 review 行内评论，
   出现在 Files changed 页面对应代码行上。

用法：
  python3 post_comment.py --marker "ors-ai-review:report" --file report.md \
    [--inline inline.json]
  env: REPO / PR_NUMBER / GH_TOKEN
"""
import argparse
import json
import os
import re
import sys

from ai_review_common import gh_api

EXIT_OK, EXIT_ERROR = 0, 1
FP_RE = re.compile(r"ors-ai-review:fp=([0-9a-f,]*)")


def find_comment(repo, pr, token, marker):
    """找含标记的最新一条评论 id，找不到返回 None。"""
    page, found = 1, None
    while True:
        status, batch = gh_api(
            repo, f"issues/{pr}/comments?per_page=100&page={page}", token)
        if status != 200 or not isinstance(batch, list):
            break
        for c in batch:
            if marker in (c.get("body") or ""):
                found = c["id"]
        if len(batch) < 100:
            break
        page += 1
    return found


def _extract_fps(body):
    m = FP_RE.search(body or "")
    if not m:
        return None
    return {f for f in m.group(1).split(",") if f}


def fingerprint_delta(old_body, new_body):
    """对比新旧报告指纹，生成「较上次检视」增量行（无旧报告返回 None）。"""
    old = _extract_fps(old_body)
    new = _extract_fps(new_body) or set()
    if old is None:
        return None
    added, removed = new - old, old - new
    if not added and not removed:
        return "> 🔄 较上次检视：意见无变化"
    parts = []
    if added:
        parts.append(f"**新增 {len(added)} 条**")
    if removed:
        parts.append(f"已消除 {len(removed)} 条")
    return f"> 🔄 较上次检视：{'，'.join(parts)}"


def insert_delta(body, delta_line):
    """把增量行插到「结论」行（**AI 检视报告** 标题行的下一行）之后。"""
    lines = body.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("**AI 检视报告**"):
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].startswith("<!--"):
                insert_at += 1
            lines.insert(insert_at + 1, delta_line)
            return "\n".join(lines)
    lines.insert(min(3, len(lines)), delta_line)
    return "\n".join(lines)


def post_review_inline(repo, pr, token, inline_path):
    """创建带行内评论的 review（出现在 Files changed 对应行）。"""
    with open(inline_path, encoding="utf-8") as fh:
        items = json.load(fh)
    if not items:
        print("无可定位的行内意见，跳过 review")
        return True
    comments = []
    for it in items:
        comments.append({
            "path": it["path"],
            "line": it["line"],
            "side": "SUPPORT",
            "body": it["body"],
        })
    status, resp = gh_api(
        repo, f"pulls/{pr}/reviews", token, method="POST",
        body={"event": "COMMENT",
              "body": "🤖 AI 检视：以下意见已钉到具体代码行（详见总结评论）",
              "comments": comments})
    ok = status in (200, 201)
    n = len(resp.get("data", {}).get("comments", comments)) if isinstance(resp, dict) else "?"
    print(f"行内 review {'创建成功' if ok else f'失败（HTTP {status}）'}，"
          f"携带 {len(comments)} 条")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--marker", required=True,
                        help="评论体中的唯一标记，如 ors-ai-review:report")
    parser.add_argument("--file", required=True, help="评论 markdown 文件")
    parser.add_argument("--inline", default=None,
                        help="行内评论数据 JSON（可选，创建 review）")
    args = parser.parse_args()

    repo = os.environ.get("REPO", "")
    pr = os.environ.get("PR_NUMBER", "")
    token = os.environ.get("GH_TOKEN", "")
    if not (repo and pr and token):
        print("缺少 REPO / PR_NUMBER / GH_TOKEN", file=sys.stderr)
        return EXIT_ERROR

    with open(args.file, encoding="utf-8") as fh:
        body = fh.read()
    if not body.strip():
        print("评论内容为空，跳过")
        return EXIT_OK

    existing = find_comment(repo, pr, token, args.marker)
    if existing:
        status, old = gh_api(
            repo, f"issues/comments/{existing}", token)
        old_body = old.get("body", "") if isinstance(old, dict) else ""
        delta = fingerprint_delta(old_body, body)
        if delta:
            body = insert_delta(body, delta)
        status, resp = gh_api(
            repo, f"issues/comments/{existing}", token, method="PATCH",
            body={"body": body})
        action = "更新"
    else:
        status, resp = gh_api(
            repo, f"issues/{pr}/comments", token, method="POST",
            body={"body": body})
        action = "创建"
    ok = status in (200, 201)
    print(f"评论{action}{'成功' if ok else f'失败（HTTP {status}）'}")
    if ok and isinstance(resp, dict):
        print(f"  {resp.get('html_url', '')}")
    if ok and args.inline:
        post_review_inline(repo, pr, token, args.inline)
    return EXIT_OK if ok else EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
