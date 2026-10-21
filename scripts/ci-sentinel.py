#!/usr/bin/env python3
"""部署工作流哨兵：检测 test 上的 deploy-split.yml 是否被意外改动。

背景
----
2026-10-20 的 PR #338 在功能分支 merge main 时，把
.github/workflows/deploy-split.yml 整份解冲突成了 main 的旧版，删掉
结构门禁、40x5s 健康窗口、gate outcome 标注，导致次日部署报
1054 Unknown column。

仓库侧的 CI Guard（.github/workflows/ci-guard.yml）只能拦 PR，
挡不住自批自合。本哨兵跑在**本地机器**上，基线也存在本地
（默认 ~/.codebuddy/ci-sentinel/），仓库里任何人改不到，
因此是唯一不可被内部绕过的检测手段。

用法
----
    python scripts/ci-sentinel.py              # 检查，有变更返回退出码 2
    python scripts/ci-sentinel.py --update     # 确认变更无误后更新基线
    python scripts/ci-sentinel.py --ref origin/dev   # 临时检查别的分支
    python scripts/ci-sentinel.py --webhook https://qyapi.weixin.qq.com/...  # 变更时推企微

退出码
------
    0  基线一致且关键能力完整
    2  文件有变更，或关键能力缺失
    3  执行出错（fetch 失败、文件缺失等）
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TARGET = ".github/workflows/deploy-split.yml"

# 部署工作流必须保留的关键能力：(说明, 片段)
ASSERTIONS: list[tuple[str, str]] = [
    ("结构门禁参数", "--schema-mode"),
    ("结构模式覆盖开关", "SCHEMA_MODE_OVERRIDE"),
    ("schema_sync 打包", "backend/scripts/schema_sync.py"),
    ("结构模式输出", "DEPLOY_SCHEMA_MODE"),
    ("结构阻塞标记", "DEPLOY_SCHEMA_BLOCKED"),
    ("健康窗口 40 次", "--health-retries 40"),
    ("健康间隔 5s", "--health-interval 5"),
    ("pytest 结果透出", "pytest_outcome"),
    ("vitest 结果透出", "vitest_outcome"),
]


def sh(*args: str, cwd: Path) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).decode("utf-8", "ignore").strip())
    return r.stdout.decode("utf-8", "ignore")


def baseline_path(repo: Path) -> Path:
    return Path.home() / ".codebuddy" / "ci-sentinel" / f"{repo.name}.json"


def load_baseline(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_baseline(path: Path, ref: str, content: bytes, missing: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "ref": ref,
                "file": TARGET,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "captured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "missing_assertions": missing,
                "content": content.decode("utf-8", "ignore"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def notify_wecom(webhook: str, title: str, body: str) -> None:
    payload = json.dumps(
        {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n{body}"}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:  # 通知失败不影响判定结果
        print(f"[warn] 企微通知发送失败：{exc}")


def recent_commits(repo: Path, ref: str, limit: int = 5) -> str:
    try:
        return sh(
            "git", "log", f"-{limit}", "--format=%h  %ad  %an  %s",
            "--date=format:%m-%d %H:%M", ref, "--", TARGET, cwd=repo,
        )
    except RuntimeError:
        return "（无法读取提交历史）"


def main() -> int:
    ap = argparse.ArgumentParser(description="检测部署工作流是否被意外改动")
    ap.add_argument("--repo", default=".", help="仓库路径，默认当前目录")
    ap.add_argument("--ref", default="origin/test", help="要检查的引用，默认 origin/test")
    ap.add_argument("--file", default=TARGET, help="目标文件，默认部署工作流")
    ap.add_argument("--baseline", help="基线文件路径，默认 ~/.codebuddy/ci-sentinel/<repo>.json")
    ap.add_argument("--update", action="store_true", help="把当前内容写入基线（确认变更无误后使用）")
    ap.add_argument("--no-fetch", action="store_true", help="不执行 git fetch")
    ap.add_argument("--webhook", help="企业微信机器人 webhook，变更时推送")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    repo = Path(args.repo).expanduser().resolve()
    bpath = Path(args.baseline).expanduser() if args.baseline else baseline_path(repo)
    ref = args.ref

    try:
        if not args.no_fetch and "/" in ref:
            remote = ref.split("/", 1)[0]
            sh("git", "fetch", remote, "--quiet", cwd=repo)
        content = subprocess.run(
            ["git", "show", f"{ref}:{args.file}"], cwd=repo, capture_output=True
        )
        if content.returncode != 0:
            raise RuntimeError(f"无法读取 {ref}:{args.file}")
        content_bytes = content.stdout
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"[error] {exc}")
        return 3

    text = content_bytes.decode("utf-8", "ignore")
    digest = hashlib.sha256(content_bytes).hexdigest()
    missing = [name for name, frag in ASSERTIONS if frag not in text]

    base = load_baseline(bpath)

    if args.update or base is None:
        save_baseline(bpath, ref, content_bytes, missing)
        reason = "已更新基线" if args.update else "首次运行，已建立基线"
        print(f"[ok] {reason}：{ref}  {digest[:16]}  {len(content_bytes)} 字节")
        print(f"     基线文件：{bpath}")
        if missing:
            print(f"[warn] 当前版本仍缺少：{', '.join(missing)}")
        return 0

    changed = base.get("sha256") != digest
    escaped = set(missing) - set(base.get("missing_assertions") or [])
    regressions = [name for name, _ in ASSERTIONS if name in escaped]

    if not changed and not regressions:
        print(f"[ok] 部署工作流未变更  {ref}  {digest[:16]}")
        return 0

    lines = []
    lines.append(f"检测到 {ref}:{args.file} 发生变化")
    lines.append(f"  基线 {base.get('captured_at', '?')}  {str(base.get('sha256'))[:16]}  {base.get('bytes')} 字节")
    lines.append(f"  当前 {datetime.now().astimezone().isoformat(timespec='seconds')}  {digest[:16]}  {len(content_bytes)} 字节")
    lines.append("")
    lines.append("最近改动该文件的提交：")
    lines.append(recent_commits(repo, ref))

    old = (base.get("content") or "").splitlines()
    new = text.splitlines()
    diff = list(difflib.unified_diff(old, new, fromfile="baseline", tofile=ref, lineterm="", n=0))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    lines.append("")
    lines.append(f"内容差异：+{added} / -{removed} 行")
    for l in diff[:40]:
        if l.startswith(("+++", "---", "@@")):
            continue
        lines.append(f"  {l[:150]}")
    if len(diff) > 40:
        lines.append(f"  ...（其余 {len(diff) - 40} 行省略）")

    if regressions:
        lines.append("")
        lines.append(f"关键能力缺失：{', '.join(regressions)}")
        lines.append("这通常意味着 CI 被回退（例如误合并 main 旧版），请立即检查。")

    report = "\n".join(lines)
    print(report)

    if args.webhook:
        notify_wecom(args.webhook, "部署工作流被改动", report)

    print("")
    print("确认这次变更是有意的 → python scripts/ci-sentinel.py --update")
    return 2


if __name__ == "__main__":
    sys.exit(main())
