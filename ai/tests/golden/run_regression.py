# -*- coding: utf-8 -*-
"""四维度回归测试 runner（0901 建立）——治「每次跑测试想一出是一出」。

基准用例集在 cases/*.yaml（唯一真源），四个维度：
  retrieval  无 LLM 秒级，改检索/知识库后必跑   入口 _retrieve_with_context
  answer     真实 LLM 单轮，改 prompt 后跑      入口 _agent_think_stream
  ticket     行为层多轮 + 状态机层(pytest)      入口 _agent_think_stream
  flow       待补充询问/项目引导铁律            入口 _agent_think_stream

用法：
  python ai/tests/golden/run_regression.py                      # 全跑
  python ai/tests/golden/run_regression.py --suite retrieval     # 只跑检索
  python ai/tests/golden/run_regression.py --suite answer --judge
  python ai/tests/golden/run_regression.py --add retrieval --query "错误码10701是什么" --expect "10701"

断言分两级：strict: true 的用例失败=FAIL；否则只 WARN（LLM 行为有波动，
软失败提示人工看明细，不阻塞）。检索维度全部硬断言（纯机械稳定）。
明细 JSON 落 OpenRobotService_Data/regression_<时间戳>.json。
"""
import argparse
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJ)
os.chdir(_PROJ)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJ, "ai", ".env"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import yaml

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AiDiagnosisPlatform, AgentState, DiagnosisRequest,
    _load_agent_state, _save_agent_state,
)

CASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")
DATA_DIR = "D:/Code/OpenRobotService_Data"
SUITES = ("retrieval", "answer", "ticket", "flow")
# 提单用户（项目引导闸门按它查名下项目出题）；--user 可覆盖。
# 默认测试账号 hujiannan：helpdesk_test 名下 5 项目（含摇人吧服务号），
# 配合 HELPDESK_DB=helpdesk_test + DATABASE_URL 指向测试库使用（0916）。
REG_USER = "hujiannan"

# 项目永不拦截铁律话术黑名单（flow/ticket 共用兜底检查）
_PROJECT_BLOCK_PATTERNS = ["必须选择项目", "项目是必填", "先选择项目",
                           "项目不能为空", "必须先选项目"]


def load_cases(suite: str) -> list:
    path = os.path.join(CASES_DIR, f"{suite}.yaml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


# ----------------------------------------------------------------
# 多轮驱动（answer 单轮 / ticket 与 flow 多轮共用）
# ----------------------------------------------------------------
async def drive_turns(platform: AiDiagnosisPlatform, session_id: str,
                      turns: list, created_by: str = "") -> list:
    """逐轮走真实链路：状态从 memory 恢复（仿 run() 入口逻辑），
    user turn 由 _agent_think_stream 内部追加，assistant 由 _finalize 落盘。
    created_by：提单用户（项目引导闸门按它查名下项目出题——0916 修复：
    此前不传导致候选恒空、项目题用例必挂）。
    返回每轮 {answer, stages, review}。"""
    out = []
    for text in turns:
        memory = await platform._memory_manager.get_memory(session_id)
        state = _load_agent_state(memory.metadata)
        if state is None:
            state = AgentState(session_id=session_id, phase="idle",
                               original_query=text, problem_summary=text)
            _save_agent_state(memory, state)
            await platform._memory_manager.save_memory(memory)
        request = DiagnosisRequest(session_id=session_id, query=text,
                                   created_by=created_by)
        events = []
        async for ev in platform._agent_think_stream(request, state, memory):
            events.append(ev)
        tokens = [e["data"] for e in events
                  if e.get("event") == "token" and isinstance(e.get("data"), str)]
        stages = [e["data"].get("stage") for e in events
                  if e.get("event") == "status" and isinstance(e.get("data"), dict)]
        out.append({
            "answer": "".join(tokens),
            "stages": [s for s in stages if s],
            "review": "review" in stages,
        })
    return out


def _collect_images(text: str) -> list:
    return re.findall(r'!\[[^\]]*\]\((/api/ai/media/kb/[^)\s]+?)\)', text)


# ----------------------------------------------------------------
# 各维度 runner
# ----------------------------------------------------------------
async def run_retrieval(platform, cases) -> list:
    results = []
    for i, c in enumerate(cases):
        sid = f"golden_retr_{i}_{int(time.time())}"
        state = AgentState(session_id=sid)
        docs = await platform._retrieve_with_context(sid, state, query_override=c["query"])
        detail = {"query": c["query"], "docs_head": docs[:400]}
        hit_kw, rank = "", 0
        for kw in c.get("expect_hit", []):
            if kw in docs:
                hit_kw = kw
                m = re.search(re.escape(kw), docs)
                line_start = docs.rfind("\n", 0, m.start()) + 1
                line = docs[line_start: docs.find("\n", m.start())]
                num = re.match(r"\S+(?: \S+)*? (\d+)（", line.strip())
                rank = int(num.group(1)) if num else 99
                break
        ok = bool(hit_kw)
        limit = c.get("rank_within")
        if ok and limit and rank > limit:
            ok, hit_kw = False, f"{hit_kw}（排名#{rank} 超出前{limit}）"
        detail["hit"] = hit_kw or "未命中"
        detail["rank"] = rank
        results.append({"name": c["name"], "ok": ok, "detail": detail})
    return results


async def run_answer(platform, cases, judge: bool) -> list:
    results = []
    for i, c in enumerate(cases):
        sid = f"golden_ans_{i}_{int(time.time())}"
        rounds = await drive_turns(platform, sid, [c["query"]], created_by=REG_USER)
        answer = rounds[0]["answer"] if rounds else ""
        problems, warns = [], []

        if not answer.strip():
            problems.insert(0, "LLM 响应为空（上游偶发），建议重跑该维度")

        for kw in c.get("must_reference", []):
            if kw not in answer:
                problems.append(f"缺必含要素「{kw}」")
        for kw in c.get("forbid", []):
            if kw in answer:
                problems.append(f"出现禁词「{kw}」")
        max_chars = c.get("max_chars")
        if max_chars and len(answer) > max_chars:
            problems.append(f"超长 {len(answer)}>{max_chars}")

        if c.get("min_images") is not None:
            n_imgs = len(_collect_images(answer))
            if n_imgs < c["min_images"]:
                problems.append(
                    f"图不足 {n_imgs}<{c['min_images']}"
                    "（资料带截图的操作章节，弃图=不完整回答，0904 XNA/RXX 事故形态）")

        if c.get("images_in_allowlist"):
            allow = platform._kb_image_allowlist.get(sid) or set()
            imgs = _collect_images(answer)
            bad = [u for u in imgs if u not in allow]
            if bad:
                problems.append(f"白名单外图片 {len(bad)} 个: {[u.rsplit('/', 1)[-1] for u in bad[:3]]}")

        wa = c.get("warn_analysis")
        if wa:
            head = answer[:120]
            has_analysis = bool(re.search(
                r"最可能|大概率|原因|可能|大多是|多半|从.{0,20}看|符合|意思是|这种错|属于|说明", head))
            if wa == "fault" and not has_analysis:
                warns.append("故障类回答开头未见分析段特征")
            if wa == "support" and has_analysis:
                warns.append("咨询类回答疑似出现伪分析段")

        if judge:
            j = await llm_judge(platform, c["query"], answer)
            if j:
                warns.append(f"judge: {j}")

        strict = c.get("strict", False)
        status = ("FAIL" if problems else "PASS") if strict else \
                 ("WARN" if problems else "PASS")
        if not problems and warns:
            status = "WARN" if status == "PASS" else status
        results.append({"name": c["name"], "ok": status != "FAIL",
                        "status": status,
                        "detail": {"query": c["query"], "answer": answer,
                                   "problems": problems, "warns": warns}})
    return results


async def drive_case(platform: AiDiagnosisPlatform, suite: str, sid: str,
                     c: dict) -> list:
    """固定 turns + followup_pool 接力（0916 补齐，对齐线上 dar_regress 能力）。

    turns 跑完仍未到 review 且用例带 followup_pool：按最后一条 AI 话术匹配
    when 关键词追加 say 轮（如项目题话术含「关联项目」→ 自动回序号），
    直到出现 review、pool 用尽或 8 轮保险丝。每条目只消费一次防鬼打墙。"""
    # 同一 when 可多次消费：二次提单会再次出项目题（0916 新流程），每次
    # 都要应答；死循环由 guard=8 + 「review 出现即停」双保险兜住。
    rounds = await drive_turns(platform, sid, c["turns"], created_by=REG_USER)
    pool = c.get("followup_pool") or []
    guard = 0
    while (pool and not any(r["review"] for r in rounds) and guard < 8):
        last = rounds[-1]["answer"] if rounds else ""
        hit = next((f for f in pool
                    if f.get("when") and f["when"] in last), None)
        if not hit:
            break
        rounds += await drive_turns(platform, sid, [hit["say"]],
                                    created_by=REG_USER)
        guard += 1
    return rounds


async def run_dialogue(platform, cases, suite: str) -> list:
    """ticket 行为层 / flow 共用：多轮驱动 + 弹窗/话术断言。"""
    results = []
    for i, c in enumerate(cases):
        sid = f"golden_{suite}_{i}_{int(time.time())}"
        rounds = await drive_case(platform, suite, sid, c)
        last_answer = rounds[-1]["answer"] if rounds else ""
        all_answers = [r["answer"] for r in rounds]
        problems, warns = [], []

        if not any(a.strip() for a in all_answers):
            problems.insert(0, "所有轮次 LLM 响应均为空（上游偶发），建议重跑该维度")

        if c.get("expect_review_any_round") and not any(r["review"] for r in rounds):
            problems.append("全程未出现 review 弹窗事件")
        ask_any = c.get("expect_ask_any")
        if ask_any and not any(kw in a for a in all_answers for kw in ask_any):
            problems.append(f"话术未含追问要素 {ask_any}")
        forbid = c.get("forbid_any", [])
        for kw in forbid:
            if any(kw in a for a in all_answers):
                problems.append(f"出现铁律禁词「{kw}」")
        if not forbid:
            for kw in _PROJECT_BLOCK_PATTERNS:  # 兜底铁律检查
                if any(kw in a for a in all_answers):
                    problems.append(f"兜底铁律：出现「{kw}」")

        strict = c.get("strict", False)
        status = ("FAIL" if problems else "PASS") if strict else \
                 ("WARN" if problems else "PASS")
        results.append({"name": c["name"], "ok": status != "FAIL",
                        "status": status,
                        "detail": {"turns": c["turns"], "rounds": rounds,
                                   "problems": problems}})
    return results


async def llm_judge(platform, query: str, answer: str) -> str:
    """flash 按检查单评分，返回一行评语（只 WARN 不 FAIL）。"""
    try:
        from ai.core import get_intent_client
        llm = await get_intent_client()
        prompt = (
            f"用户问题：{query}\n\n助手回答：\n{answer[:1500]}\n\n"
            "按检查单逐项打分（每项1分）：1与事实/资料一致 2直接回答了所问 "
            "3步骤或方案完整可执行 4无编造内容 5语气口语自然。"
            "输出一行：总分X/5 加一句最主要的问题（没有问题就写无明显问题）。"
        )
        out = await llm.complete(prompt=prompt, max_tokens=80, temperature=0,
                                 thinking=False)
        return (out or "").strip().splitlines()[0][:100]
    except Exception as e:
        return f"judge调用失败: {type(e).__name__}"


def run_pytest_layer() -> dict:
    """ticket 状态机层：复用既有 pytest 资产（mock 全栈，不需要外部服务）。"""
    env = dict(os.environ, HF_HUB_OFFLINE="1")
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest",
             "tests/test_ticket_submit.py", "tests/test_can_submit.py", "-q"],
            cwd=os.path.join(_PROJ, "ai"), env=env, capture_output=True,
            text=True, timeout=600, encoding="utf-8", errors="replace")
        tail = (proc.stdout or "").strip().splitlines()
        summary = tail[-1] if tail else "无输出"
        return {"ok": proc.returncode == 0, "summary": summary}
    except Exception as e:
        return {"ok": False, "summary": f"pytest 执行失败: {e}"}


# ----------------------------------------------------------------
# 报告
# ----------------------------------------------------------------
def report(all_results: dict, out_path: str):
    print("\n" + "=" * 72)
    n_fail = n_warn = n_pass = 0
    for suite, items in all_results.items():
        if isinstance(items, dict):  # pytest 层
            mark = "PASS" if items["ok"] else "FAIL"
            print(f"[{suite}] pytest 状态机层: {mark}  {items['summary']}")
            n_pass, n_fail = n_pass + (items["ok"] == 1), n_fail + (items["ok"] == 0)
            continue
        for r in items:
            st = r.get("status") or ("PASS" if r["ok"] else "FAIL")
            n_pass, n_warn, n_fail = (n_pass + (st == "PASS"),
                                      n_warn + (st == "WARN"),
                                      n_fail + (st == "FAIL"))
            extra = ""
            d = r.get("detail") or {}
            if st != "PASS":
                extra = "；".join(d.get("problems") or []) or d.get("hit") or ""
            print(f"[{suite}] {st:4} {r['name']}" + (f"  ← {extra[:70]}" if extra else ""))
            for w in (d.get("warns") or []):
                print(f"        WARN: {w[:70]}")
    print("=" * 72)
    print(f"合计: {n_pass} PASS / {n_warn} WARN / {n_fail} FAIL")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(all_results, fh, ensure_ascii=False, indent=1)
    print(f"明细已存 {out_path}")


# ----------------------------------------------------------------
# --add 顺手沉淀
# ----------------------------------------------------------------
def add_case(suite: str, query: str, expect: str = "", extra: dict = None):
    if suite not in SUITES:
        sys.exit(f"维度必须是 {SUITES} 之一")
    if suite == "retrieval":
        block = f'\n- name: 临时-{int(time.time()) % 10000}\n  query: "{query}"\n'
        if expect:
            block += f'  expect_hit: ["{expect}"]\n'
    else:
        block = (f'\n- name: 临时-{int(time.time()) % 10000}\n'
                 f'  query: "{query}"\n')
        if expect:
            block += f'  must_reference: ["{expect}"]\n'
    path = os.path.join(CASES_DIR, f"{suite}.yaml")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(block)
    print(f"[OK] 已追加到 {path}（记得改成正式 name 与断言字段）")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all",
                    help=f"逗号分隔：{SUITES} 或 all（默认）")
    ap.add_argument("--judge", action="store_true", help="answer 维度加 LLM judge（只 WARN）")
    ap.add_argument("--add", metavar="SUITE", help="追加临时用例到维度（配合 --query/--expect）")
    ap.add_argument("--query", default="", help="--add 的问句")
    ap.add_argument("--expect", default="", help="--add 的期望关键词")
    ap.add_argument("--user", default="", help="提单用户（项目闸门查名下项目用，默认 hujiannan）")
    args = ap.parse_args()
    if args.user:
        global REG_USER
        REG_USER = args.user

    if args.add:
        add_case(args.add, args.query, args.expect)
        return

    suites = list(SUITES) if args.suite == "all" else \
        [s.strip() for s in args.suite.split(",") if s.strip()]

    platform = AiDiagnosisPlatform()
    await platform._ensure_clients()
    # 预热精排模型（首次加载数秒，避免第一条用例计时失真）
    await platform._retriever._ensure_clients()

    all_results = {}
    for suite in suites:
        cases = load_cases(suite)
        if not cases:
            continue
        print(f"\n>>> [{suite}] {len(cases)} 条用例")
        if suite == "retrieval":
            all_results[suite] = await run_retrieval(platform, cases)
        elif suite == "answer":
            all_results[suite] = await run_answer(platform, cases, args.judge)
        else:
            all_results[suite] = await run_dialogue(platform, cases, suite)
        # 每个维度跑完即小结
        rs = all_results[suite]
        if isinstance(rs, list):
            n_bad = sum(1 for r in rs if not r["ok"])
            n_warn = sum(1 for r in rs if r.get("status") == "WARN")
            print(f"    {len(rs)} 条: {len(rs)-n_bad-n_warn} PASS / {n_warn} WARN / {n_bad} FAIL")

    if "ticket" in suites:
        print("\n>>> [ticket] pytest 状态机层（mock 全栈）")
        all_results["ticket_pytest"] = run_pytest_layer()
        print(f"    {all_results['ticket_pytest']['summary']}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    report(all_results, os.path.join(DATA_DIR, f"regression_{stamp}.json"))


if __name__ == "__main__":
    asyncio.run(main())
