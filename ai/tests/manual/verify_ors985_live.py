# -*- coding: utf-8 -*-
"""ORS-985 真实链路验证：本地 AI 服务（9411）跑两个事故场景。

场景A（985 复现）：连提两单，第二单答项目题序号「2」——修复前规划会
联想上一单 AI 号当查单、主 LLM 清空 problem_summary；修复后第二单描述
应包含真实问题内容。

场景B（1019 循环）：项目题后答「我的项目不在里面」+「不在里面，转工单」
——修复前 prepare 反复重出同一道题；修复后 project_none 解除闸门，
prepare 不再重出项目题。
"""
import json
import sys
import uuid

import httpx
import jwt

BASE = "http://127.0.0.1:9411/api/ai/qa"
USER = "admin"
TOKEN = jwt.encode({"sub": USER}, "your-secret-key-here", algorithm="HS256")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def ask(client, session_id, query):
    """一轮对话：收全 SSE，返回 (result_dict, all_events)"""
    events = []
    result = {}
    with client.stream("POST", f"{BASE}/ask/stream", json={
            "session_id": session_id, "query": query,
            "created_by": USER, "username": USER},
            headers=HEADERS, timeout=120.0) as r:
        cur_event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                cur_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and cur_event:
                data = line.split(":", 1)[1].strip()
                events.append((cur_event, data[:60]))
                if cur_event == "result":
                    try:
                        result = json.loads(data)
                    except Exception:
                        pass
    return result, events


def prepare(client, session_id):
    r = client.post(f"{BASE}/ticket/prepare", json={
        "session_id": session_id, "username": USER},
        headers=HEADERS, timeout=60.0)
    j = r.json()
    return j.get("data") or j


def get_draft(client, session_id):
    r = client.get(f"{BASE}/ticket/draft", params={"session_id": session_id},
                   headers=HEADERS, timeout=60.0).json()
    return (r.get("data") or {}) if isinstance(r.get("data"), dict) else {}


def confirm(client, session_id, overrides=None):
    # 前端弹窗会把预填项目带回 overrides——脚本从草稿取预填补上
    if overrides is None:
        # 验证焦点是描述内容；项目按弹窗行为硬带（测试库已知项目）
        overrides = {"project": "摇人吧服务号", "project_id": "001"}
    r = client.post(f"{BASE}/ticket/confirm", json={
        "session_id": session_id, "username": USER,
        "overrides": overrides},
        headers=HEADERS, timeout=60.0)
    return r.json()


def show(tag, res):
    msg = str(res.get("message") or res.get("data", {}).get("message") or "")
    print(f"  [{tag}] code={res.get('code')} stage={res.get('stage')} msg={msg[:80]!r}")
    return res


def main():
    ok = True
    with httpx.Client() as client:
        # ============ 场景A：985 连提两单 ============
        print("=== 场景A（985 复现）：连提两单，第二单答序号「2」 ===")
        sid = f"sess_985a_{uuid.uuid4().hex[:8]}"
        r0, _ = ask(client, sid,
                    "提个功能需求：科钛3D激光导航车跨楼层重定位时，需要调度下发Z轴高度，目前不支持，希望增加支持")
        print(f"  [A.0 描述轮] stage={r0.get('stage')}")
        r1 = prepare(client, sid)  # 按钮路径：985 事故里项目题就是 prepare 出的
        print(f"  [A.1 prepare] project_ask={r1.get('project_ask')} "
              f"choices={len(r1.get('project_choices') or [])} stage={r1.get('stage')}")
        if not r1.get("project_ask"):
            print("  !! prepare 没出项目题（候选空？），场景A无法复现链路")
            return 1
        r2, _ = ask(client, sid, "1")  # 答序号（走预填→自动 review）
        print(f"  [A.2 答序号1] action={r2.get('action')} "
              f"msg={str(r2.get('message'))[:60]!r}")
        r2b, _ = ask(client, sid, "目前是人工手动核对高度的，没有系统支持")
        print(f"  [A.2b 补一轮] action={r2b.get('action')} "
              f"msg={str(r2b.get('message'))[:60]!r}")
        c1 = confirm(client, sid)
        show("A.3 提交第一单", c1)
        if c1.get("code") != 0:
            print("  !! 第一单提交失败，中止")
            return 1

        # 第二单：上下文里已有上一单 AI 号（985 联想温床）
        r3, _ = ask(client, sid,
                    "还有一个问题：调度升级后缓存区库位状态不同步，人工拉走货物后系统仍显示有货，这个也提个单")
        print(f"  [A.4 第二单描述] stage={r3.get('stage')} "
              f"project_ask={r3.get('project_ask')}")
        r3b = prepare(client, sid)
        print(f"  [A.4b prepare] project_ask={r3b.get('project_ask')} "
              f"choices={len(r3b.get('project_choices') or [])} stage={r3b.get('stage')}")
        if not r3b.get("project_ask"):
            print(f"      msg={str(r3b.get('message'))[:70]!r}")
        r4, _ = ask(client, sid, "2")  # 985 案发现场：答序号 2
        print(f"  [A.5 答序号2] action={r4.get('action')} "
              f"msg={str(r4.get('message'))[:70]!r}")
        # decide 可能给第二单挂了动态字段——答齐后草稿才生成
        if r4.get("action") == "ask":
            r4b, _ = ask(client, sid, "升级后第二天开始出现，调度版本 3.2.1")
            print(f"  [A.5b 补字段] action={r4b.get('action')} "
                  f"msg={str(r4b.get('message'))[:60]!r}")
        # 草稿在 memory（ticket_draft），不在 result 顶层——从 prepare 取
        pr = prepare(client, sid)
        draft = pr.get("draft") or {}
        if not draft:
            dr = client.get(f"{BASE}/ticket/draft", params={"session_id": sid},
                            headers=HEADERS, timeout=60.0).json()
            draft = ((dr.get("data") or {}).get("draft") or {}) if isinstance(dr.get("data"), dict) else {}
        desc = str(draft.get("description") or "")
        title = str(draft.get("title") or "")
        print(f"  [A.6 草稿] prepare_stage={pr.get('stage')} draft_ready={bool(draft)}")
        print(f"      title={title[:50]!r}")
        print(f"      desc={desc[:120]!r}")
        # 985 判定：描述包含真实问题（缓存区/库位/货物），绝不出现「仅发送数字」
        bad = "数字2" in desc or "无有效问题描述" in desc or "数字2" in title
        good = any(k in desc for k in ("缓存", "库位", "货物"))
        if bad or not good:
            ok = False
            print("  ❌ 场景A FAIL：描述丢失真实问题")
        else:
            print("  ✅ 场景A PASS：第二单描述含真实问题内容")

        # ============ 场景B：1019 「不在里面」循环 ============
        print("=== 场景B（1019 循环）：项目题 → 不在里面 → 转工单 ===")
        sid2 = f"sess_985b_{uuid.uuid4().hex[:8]}"
        b0, _ = ask(client, sid2, "地图上传后机器人在三号仓定位漂移严重")
        print(f"  [B.0 描述轮] stage={b0.get('stage')}")
        b1 = prepare(client, sid2)
        print(f"  [B.1 prepare] project_ask={b1.get('project_ask')} "
              f"choices={len(b1.get('project_choices') or [])}")
        b2, _ = ask(client, sid2, "我的项目不在里面")
        msg2 = str(b2.get("message") or "")
        print(f"  [B.2 不在里面] msg={msg2[:80]!r}")
        b3, _ = ask(client, sid2, "不在里面，转工单")
        msg3 = str(b3.get("message") or "")
        asked3 = b3.get("project_ask")
        is_project_ask = "关联项目" in msg3 or "回复【序号】" in msg3
        print(f"  [B.3 坚持转工单] project_ask={asked3} msg={msg3[:80]!r}")
        p1 = prepare(client, sid2)
        msg_p1 = str(p1.get("message") or "")
        p1_reask = p1.get("project_ask") or ("关联项目" in msg_p1 and "回复【序号】" in msg_p1)
        print(f"  [B.4 按钮prepare] project_ask={p1.get('project_ask')} "
              f"stage={p1.get('stage')} msg={msg_p1[:60]!r}")
        if p1_reask:
            ok = False
            print("  ❌ 场景B FAIL：用户明确说不在后 prepare 仍重出项目题")
        else:
            print("  ✅ 场景B PASS：不再重出项目题，走弹窗/字段路径")

    print()
    print("=== 结论:", "全部 PASS" if ok else "存在 FAIL", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
