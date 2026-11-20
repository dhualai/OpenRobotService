# -*- coding: utf-8 -*-
"""读取 GitHub Release 里「上次成功的发行说明」，写回 RELEASE_NOTES.md。

生产部署通知卡片的「更新日志」区内容来自这个文件（见 deploy/notify.py 的
`RELEASE_NOTES_FILE`）。这一步原先在 workflow 里用

    gh api repos/.../releases/tags/prod-release-notes --jq '.body'

直接取 body。但 Release 不存在时，gh 会把 404 的**错误 JSON 打到 stdout**
（错误提示走 stderr，被 `2>/dev/null` 丢掉），`--jq` 根本不生效——那段
`{"message":"Not Found", ...}` 会被当成发行说明写进文件，把手写版覆盖成
134 字节的脏内容。通知侧解析不到「## 未发布」区 → 更新日志区为空 → 卡片
退化成没有样式的 standard 版面（只剩键值对）。

本脚本只认「响应里 body 字段确实是字符串」的情况，其余（404、无 Release、
响应不是 JSON、body 为空）一律视为「没有历史发行说明」，沿用手写版，
既不写脏文件，也不阻断构建。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

RELEASE_TAG = "prod-release-notes"


def pick_body(raw):
    """从 gh api 的原始 stdout 里取出 Release body；不是有效 body 时返回空串。

    刻意不看 gh 的退出码：HTTP 错误时 gh 同样把响应体打到 stdout 并非 0 退出，
    唯一可靠的判据是「解析出来的 JSON 里 body 是字符串」。
    """
    if not raw or not raw.strip():
        return ""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    body = payload.get("body")
    return body if isinstance(body, str) else ""


def fetch_body(repo, tag=RELEASE_TAG, runner=subprocess.run):
    """调 gh api 取 Release 的 body；命令失败或响应异常时返回空串。"""
    result = runner(
        ["gh", "api", f"repos/{repo}/releases/tags/{tag}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return pick_body(result.stdout or "")


def write_notes(body, out_path):
    """把 body 写入文件；body 为空时**不碰**已有文件，返回 False。"""
    if not body or not body.strip():
        return False
    text = body if body.endswith("\n") else body + "\n"
    Path(out_path).write_text(text, encoding="utf-8")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="取上次成功的生产发行说明")
    parser.add_argument("--repo", required=True, help="仓库名，如 owner/repo")
    parser.add_argument("--tag", default=RELEASE_TAG, help="Release tag")
    parser.add_argument("--out", required=True, help="输出文件路径")
    args = parser.parse_args(argv)

    try:
        body = fetch_body(args.repo, args.tag)
    except Exception as exc:          # 任何异常都不得阻断构建
        print(f"[release-notes] 读取 Release 失败：{type(exc).__name__}: {exc}")
        return 0

    if write_notes(body, args.out):
        size = Path(args.out).stat().st_size
        print(f"已用上次成功的发行说明覆盖人工版（{size} 字节）")
    else:
        print("没有历史发行说明（首次发布或读取失败），沿用 docs/RELEASE_NOTES.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
