"""
脚本化实测：对运行中的服务跑全场景矩阵（全部走 /ask/stream 流式）

矩阵：
  problem  对话提单 / 按钮提单 / 信息不足点按钮(not_ready→补齐→再点)
  feature  对话提单 / 按钮提单
  bug      对话提单（版本+复现步骤）
  咨询类   纯问答不提单（不应出现 submitted）
  催促拦截 信息不足时"别问了直接提"（不应提单）
  闭环保护 提单后再 prepare / 再说转工单（应拦截）
  pending  信息齐但缺 project → 转工单 → 补项目 → 自动提单

用法: python ai/tools/live_dialog_test.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import time
import requests

BASE_STREAM = "http://127.0.0.1:8401/api/ai/qa/ask/stream"
BASE_PREPARE = "http://127.0.0.1:8401/api/ai/qa/ticket/prepare"
BASE_CONFIRM = "http://127.0.0.1:8401/api/ai/qa/ticket/confirm"
SESSION = requests.Session()
RESULTS = {}


def record(name, ok, detail=""):
    RESULTS[name] = ok
    print(f"  >>> {'✅ PASS' if ok else '❌ FAIL'}  {name}  {detail}")


def ask_stream(sid: str, query: str):
    """走流式接口，打印 token 流，返回 (消息文本, status stages 列表)"""
    print(f"\n👤 > {query}")
    resp = SESSION.post(BASE_STREAM, json={"session_id": sid, "query": query},
                        stream=True, timeout=(5, 180))
    resp.raise_for_status()
    current_event = None
    stages = []
    first_token = True

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            current_event = line[7:].strip()
            continue
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except json.JSONDecodeError:
            continue

        if "token" in ev:
            if first_token:
                print("🤖 ", end="", flush=True)
                first_token = False
            print(ev["token"], end="", flush=True)

        if current_event == "status" and isinstance(ev, dict):
            stage = ev.get("stage", "")
            stages.append(stage)
            if stage == "submitted":
                print(f"\n🎫 工单已生成 ticket_id={ev.get('ticket_id')} title={ev.get('title')} db_id={ev.get('db_id')}")
            elif stage == "submit_failed":
                print(f"\n❌ 提单失败: {ev.get('error')}")
            elif stage == "need_fields":
                print(f"\n⚠️  还缺(弹窗字段): {ev.get('missing_fields')}  {ev.get('prompt', '')}")
            elif stage == "need_info":
                print(f"\n⚠️  信息不足: {ev.get('missing_info')}")
            current_event = None
        elif current_event == "result":
            current_event = None
    print()
    return stages


def conv(sid, turns):
    """跑一串对话，返回所有轮累计的 status stages"""
    all_stages = []
    for q in turns:
        all_stages += ask_stream(sid, q)
        time.sleep(0.3)
    return all_stages


def prepare(sid):
    r = SESSION.post(BASE_PREPARE, json={"session_id": sid}, timeout=30).json()
    return r.get("data", {})


def confirm(sid, overrides=None):
    r = SESSION.post(BASE_CONFIRM, json={"session_id": sid, "overrides": overrides or {}},
                     timeout=30).json()
    return r.get("data", {})


def button_path(sid, project_override=None):
    """prepare -> (need_fields 则用 overrides 补) -> confirm，返回 ticket dict 或 None"""
    inner = prepare(sid)
    stage = inner.get("stage", "")
    if inner.get("code") == 1 or stage == "not_ready":
        print(f"  prepare -> not_ready: missing={inner.get('missing_info')}")
        return None
    draft = inner.get("draft", {})
    print(f"  prepare -> {stage}: type={draft.get('type')} title={draft.get('title')!r} project={draft.get('project')!r}")
    ov = {}
    if stage == "need_fields":
        print(f"  缺弹窗字段 {inner.get('missing_fields')}，confirm 时补: {project_override}")
        if project_override:
            ov["project"] = project_override
    c = confirm(sid, ov)
    if c.get("code") == 1:
        print(f"  confirm FAIL: {c.get('message')}  missing={c.get('missing_info') or c.get('missing_fields')}")
        return None
    # confirm 路由不包装，confirm() 已剥外层 → 成功时 c = {ticket, db_id, notice}
    t = c.get("ticket", {}) or {}
    t["db_id"] = c.get("db_id")
    print(f"  confirm OK: 🎫 {t.get('title')!r} type={t.get('type')} db_id={t.get('db_id')}")
    return t


def header(txt):
    print("\n" + "=" * 70 + f"\n  {txt}\n" + "=" * 70)


def main():
    ts = int(time.time())

    # ── 1. problem 对话提单（中途被拦要项目 → 补项目 → pending 自动提单）──
    sid = f"t1-prob-conv-{ts}"
    header(f"1. problem 对话提单 ({sid})")
    stages = conv(sid, [
        "昨天11点46 我的XNA161突然在路上不动了",
        "没错误码",
        "每次都这样，今天已经发生三次了",
        "转工单",
        "本川项目",
    ])
    record("problem对话提单", "submitted" in stages, f"stages={stages}")

    # ── 2. problem 按钮提单 ─────────────────────────────
    sid = f"t2-prob-btn-{ts}"
    header(f"2. problem 按钮提单 ({sid})")
    conv(sid, [
        "我的XNA161昨天开始不动，每次启动都这样，本川项目",
    ])
    t = button_path(sid, project_override="本川")
    record("problem按钮提单", t is not None and t.get("type") == "problem",
           f"type={t.get('type') if t else None}")

    # ── 3. 信息不足点按钮 → not_ready → 补齐 → LLM 可能自动提交或再按钮提单 ──
    sid = f"t3-prob-notready-{ts}"
    header(f"3. problem 信息不足点按钮 ({sid})")
    conv(sid, ["我的车不动了"])
    inner = prepare(sid)
    not_ready = inner.get("code") == 1 or inner.get("stage") == "not_ready"
    record("not_ready拦截", not_ready, f"missing={inner.get('missing_info')}")
    print(f"  prepare -> {'not_ready ✅' if not_ready else '❌ 竟然放行了'}: missing={inner.get('missing_info')}")
    stages3 = conv(sid, [
        "车型是XNA161，昨天下午开始的，每次都这样",
        "本川项目",
    ])
    # LLM 可能在对话中直接提交了
    if "submitted" in stages3:
        record("补齐后按钮提单", True, "LLM auto-submitted after filling details")
    else:
        t = button_path(sid, project_override="本川")
        record("补齐后按钮提单", t is not None, "")

    # ── 4. feature 对话提单 ─────────────────────────────
    sid = f"t4-feat-conv-{ts}"
    header(f"4. feature 对话提单 ({sid})")
    stages = conv(sid, [
        "我有一个需求 我希望usp加一个航向角的功能",
        "因为货架有倾斜 所以在进入库区分支时需要一个航向角的参数来调整",
        "在库位管理里面给每个库位配置一个航向角，任务下发时传给车，车自动调整",
        "本川项目",
        "好的帮我提单吧",
    ])
    record("feature对话提单", "submitted" in stages, f"stages={stages}")

    # ── 5. feature 按钮提单（先点按钮被拦 → 补齐信息 → LLM 可能自动提交或再按钮提交）──
    sid = f"t5-feat-btn-{ts}"
    header(f"5. feature 按钮提单 ({sid})")
    conv(sid, [
        "我希望USP在库位配置里加一个航向角字段",
    ])
    inner = prepare(sid)
    print(f"  先点按钮 -> {inner.get('stage') or inner.get('message')}: missing={inner.get('missing_info')}")
    stages5 = conv(sid, [
        "场景是货架倾斜AGV进库位要调方向，期望效果是任务下发时把角度传给车自动调整，项目本川",
    ])
    # LLM 可能在对话中直接提交了（安全网/action=submit）
    if "submitted" in stages5:
        record("feature按钮提单", True, "LLM auto-submitted after filling details")
    else:
        t = button_path(sid, project_override="本川")
        record("feature按钮提单", t is not None and t.get("type") == "feature",
               f"type={t.get('type') if t else None}")

    # ── 6. bug 对话提单（转工单被拦要项目 → 补项目 → pending 自动提单）──
    sid = f"t6-bug-conv-{ts}"
    header(f"6. bug 对话提单 ({sid})")
    stages = conv(sid, [
        "USP有个缺陷，库位编辑页面保存后数据丢失",
        "版本是2.3.1，复现步骤：打开库位编辑，改一个字段，点保存，刷新后修改没了",
        "转工单",
        "本川项目",
    ])
    record("bug对话提单", "submitted" in stages, f"stages={stages}")

    # ── 7. 纯咨询不提单 ─────────────────────────────────
    sid = f"t7-howto-{ts}"
    header(f"7. 纯咨询不提单 ({sid})")
    stages = conv(sid, [
        "USP怎么添加新地图？",
        "好的谢谢",
    ])
    record("纯咨询不提单", "submitted" not in stages, f"stages={stages}")

    # ── 8. 催促拦截 ─────────────────────────────────────
    sid = f"t8-impatient-{ts}"
    header(f"8. 催促拦截 ({sid})")
    stages = conv(sid, [
        "车不动了",
        "别问了直接给我提工单",
    ])
    record("催促拦截", "submitted" not in stages, f"stages={stages}")

    # ── 9. 闭环保护 ─────────────────────────────────────
    sid = f"t9-closed-{ts}"
    header(f"9. 闭环保护 ({sid})")
    stages0 = conv(sid, [
        "XNA161昨天不动了，每次都这样，本川项目",
        "转工单",
        "本川项目",
    ])
    print(f"  前置提单: stages={stages0}")
    inner = prepare(sid)
    blocked = inner.get("code") == 1
    print(f"  提单后再 prepare -> {inner.get('message') or inner.get('stage')}")
    stages2 = conv(sid, ["再帮我转一个工单"])
    record("闭环拦截prepare", blocked, f"msg={inner.get('message', '')[:30]}")
    record("闭环拦截对话", "submitted" not in stages2, f"stages={stages2}")

    # ── 10. pending_submit 自动提单 ─────────────────────
    sid = f"t10-pending-{ts}"
    header(f"10. pending_submit 自动提单 ({sid})")
    stages = conv(sid, [
        "XNA161昨天在路上不动了，每次都这样",
        "转工单",
        "本川项目",
    ])
    record("pending补项目自动提单", "submitted" in stages, f"stages={stages}")

    # ── 汇总 ─────────────────────────────────────────────
    header("汇总")
    for k, v in RESULTS.items():
        print(f"  {'✅' if v else '❌'}  {k}")
    print(f"\n  {sum(RESULTS.values())}/{len(RESULTS)} 通过")


if __name__ == "__main__":
    main()
