# -*- coding: utf-8 -*-
"""一键回归执行器：golden 用例集 → 测试环境 API + 本地检索重放。

被 dar_studio.py 以子进程调用，也可独立 CLI 使用。stdout 逐行 JSON：
    {event:"case_begin", suite, name, index, total}
    {event:"case", suite, name, ok, warn, verdict, detail:{...}}
    {event:"summary", pass, warn, fail, ms}
断言语义与 ai/tests/golden/run_regression.py 对齐；answer 的图片白名单类
断言依赖服务端内存，线上不可判，自动跳过并标注。

用法：
    python ai/scripts/dar_regress.py --suites retrieval --qdrant test
    python ai/scripts/dar_regress.py --suites answer --base http://125.122.97.107:9401
        （token 走环境变量 REGRESS_TOKEN，不进命令行）
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
CASES_DIR = os.path.join(PROJ, "ai", "tests", "golden", "cases")

API_SUITES = ("answer", "ticket", "flow")  # 打测试环境真实服务


def emit(obj: dict):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_cases(suite: str) -> list:
    import yaml
    p = os.path.join(CASES_DIR, f"{suite}.yaml")
    if not os.path.exists(p):
        return []
    return yaml.safe_load(open(p, encoding="utf-8")) or []


# ── retrieval：本地重放（连 --qdrant 指定源，test=与测试服务同指针） ──
async def run_retrieval(cases: list, qdrant: str, counted):
    from dar_qdrant import remote_qdrant

    if qdrant in ("test", "prod"):
        with remote_qdrant(qdrant):
            await _retrieval_body(cases, counted)
    else:
        await _retrieval_body(cases, counted)


async def _retrieval_body(cases: list, counted):
    os.chdir(PROJ)
    if PROJ not in sys.path:
        sys.path.insert(0, PROJ)
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState, get_diagnosis_platform
    platform = await get_diagnosis_platform()
    await platform._ensure_clients()
    for i, c in enumerate(cases):
        counted({"event": "case_begin", "suite": "retrieval", "name": c["name"],
              "index": i, "total": len(cases)})
        t0 = time.time()
        try:
            sid = f"reg_retr_{os.getpid()}_{i}"
            state = AgentState(session_id=sid)
            docs = await platform._retrieve_with_context(sid, state, query_override=c["query"])
            hit_kw, rank = "", 0
            for kw in c.get("expect_hit", []):
                if kw in (docs or ""):
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
            counted({"event": "case", "suite": "retrieval", "name": c["name"], "ok": ok,
                  "warn": False, "verdict": "PASS" if ok else "FAIL",
                  "detail": {"query": c["query"], "hit": hit_kw or "未命中",
                             "rank": rank, "limit": limit,
                             "docs_head": (docs or "")[:300],
                             "ms": round((time.time() - t0) * 1000)}})
        except Exception as e:
            counted({"event": "case", "suite": "retrieval", "name": c["name"], "ok": False,
                  "warn": False, "verdict": "ERROR",
                  "detail": {"query": c.get("query", ""), "error": f"{type(e).__name__}: {e}"[:200]}})


# ── answer/ticket/flow：打测试环境 ask/stream ──
TEST_ENV_FILE = "/data/apps/TestOpenRobotService/ai/.env"
TEST_PY = "~/miniconda3/envs/test-ai/bin/python"
SSH_CMD = ["ssh", "-p", "8802", "usp-a@125.122.97.107"]


def remote_plan_tools(sid: str, since_kw: str = "") -> list:
    """ssh 到测试机读该会话实际调用的工具（从 [plan] 规划行解析，多轮合并去重）。
    服务端 [plan] 行已补 session= 字段（0909，待部署）——有则精确匹配；
    旧版无 session 用会话首末行号区间近似（回归串行跑、测试环境低并发，
    区间内 [plan] 行即该会话各轮规划）。
    since_kw：只取日志中含该关键词的行**之后**的 plan 行——post_ask 轮级断言用
    （ask 文本会出现在 [sse] q= 行里，锚定后即该轮规划，不含前几轮）。"""
    import subprocess
    script = (
        "import json\n"
        f"log=open('/data/apps/TestOpenRobotService/ai/logs/ai.log',encoding='utf-8',errors='replace').read().splitlines()\n"
        f"sid={sid!r}\n"
        "hits=[(i,l) for i,l in enumerate(log) if '[plan]' in l and sid in l]\n"
        "if not hits:\n"
        "    idx=[i for i,l in enumerate(log) if sid in l]\n"
        "    lo,hi=(idx[0],idx[-1]) if idx else (-1,-2)\n"
        "    hits=[(i,l) for i,l in enumerate(log) if '[plan]' in l and lo<=i<=hi]\n"
        f"kw={since_kw!r}\n"
        "if kw:\n"
        "    a=[i for i,l in enumerate(log) if kw in l]\n"
        "    if a: hits=[(i,l) for i,l in hits if i>a[0]]\n"
        "print('PLAN:'+json.dumps([l for _,l in hits][-30:],ensure_ascii=False))\n"
    )
    r = subprocess.run(SSH_CMD + [f"{TEST_PY} -"], input=script,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90)
    names: list = []
    for line in (r.stdout or "").splitlines():
        if not line.startswith("PLAN:"):
            continue
        import json as _j
        for plan_line in _j.loads(line[5:]):
            for m in re.finditer(r"tools=\[([^\]]*)\]", plan_line):
                for nm in re.findall(r"\('([a-z_]+)'", m.group(1)):
                    if nm not in names:
                        names.append(nm)
    return names


def _fill_refs(ask: str, db_id, db_row) -> str:
    """追问占位符：{db_id}=本用例落库的单号；{other_id}=动态查一张别人的单
    （权限反例——测试库静态号会被清/换人，别人的单只能现查现用；
    查不到时返回空串，调用方跳过该步）。
    remote_query_one 返回值是 list（tuple 经 json 序列化），按位取列。"""
    ask = ask.replace("{db_id}", str(db_id or ""))
    if "{other_id}" in ask:
        # db_row = [id, title, created_by, source]（见 expect_db 的 SELECT）
        owner = ""
        if isinstance(db_row, list) and len(db_row) > 2:
            owner = str(db_row[2] or "").replace("'", "")
        cond = f"created_by <> '{owner}' AND created_by IS NOT NULL" if owner \
            else "created_by IS NOT NULL"
        row = remote_query_one(
            f"SELECT id FROM tasks WHERE {cond} ORDER BY id DESC LIMIT 1")
        oid = row[0] if isinstance(row, list) and row else None
        ask = ask.replace("{other_id}", str(oid)) if oid else ""
    return ask


def remote_query_one(sql: str):
    """ssh 到测试机查 helpdesk_test 单行（工单落库验证；凭据只在服务器端解析）。"""
    import subprocess
    script = (
        "import re,json,pymysql\n"
        f"env=open({TEST_ENV_FILE!r},encoding='utf-8').read()\n"
        "url=next(l for l in env.splitlines() if l.startswith('DATABASE_URL='))\n"
        "m=re.search(r'//([^:]+):([^@]+)@([^/:]+)(?::(\\d+))?/(\\w+)',url)\n"
        "conn=pymysql.connect(host=m.group(3),port=int(m.group(4) or 3306),"
        "user=m.group(1),password=m.group(2),database=m.group(5),charset='utf8mb4')\n"
        "cur=conn.cursor()\n"
        f"cur.execute({sql!r})\n"
        "print('ROW:'+json.dumps(cur.fetchone(),ensure_ascii=False,default=str))\n"
    )
    r = subprocess.run(SSH_CMD + [f"{TEST_PY} -"], input=script,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90)
    for line in (r.stdout or "").splitlines():
        if line.startswith("ROW:"):
            return json.loads(line[4:])
    return None


async def _post_ticket(base: str, token: str, path: str, body: dict) -> dict:
    """打测试环境提单接口（prepare/confirm）。返回 {status, json}。"""
    import httpx
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{base}{path}", json=body,
                         headers={"Authorization": f"Bearer {token}"})
        try:
            j = r.json()
        except Exception:
            j = {"raw": r.text[:200]}
        return {"status": r.status_code, "json": j}


async def _ask_turn(base: str, token: str, sid: str, query: str) -> dict:
    """单轮打测试环境，返回 {answer, stages, result, first_ms, total_ms}。"""
    import httpx
    out = {"answer": "", "stages": [], "result": None, "first_ms": None, "total_ms": None}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, read=180)) as c:
        async with c.stream("POST", f"{base}/api/ai/qa/ask/stream",
                            json={"session_id": sid, "query": query},
                            headers={"Authorization": f"Bearer {token}"}) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode("utf-8", "replace")[:200]
                raise RuntimeError(f"HTTP {r.status_code}: {body}")
            ev, buf = None, ""
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    buf += line[5:].strip()
                elif not line.strip() and buf:
                    try:
                        data = json.loads(buf)
                    except Exception:
                        data = {}
                    if ev == "token" and isinstance(data.get("token"), str):
                        if out["first_ms"] is None:
                            out["first_ms"] = data.get("ms")
                        out["answer"] += data["token"]
                    elif ev == "status" and isinstance(data, dict) and data.get("stage"):
                        out["stages"].append(data["stage"])
                    elif ev == "result":
                        out["result"] = data
                    elif ev == "error":
                        raise RuntimeError(str(data.get("error", ""))[:200])
                    elif ev == "done":
                        out["total_ms"] = data.get("total_ms")
                    ev, buf = None, ""
            # result.message 是出口清洗后的全文，比 token 拼接更权威
            if out.get("result") and out["result"].get("message"):
                out["answer"] = out["result"]["message"]
    return out


def _check_text(c: dict, answer: str) -> tuple[bool, list, list]:
    """answer 断言子集（线上可判的）。返回 (ok, fails, warns)。"""
    fails, warns = [], []
    for kw in c.get("must_reference", []):
        if kw not in answer:
            fails.append(f"缺关键词「{kw}」")
    for kw in c.get("forbid", []):
        if kw in answer:
            fails.append(f"含禁词「{kw}」")
    mc = c.get("max_chars")
    if mc and len(answer) > mc:
        warns.append(f"超长 {len(answer)}>{mc}")
    n_img = c.get("min_images")
    if n_img:
        imgs = re.findall(r"/api/ai/media/kb/", answer)
        if len(imgs) < n_img:
            fails.append(f"回答缺图（命中 {len(imgs)}<{n_img}，拦弃图）")
    return (not fails and not warns), fails, warns


def _check_turns(c: dict, turns_out: list, extra_fails: list = None) -> tuple[bool, list]:
    """ticket/flow 多轮断言（含 extra_fails：落库/草稿等链路断言）。"""
    fails = list(extra_fails or [])
    all_text = "\n".join(t["answer"] for t in turns_out)
    all_stages = [s for t in turns_out for s in t["stages"]]
    review = "review" in all_stages
    if c.get("expect_review_any_round") and not review:
        fails.append("任一轮都没出现 review 弹窗")
    if c.get("forbid_review") and review:
        fails.append("不应出现 review 弹窗但出现了")
    ask = c.get("expect_ask_any")
    if ask and not any(kw in all_text for kw in ask):
        fails.append(f"话术缺追问要素（任一即可：{'、'.join(ask[:4])}…）")
    for kw in c.get("forbid_any", []):
        if kw in all_text:
            fails.append(f"话术含禁词「{kw}」")
    stages_any = c.get("expect_stages_any")
    if stages_any and not any(s in all_stages for s in stages_any):
        fails.append(f"阶段未出现（任一：{'、'.join(stages_any)}）")
    n_img = c.get("min_images")
    if n_img:
        last = turns_out[-1]["answer"] if turns_out else ""
        imgs = re.findall(r"/api/ai/media/kb/", last)
        if len(imgs) < n_img:
            fails.append(f"回答缺图（{len(imgs)}<{n_img}）")
    return (not fails), fails


async def run_api_suite(suite: str, cases: list, base: str, token: str, counted):
    for i, c in enumerate(cases):
        counted({"event": "case_begin", "suite": suite, "name": c["name"],
              "index": i, "total": len(cases)})
        t0 = time.time()
        sid = f"reg_{suite}_{os.getpid()}_{i}_{int(time.time())}"
        turns = c.get("turns") or [c.get("query", "")]
        try:
            turns_out = []
            review_hit = False
            # 模拟全流程（响应式多轮，0909 实锤教训：一轮预设台词测不完提单链路）：
            # ①服务端出项目选择题（模板直出「出单前确认一下关联项目」，特征绝对
            #   稳定）自动回「1」选第 1 个候选——真实用户点按钮/回序号，无头回归
            #   必须替用户答，否则草稿无项目、confirm 被弹窗闸门拦（票史候选
            #   1 个时 LLM 照抄预填碰巧能过，≥2 个摇摆即挂）；
            # ②预设 turns 发完仍未到弹窗 → 按 followup_pool 关键词接力应答信息
            #   追问（追问顺序/轮数由 LLM 决定，固定轮次测不稳）。
            pool = c.get("followup_pool") or []
            pool_used = set()
            queue = list(turns)
            auto_ans = 0
            while queue and len(turns_out) < len(turns) + 6:
                text = queue.pop(0)
                r = await _ask_turn(base, token, sid, text)
                r["q"] = text
                turns_out.append(r)
                if "review" in r["stages"]:
                    review_hit = True
                    break  # 到弹窗即达成本轮目标，省 API 轮次
                ans = r["answer"]
                if auto_ans < 2 and "出单前确认一下关联项目" in ans:
                    auto_ans += 1
                    queue.insert(0, "1")
                    continue
                if not queue:
                    for k, item in enumerate(pool):
                        if k not in pool_used and item["when"] in ans:
                            pool_used.add(k)
                            queue.append(item["say"])
                            break
            # 提单链路纵深：到弹窗后验草稿 → confirm 落库 → ssh 查测试库
            extra_fails, db_row, db_id = [], None, None
            if review_hit and (c.get("expect_draft_any") or c.get("expect_confirm")):
                prep = await _post_ticket(base, token, "/api/ai/qa/ticket/prepare",
                                          {"session_id": sid})
                draft_text = json.dumps(prep.get("json", {}), ensure_ascii=False)
                if c.get("expect_draft_any") and \
                        not any(kw in draft_text for kw in c["expect_draft_any"]):
                    extra_fails.append(
                        f"草稿缺关键词（任一：{'、'.join(c['expect_draft_any'][:3])}）")
                if c.get("expect_confirm"):
                    conf = await _post_ticket(base, token, "/api/ai/qa/ticket/confirm",
                                              {"session_id": sid, "overrides": {}})
                    cj = conf.get("json", {}) or {}
                    db_id = (cj.get("data") or {}).get("db_id")
                    if conf.get("status") != 200 or cj.get("code") != 0 or not db_id:
                        extra_fails.append(
                            "confirm 未落库: "
                            + f"HTTP{conf.get('status')} "
                            + json.dumps(cj, ensure_ascii=False)[:120])
                    elif c.get("expect_db"):
                        db_row = remote_query_one(
                            f"SELECT id,title,created_by,source "
                            f"FROM tasks WHERE id={int(db_id)}")
                        if not db_row:
                            extra_fails.append(f"测试库 tasks 未查到 db_id={db_id}")
                        else:
                            row_text = json.dumps(db_row, ensure_ascii=False)
                            if c.get("expect_draft_any") and \
                                    not any(kw in row_text for kw in c["expect_draft_any"]):
                                extra_fails.append(
                                    f"落库记录缺关键词: {row_text[:120]}")
                    if c.get("after_confirm_ask") and not extra_fails:
                        q2 = _fill_refs(c["after_confirm_ask"], db_id, db_row)
                        r2 = await _ask_turn(base, token, sid, q2)
                        r2["q"] = q2
                        turns_out.append(r2)
            # post_asks：confirm 后多步追问（查单状态/权限对比/再提单等），
            # 支持 {db_id}=本用例落的单、{other_id}=动态查一张别人的单（权限反例）
            if c.get("post_asks") and not extra_fails:
                for pa in c["post_asks"]:
                    ask = _fill_refs(pa.get("ask", ""), db_id, db_row)
                    if not ask:
                        continue
                    r2 = await _ask_turn(base, token, sid, ask)
                    r2["q"] = ask
                    turns_out.append(r2)
                    txt = r2["answer"]
                    if pa.get("expect_any") and not any(k in txt for k in pa["expect_any"]):
                        extra_fails.append(
                            f"追问「{ask[:24]}」回答缺关键词"
                            f"（任一：{'、'.join(pa['expect_any'][:4])}）")
                    for kw in pa.get("forbid_any") or []:
                        if kw in txt:
                            extra_fails.append(f"追问「{ask[:24]}」回答含禁词「{kw}」")
                    # 轮级工具断言：锚=ask 文本（[sse] q= 行），只看该轮之后的规划
                    if pa.get("expect_tools_all") or pa.get("forbid_tools"):
                        called2 = remote_plan_tools(sid, since_kw=ask)
                        miss2 = [t for t in (pa.get("expect_tools_all") or [])
                                 if t not in called2]
                        if miss2:
                            extra_fails.append(
                                f"追问「{ask[:24]}」轮工具未调"
                                f"（实际 {'、'.join(called2) or '无'}）：{'、'.join(miss2)}")
                        hit2 = [t for t in (pa.get("forbid_tools") or []) if t in called2]
                        if hit2:
                            extra_fails.append(
                                f"追问「{ask[:24]}」轮禁调工具被调：{'、'.join(hit2)}")
            # 工具路由断言（全轮跑完后拉服务端 [plan] 日志；合并集合语义）
            _tkeys = ("expect_tools_any", "expect_tools_all", "forbid_tools")
            if any(c.get(k) for k in _tkeys):
                called = remote_plan_tools(sid)
                miss = [t for t in (c.get("expect_tools_all") or []) if t not in called]
                if miss:
                    extra_fails.append(
                        f"工具未调用（必须全调，实际调了 {'、'.join(called) or '无'}）："
                        f"{'、'.join(miss)}")
                if c.get("expect_tools_any") and \
                        not any(t in called for t in c["expect_tools_any"]):
                    extra_fails.append(
                        f"工具全未调用（任一即可，实际调了 {'、'.join(called) or '无'}）："
                        f"{'、'.join(c['expect_tools_any'])}")
                hit = [t for t in (c.get("forbid_tools") or []) if t in called]
                if hit:
                    extra_fails.append(f"禁调工具被调用：{'、'.join(hit)}")
            detail = {
                "query": c.get("query") or turns[0],
                "turns_run": len(turns_out),
                "stages": [t["stages"] for t in turns_out],
                "turns_qa": [{"q": t.get("q", ""),
                              "a": (t["answer"] or "")[:400]} for t in turns_out],
                "answer_head": (turns_out[-1]["answer"] if turns_out else "")[:400],
                "first_ms": turns_out[0].get("first_ms") if turns_out else None,
                "ms": round((time.time() - t0) * 1000),
            }
            if db_row:
                detail["db_row"] = db_row
            if suite == "answer":
                ok, fails, warns = _check_text(c, turns_out[0]["answer"] if turns_out else "")
            else:
                ok, fails = _check_turns(c, turns_out, extra_fails)
                warns = []
            strict = c.get("strict", False)
            if not ok:
                verdict = "FAIL" if strict else "WARN"
                counted({"event": "case", "suite": suite, "name": c["name"],
                      "ok": False, "warn": not strict, "verdict": verdict,
                      "detail": {**detail, "fails": fails}})
            else:
                counted({"event": "case", "suite": suite, "name": c["name"],
                      "ok": True, "warn": bool(warns), "verdict": "PASS",
                      "detail": {**detail, "fails": warns}})
        except Exception as e:
            counted({"event": "case", "suite": suite, "name": c["name"], "ok": False,
                  "warn": False, "verdict": "ERROR",
                  "detail": {"query": (turns or [""])[0],
                             "error": f"{type(e).__name__}: {e}"[:200]}})
        await asyncio.sleep(1)  # 测试环境限压


async def main_async(a):
    t0 = time.time()
    counts = {"pass": 0, "warn": 0, "fail": 0}

    def counted(ev: dict):
        if ev.get("event") == "case":
            if ev.get("verdict") == "PASS" and not ev.get("warn"):
                counts["pass"] += 1
            elif ev.get("verdict") in ("FAIL", "ERROR"):
                counts["fail"] += 1
            else:
                counts["warn"] += 1
        emit(ev)

    token = os.environ.get("REGRESS_TOKEN", "")
    for suite in a.suites.split(","):
        suite = suite.strip()
        cases = load_cases(suite)
        if a.only:
            names = set(a.only.split(","))
            cases = [c for c in cases if c.get("name") in names]
        if not cases:
            counted({"event": "case", "suite": suite, "name": "(无匹配用例)",
                     "ok": False, "warn": False, "verdict": "ERROR",
                     "detail": {"error": "用例集为空或过滤后无匹配"}})
            continue
        if suite in API_SUITES:
            if not token:
                counted({"event": "case", "suite": suite, "name": "(需登录)",
                         "ok": False, "warn": False, "verdict": "ERROR",
                         "detail": {"error": "answer/ticket/flow 需先登录测试环境拿 token"}})
                continue
            await run_api_suite(suite, cases, a.base, token, counted)
        elif suite == "retrieval":
            await run_retrieval(cases, a.qdrant, counted)
        else:
            counted({"event": "case", "suite": suite, "name": suite,
                     "ok": False, "warn": False, "verdict": "ERROR",
                     "detail": {"error": f"未知 suite：{suite}"}})
    emit({"event": "summary", **counts, "ms": round((time.time() - t0) * 1000)})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", required=True, help="逗号分隔：retrieval,answer,ticket,flow")
    ap.add_argument("--qdrant", default="test", choices=["test", "prod", "local"])
    ap.add_argument("--base", default="http://125.122.97.107:9401")
    ap.add_argument("--only", default="", help="逗号分隔的用例名过滤")
    a = ap.parse_args()
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(main_async(a))
    except Exception as e:
        emit({"event": "fatal", "error": f"{type(e).__name__}: {e}"[:300]})
        emit({"event": "summary", "pass": 0, "warn": 0, "fail": 1, "ms": 0})
