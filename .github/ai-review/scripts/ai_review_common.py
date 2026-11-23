#!/usr/bin/env python3
"""AI Review 共享工具：GitHub API 访问、diff 拉取、企微推送、时间格式。

设计约束（与 deploy/notify.py 同口径）：
- 仅用标准库（urllib），托管 runner 的 python3 零依赖直接跑；
- 日志只打印 Webhook 主机名与 API 路径，绝不回显 token / 完整 webhook URL；
- 时间戳一律 UTC。
"""
import fnmatch
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

GITHUB_API = "https://api.github.com"
ALLOWED_HOSTS = ("qyapi.weixin.qq.com", "open.feishu.cn")
POLICIES = ("always", "failure", "success", "off")
# AI Review 属于 test 口径的提醒，envs 匹配按此值路由
REVIEW_ENV = "test"

# 快筛/深度检视都要排除的「无语义」文件：lock 文件与纯文档
LOCK_PATTERNS = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "uv.lock", "Pipfile.lock", "composer.lock", "Gemfile.lock",
)
DOC_SUFFIXES = (".md", ".rst", ".txt")


def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def gh_api(repo, path, token, method="GET", body=None, accept=None):
    """调用 GitHub API，返回 (status, 解析后的对象或原文 str)。

    404/403 等非 2xx 不抛异常，由调用方按状态码降级处理。
    """
    url = f"{GITHUB_API}/repos/{repo}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept or "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read(4 * 1024 * 1024).decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read(4000).decode("utf-8", "replace")
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # 网络抖动不抛异常：返回合成状态码，由调用方按非 200 降级
        return 599, str(exc)
    if accept and "diff" in accept:
        return status, raw
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def fetch_pr_files(repo, pr_number, token, per_page=100):
    """拉取 PR 变更文件列表（分页）。失败返回 (status, None)。"""
    files, page = [], 1
    while True:
        status, batch = gh_api(
            repo, f"pulls/{pr_number}/files?per_page={per_page}&page={page}", token)
        if status != 200 or not isinstance(batch, list):
            return status, None
        files.extend(batch)
        if len(batch) < per_page:
            return 200, files
        page += 1


def fetch_pr_meta(repo, pr_number, token):
    """拉取 PR 基本信息（标题/正文/分支/sha）。供 workflow_dispatch 场景使用。"""
    return gh_api(repo, f"pulls/{pr_number}", token)


def is_lock_file(path):
    base = path.rsplit("/", 1)[-1]
    return base in LOCK_PATTERNS or fnmatch.fnmatch(base, "*.lock")


def split_code_and_docs(files):
    """把变更文件分成 (代码类, 文档类)。lock 文件两边都不进。"""
    code, docs = [], []
    for f in files:
        path = f.get("filename", "")
        if is_lock_file(path):
            continue
        if path.lower().endswith(DOC_SUFFIXES):
            docs.append(f)
        else:
            code.append(f)
    return code, docs


def parse_targets(raw):
    """解析 NOTIFY_WEBHOOKS：`<url>|<policy>|<群名>|<envs>[, ...]`。

    与 deploy/notify.py 同款格式；envs 内部分隔符为分号，缺省=所有环境。
    """
    targets = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]
        url = parts[0]
        policy = (parts[1] if len(parts) > 1 and parts[1] else "always").lower()
        label = parts[2] if len(parts) > 2 and parts[2] else ""
        envs = {e.lower() for e in (parts[3] if len(parts) > 3 and parts[3] else "").split(";") if e}
        if policy not in POLICIES:
            policy = "always"
        targets.append((url, policy, label, envs))
    return targets


def allowed_webhook(url):
    """校验 https + 企微/飞书域名白名单，避免误配产生 SSRF。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_HOSTS)


def masked(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path[:12]}..."


def wecom_markdown(webhook, content):
    """推送企微 markdown 消息，返回 (ok, 说明)。失败不抛异常。"""
    payload = json.dumps(
        {"msgtype": "markdown", "markdown": {"content": content[:4000]}},
        ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read(2000).decode("utf-8", "replace"))
            return body.get("errcode") == 0, f"errcode={body.get('errcode')}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, str(exc)


def parse_new_side_lines(patch):
    """解析 patch 新侧（+ 行与上下文行）的行号集合，供行内评论落点校验。"""
    lines = set()
    for m in re.finditer(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", patch or ""):
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        lines.update(range(start, start + max(count, 1)))
    return lines


def finding_fingerprint(location, issue):
    """意见指纹：用于两次检视间的增量对比（新增/消除）。"""
    import hashlib
    raw = f"{location}|{(issue or '')[:40]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def strip_patch_noise(patch):
    """取 patch 中的新增行（去掉行首 + 与行号头），供内容规则匹配。"""
    added = []
    for line in (patch or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return added


def guess_line_number(patch):
    """从 patch 头估算新增块起始行号，供报告定位（估算值，供参考）。"""
    m = re.search(r"@@ -\d+(?:,\d+)? \+(\d+)", patch or "")
    return int(m.group(1)) if m else None
