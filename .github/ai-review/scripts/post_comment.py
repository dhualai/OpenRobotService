#!/usr/bin/env python3
"""AI Review：把报告落到 PR 评论（sticky：同一标记只保留一条，更新不堆楼）。

用法：
  python3 post_comment.py --marker "ors-ai-review:report" --file report.md
  env: REPO / PR_NUMBER / GH_TOKEN
"""
import argparse
import json
import os
import sys

from ai_review_common import gh_api

EXIT_OK, EXIT_ERROR = 0, 1


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--marker", required=True,
                        help="评论体中的唯一标记，如 ors-ai-review:report")
    parser.add_argument("--file", required=True, help="评论 markdown 文件")
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
    return EXIT_OK if ok else EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
