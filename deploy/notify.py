#!/usr/bin/env python3
"""部署 / 回滚结果通知（企业微信或飞书群机器人）。

由 GitHub Actions 通过环境变量驱动（见 .github/workflows/deploy.yml、rollback.yml）：

- 支持同时推送到多个群：`NOTIFY_WEBHOOKS` 用逗号分隔多个 Webhook，每项可写
  `<url>|<policy>|<群名>`（后两段可省略，例 `url1|always|研发群,url2|failure|运维群`）；
  未配置时回退到单个 `NOTIFY_WEBHOOK`；
- 未配置任何 Webhook 时静默跳过，不影响部署结果；
- Webhook 主机做域名白名单校验，避免误配成内网地址产生 SSRF；
- 任何发送失败都只打印告警并以 0 退出——通知不应影响部署判定；
- 日志只打印 Webhook 主机名，绝不回显完整 URL（其路径含机器人密钥）。

环境变量：
  NOTIFY_WEBHOOKS  多群推送：`<url>|<policy>|<群名>[, ...]`（policy、群名均可省略）
                   policy = always（默认，每条都发）| failure（仅失败/自动回滚）
                            | success（仅成功）| off（永久禁发，仅留档 URL）
                   群名只是日志标签，用来一眼看出哪个群发送/跳过了
                   注：没写进本变量的群本来就不会收到通知（不在名单 = 不发）
  NOTIFY_WEBHOOK   单群 Webhook（旧变量，作为 NOTIFY_WEBHOOKS 的回退，等价于 always）
  NOTIFY_PROVIDER  wecom（默认）| feishu
  NOTIFY_STATUS    success | failure | cancelled（用于判定成功与否）
  NOTIFY_TITLE     动作名，如「部署」「回滚」
  ENV_NAME         目标环境 test|prod
  COMPONENTS       组件（部署时有意义）
  GIT_REF          代码分支
  COMMIT_SHA       提交号
  SKIP_GATE        "true" 表示已跳过测试门禁
  AUTO_ROLLBACK    "yes" 表示已自动回滚
  ACTOR            触发人
  RUN_URL          Actions 运行链接
"""
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

ALLOWED_HOSTS = ("qyapi.weixin.qq.com", "open.feishu.cn")
POLICIES = ("always", "failure", "success", "off")


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def masked(url):
    """只保留主机名与路径前缀，避免 Webhook 密钥泄漏进日志。"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path[:12]}..."


def build_message():
    ok = env("NOTIFY_STATUS", "success") == "success"
    action = env("NOTIFY_TITLE", "部署")
    icon = "✅" if ok else "❌"

    scope = env("ENV_NAME") or "-"
    if env("COMPONENTS"):
        scope += f" / {env('COMPONENTS')}"
    lines = [f"{icon} {action}{'成功' if ok else '失败'}（{scope}）"]

    if env("GIT_REF"):
        lines.append(f"分支: {env('GIT_REF')} @ {env('COMMIT_SHA')[:8]}")
    if env("SKIP_GATE") == "true":
        lines.append("⚠️ 已跳过测试门禁（紧急发布，请事后补测）")
    if env("AUTO_ROLLBACK") == "yes":
        lines.append("⚠️ 健康检查未通过，已自动回滚到部署前版本，请人工确认服务状态")
    if not ok:
        lines.append("请到 Actions 日志查看失败原因")
    if env("ACTOR"):
        lines.append(f"操作人: {env('ACTOR')}")
    if env("RUN_URL"):
        lines.append(f"详情: {env('RUN_URL')}")
    return "\n".join(lines)


def send(provider, webhook, text):
    """按平台格式 POST。企业微信走 markdown，飞书走纯文本。"""
    if provider == "feishu":
        payload = {"msg_type": "text", "content": {"text": text}}
    else:
        payload = {"msgtype": "markdown", "markdown": {"content": text}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read(2000).decode("utf-8", "replace")


def allowed_webhook(url):
    """校验 https + 域名白名单，避免误配成内网地址产生 SSRF。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_HOSTS)


def parse_targets(raw):
    """解析 `NOTIFY_WEBHOOKS`：`<url>|<policy>|<群名>[, ...]`（后两段可省略）。"""
    targets = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]   # Webhook URL 本身不含 `|`
        url = parts[0]
        policy = (parts[1] if len(parts) > 1 and parts[1] else "always").lower()
        label = parts[2] if len(parts) > 2 and parts[2] else ""
        if policy not in POLICIES:
            print(f"未识别的通知策略（{policy}），按 always 处理")
            policy = "always"
        targets.append((url, policy, label))
    return targets


def wants(policy, ok):
    """按目标策略判断本次结果是否需要通知。"""
    return policy == "always" or policy == ("success" if ok else "failure")


def main():
    ok = env("NOTIFY_STATUS", "success") == "success"

    raw = env("NOTIFY_WEBHOOKS")
    if raw:
        targets = parse_targets(raw)
    else:
        single = env("NOTIFY_WEBHOOK")
        if not single:
            print("未配置 NOTIFY_WEBHOOKS / NOTIFY_WEBHOOK，跳过通知")
            return 0
        targets = [(single, "always", "")]

    if not targets:
        print("NOTIFY_WEBHOOKS 未解析出有效目标，跳过通知")
        return 0

    provider = env("NOTIFY_PROVIDER", "wecom").lower()
    if provider not in ("wecom", "feishu"):
        print(f"NOTIFY_PROVIDER 取值非法（{provider}），按 wecom 处理")
        provider = "wecom"

    text = build_message()
    sent = skipped = 0
    for index, (url, policy, label) in enumerate(targets, 1):
        name = label or f"#{index}"
        if not allowed_webhook(url):
            print(f"[{name}] 不在白名单（仅允许 https 的企业微信 / 飞书域名），"
                  f"跳过 {masked(url)}")
            skipped += 1
            continue
        if policy == "off":
            print(f"[{name}] 已禁用（策略 off），跳过 {masked(url)}")
            skipped += 1
            continue
        if not wants(policy, ok):
            print(f"[{name}] 策略为 {policy}，本次结果为 "
                  f"{'成功' if ok else '失败'}，按策略跳过 {masked(url)}")
            skipped += 1
            continue
        try:
            status, body = send(provider, url, text)
            print(f"[{name}] 已发送（{provider} → {masked(url)}）: HTTP {status} {body[:200]}")
            sent += 1
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[{name}] 发送失败（{masked(url)}，不影响部署结果）: {exc}")
    print(f"通知完成：目标 {len(targets)} 个，已发送 {sent} 个，跳过 {skipped} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
