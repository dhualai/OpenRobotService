#!/usr/bin/env python3
"""AI Review：高危项推企微（仅 blocker 才打扰，走开源小组群）。

复用 NOTIFY_WEBHOOKS secret（格式与 deploy/notify.py 一致），
按 envs=test 口径路由：envs 限 prod 的目标自动跳过（如【USP】群）。

用法：
  python3 notify_wecom.py --file wecom.md
  env: NOTIFY_WEBHOOKS / NOTIFY_WEBHOOK / REPO / PR_NUMBER
发送失败只告警并以 0 退出——提醒不应影响流程判定。
"""
import argparse
import os
import sys

from ai_review_common import (
    REVIEW_ENV, allowed_webhook, masked, parse_targets, wecom_markdown,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="企微 markdown 文案文件")
    args = parser.parse_args()

    raw = os.environ.get("NOTIFY_WEBHOOKS") or os.environ.get("NOTIFY_WEBHOOK")
    if not raw:
        print("未配置 NOTIFY_WEBHOOKS / NOTIFY_WEBHOOK，跳过企微提醒")
        return 0

    with open(args.file, encoding="utf-8") as fh:
        content = fh.read()
    if not content.strip():
        print("企微文案为空，跳过")
        return 0

    sent = skipped = 0
    for index, (url, policy, label, envs) in enumerate(parse_targets(raw), 1):
        name = label or f"#{index}"
        if envs and REVIEW_ENV not in envs:
            print(f"[{name}] 环境范围为 {'/'.join(sorted(envs))}，"
                  f"AI Review 按 {REVIEW_ENV} 口径跳过 {masked(url)}")
            skipped += 1
            continue
        if policy not in ("always", "failure"):
            print(f"[{name}] 策略 {policy} 不适用提醒，跳过")
            skipped += 1
            continue
        if not allowed_webhook(url):
            print(f"[{name}] 不在白名单（仅允许企微/飞书域名），跳过 {masked(url)}")
            skipped += 1
            continue
        ok, detail = wecom_markdown(url, content)
        if ok:
            sent += 1
            print(f"[{name}] 已发送")
        else:
            print(f"[{name}] 发送失败（{detail}），不影响流程")
    print(f"企微提醒完成：已发送 {sent}，跳过 {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
