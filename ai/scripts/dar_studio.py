# -*- coding: utf-8 -*-
"""AI 质量工作台：本地 Web UI，双页签（周流程指标生成 / 在线测试）。

    python ai/scripts/dar_studio.py          # → http://127.0.0.1:9527

- 周流程：代理 dar_weekly.py 各步骤，stdout 实时 SSE 推前端；产物浏览（周报/明细/标注工具）
- 在线测试：
  - 全链路：登录测试后端拿 token → 代理 /api/ai/qa/ask/stream（线上真实链路，
    提单确认走 /ticket/confirm 落测试库）
  - 检索探针：代理 dar_probe.py（默认连生产 qdrant 只读）
- token 只存本进程内存，不落盘；页面仅拿 username。
"""
import asyncio
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(PROJ, "backend")  # 让 from app.* 可解析
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
DATA_ROOT = r"C:/Users/PAJ26020/Desktop/export_dar"
SINK_ROOT = r"D:/Code/OpenRobotService_Data/review/ticket_resolutions"
PORT = int(os.environ.get("DAR_STUDIO_PORT", "9527"))

# 排除用户：这些用户的所有对话/工单从工作台过滤掉（0915 用户要求移除叮叮叮叮93）
# 改这里即可生效；UI 顶部会显示「已排除 N 用户」徽章
SKIP_USER_IDS = {
    "oD5oY3WsvNAFLWSsJ92NX0in3fO8",  # 叮叮叮叮93
}


def _skip_user_names() -> list[str]:
    """从 users.csv.gz 解析出 user_id → 显示名（前端徽章用）。"""
    out: list[str] = []
    try:
        import gzip as _gz
        import csv as _csv
        for env in ("prod", "test"):
            p = os.path.join(DATA_ROOT, env, "users.csv.gz")
            if not os.path.exists(p):
                continue
            with _gz.open(p, "rt", encoding="utf-8") as fh:
                r = _csv.DictReader(fh)
                for row in r:
                    if str(row.get("id") or "") in SKIP_USER_IDS:
                        out.append(str(row.get("name") or row.get("username") or row.get("id")))
    except Exception:
        pass
    return out


DEFAULT_BACKEND = "http://127.0.0.1:19640"      # 经 ssh 隧道 → 测试环境后端 9400（login）
DEFAULT_AI = "http://127.0.0.1:19641"            # 经 ssh 隧道 → 测试环境 AI 服务 9401（ask/stream）
SSH_HOST = "usp-a@125.122.97.107"
SSH_PORT = "8802"

app = FastAPI(title="AI 质量工作台")


@app.get("/api/skip_users")
def skip_users():
    """返回当前 SKIP_USER_IDS 列表 + 显示名（前端徽章）。"""
    return {"ids": sorted(SKIP_USER_IDS), "names": _skip_user_names()}

# ── ssh 隧道（测试环境 9400/9401 不对公网开放，只能经服务器转发）────
_tunnel = {"proc": None, "ready": False}


def _port_open(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_tunnel() -> bool:
    """本地 19640/19641/3306 → 服务器 9400/9401/3306。已有隧道（含上次实例残留）直接复用。"""
    if _tunnel["ready"] or _port_open(19640):
        _tunnel["ready"] = True
        return True
    if not _tunnel["proc"] or _tunnel["proc"].poll() is not None:
        _tunnel["proc"] = subprocess.Popen(
            ["ssh", "-p", SSH_PORT, "-N",
             "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
             "-o", "ServerAliveInterval=30",
             "-L", "19640:127.0.0.1:9400", "-L", "19641:127.0.0.1:9401",
             "-L", "3306:127.0.0.1:3306",   # 生产 DB 走 127.0.0.1 → 同服务器 MySQL
             SSH_HOST],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):  # 最多等 10s
        if _port_open(19640):
            _tunnel["ready"] = True
            print(f"ssh 隧道就绪：19640→9400 / 19641→9401 / 3306→3306（{SSH_HOST}:{SSH_PORT}）")
            return True
        time.sleep(0.25)
    return False


# ── 登录态（内存）──────────────────────────────────────────────
_tokens: dict[str, dict] = {}  # DEFAULT_AI -> {token, username, at}


class LoginReq(BaseModel):
    username: str
    password: str
    base: str = ""  # 可选自定义后端地址，缺省测试环境


@app.post("/api/login")
async def login(req: LoginReq):
    base = (req.base.rstrip("/") or DEFAULT_BACKEND) if req.base else DEFAULT_BACKEND
    if not req.base and not ensure_tunnel():
        raise HTTPException(502, f"ssh 隧道建不上（测试环境不对公网开放，需免密 ssh {SSH_HOST}:{SSH_PORT}）")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{base}/api/auth/login",
                             json={"username": req.username, "password": req.password})
    except Exception as e:
        raise HTTPException(502, f"连不上后端 {base}: {type(e).__name__}")
    if r.status_code != 200:
        raise HTTPException(401, f"登录失败（{r.status_code}）: {r.text[:120]}")
    data = r.json()
    tok = data.get("access_token") or ""
    if not tok:
        raise HTTPException(502, "登录响应缺 access_token")
    _tokens[DEFAULT_AI] = {"token": tok, "username": req.username, "at": time.time()}
    return {"ok": True, "username": req.username,
            "expires_in": data.get("expires_in")}


@app.post("/api/logout")
async def logout():
    _tokens.pop(DEFAULT_AI, None)
    return {"ok": True}


@app.get("/api/status")
async def status():
    t = _tokens.get(DEFAULT_AI)
    prod_at = ""
    mp = os.path.join(DATA_ROOT, "prod", "meta.json")
    try:
        metas = json.load(open(mp, encoding="utf-8"))
        prod_at = (metas[-1].get("at") or "").replace("T", " ")[:16]  # 最近一次生产导数时间
    except Exception:
        pass
    return {"backend": DEFAULT_BACKEND, "ai": DEFAULT_AI,
            "logged_in": t["username"] if t else "",
            "prod_export": prod_at}


# ── 全链路对话代理（SSE）────────────────────────────────────────
class AskReq(BaseModel):
    session_id: str
    query: str
    skip_retrieval: bool = False


def _child_env():
    # PYTHONUNBUFFERED：子进程 stdout 走 PIPE 非行缓冲，进度行会攒 8KB 不吐
    # （0909 实锤：l3 判了 180 段日志零进度行，只有大块 traceback 挤出去）
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


@app.post("/api/ask")
async def ask(req: AskReq):
    t = _tokens.get(DEFAULT_AI)
    if not t:
        raise HTTPException(401, "未登录（先在连接条登录）")
    if not ensure_tunnel():
        raise HTTPException(502, f"ssh 隧道断开（重试或检查免密 ssh {SSH_HOST}:{SSH_PORT}）")
    headers = {"Authorization": f"Bearer {t['token']}"}
    payload = {"session_id": req.session_id, "query": req.query,
               "skip_retrieval": req.skip_retrieval}

    async def gen():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, read=180)) as c:
                async with c.stream("POST", f"{DEFAULT_AI}/api/ai/qa/ask/stream",
                                    json=payload, headers=headers) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode("utf-8", "replace")[:200]
                        yield f"data: {json.dumps({'event': 'error', 'data': {'error': f'HTTP {r.status_code}: {body}', 'fatal': True}}, ensure_ascii=False)}\n\n"
                        return
                    ev = None
                    buf = ""
                    async for line in r.aiter_lines():
                        if line.startswith("event:"):
                            ev = line[6:].strip()
                        elif line.startswith("data:"):
                            buf += line[5:].strip()
                        elif not line.strip():
                            if buf:
                                try:
                                    data = json.loads(buf)
                                except Exception:
                                    data = {"raw": buf[:2000]}
                                yield f"data: {json.dumps({'event': ev or 'message', 'data': data}, ensure_ascii=False)}\n\n"
                            elif ev:  # 无 data 的事件（罕见）
                                yield f"data: {json.dumps({'event': ev, 'data': {}}, ensure_ascii=False)}\n\n"
                            ev, buf = None, ""
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'data': {'error': f'{type(e).__name__}: {e}', 'fatal': True}}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ConfirmReq(BaseModel):
    session_id: str
    overrides: dict = {}


@app.post("/api/ticket_confirm")
async def ticket_confirm(req: ConfirmReq):
    return await _ticket_post("/api/ai/qa/ticket/confirm",
                              {"session_id": req.session_id,
                               "overrides": req.overrides})


@app.post("/api/ticket_prepare")
async def ticket_prepare(req: ConfirmReq):
    return await _ticket_post("/api/ai/qa/ticket/prepare",
                              {"session_id": req.session_id})


async def _ticket_post(path: str, body: dict):
    t = _tokens.get(DEFAULT_AI)
    if not t:
        raise HTTPException(401, "未登录")
    if not ensure_tunnel():
        raise HTTPException(502, f"ssh 隧道断开（重试或检查免密 ssh {SSH_HOST}:{SSH_PORT}）")
    payload = {**body, "username": t["username"]}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{DEFAULT_AI}{path}", json=payload,
                             headers={"Authorization": f"Bearer {t['token']}"})
        try:
            return {"status": r.status_code, "body": r.json()}
        except Exception:
            return {"status": r.status_code, "body": r.text[:500]}
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")


# ── 检索探针（subprocess dar_probe）─────────────────────────────
class ProbeReq(BaseModel):
    q: str
    qdrant: str = "prod"


@app.post("/api/probe")
async def probe(req: ProbeReq):
    if req.qdrant not in ("test", "prod", "local"):
        raise HTTPException(400, "qdrant 取值 test|prod|local")

    def _run():
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "dar_probe.py"),
             "--q", req.q, "--qdrant", req.qdrant],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, cwd=PROJ, env=env)
        out = (r.stdout or "").strip()
        try:  # 输出尾段是 JSON（前面可能混 pipeline 日志）
            return json.loads(out[out.index("{"):]), (r.stderr or "")[:400]
        except Exception:
            return None, out[-600:] or (r.stderr or "")[-600:]

    result, diag = await asyncio.to_thread(_run)
    if not result:
        raise HTTPException(502, f"探针失败: {diag}")
    result["_diag"] = diag
    return result


# ── 一键回归（golden 用例集 → 测试 API / 本地检索重放）──────────
_reg_state: dict = {"proc": None}


@app.get("/api/cases")
def cases():
    """golden 四维度用例清单（同一份真源 ai/tests/golden/cases/*.yaml）。"""
    out = {}
    desc = {"retrieval": "检索命中（本地重放，连测试知识库）",
            "answer": "回答质量（打测试环境真实链路）",
            "ticket": "提单流程（多轮打测试环境）",
            "flow": "追问/铁律（多轮打测试环境）"}
    try:
        import yaml
        for s in ("retrieval", "answer", "ticket", "flow"):
            p = os.path.join(PROJ, "ai", "tests", "golden", "cases", f"{s}.yaml")
            if os.path.exists(p):
                out[s] = {"desc": desc[s],
                          "cases": yaml.safe_load(open(p, encoding="utf-8")) or []}
    except Exception as e:
        raise HTTPException(500, f"用例集解析失败: {e}")
    return out


class RegReq(BaseModel):
    suites: list[str]
    only: list[str] = []
    qdrant: str = "test"


@app.post("/api/regression")
def regression(req: RegReq):
    bad = [s for s in req.suites if s not in ("retrieval", "answer", "ticket", "flow")]
    if bad:
        raise HTTPException(400, f"未知 suite：{bad}")
    if _reg_state["proc"] and _reg_state["proc"].poll() is None:
        raise HTTPException(409, "已有回归在跑（先等完或停掉）")
    if not req.suites:
        raise HTTPException(400, "至少选一个维度")
    token = _tokens.get(DEFAULT_AI, {}).get("token", "")
    needs_test = any(s in req.suites for s in ("answer", "ticket", "flow"))
    if not token and needs_test:
        raise HTTPException(401, "answer/ticket/flow 需先登录测试环境")
    if needs_test and not ensure_tunnel():
        raise HTTPException(502, f"ssh 隧道断开（重试或检查免密 ssh {SSH_HOST}:{SSH_PORT}）")
    env = {**_child_env(), "REGRESS_TOKEN": token}
    _reg_state["proc"] = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "dar_regress.py"),
         "--suites", ",".join(req.suites), "--qdrant", req.qdrant,
         "--base", DEFAULT_AI]
        + (["--only", ",".join(req.only)] if req.only else []),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        cwd=PROJ, env=env)

    def gen():
        p = _reg_state["proc"]
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ev = obj.pop("event", "log")
            yield f"data: {json.dumps({'event': ev, 'data': obj}, ensure_ascii=False)}\n\n"
        rc = p.wait()
        _reg_state["proc"] = None
        yield f"data: {json.dumps({'event': 'exit', 'data': {'rc': rc}}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _kill_tree(p):
    """杀整棵进程树：p.kill() 只杀直接子进程，dar_weekly 的孙脚本
    （dar_retrieval_check 等 subprocess.run 子进程）会变孤儿继续跑——
    0909 实锤「点停止不管用」。Windows 用 taskkill /T。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True)
    else:
        p.kill()


@app.post("/api/stop_regression")
def stop_regression():
    p = _reg_state["proc"]
    if p and p.poll() is None:
        _kill_tree(p)
        return {"ok": True, "killed": True}
    return {"ok": True, "killed": False}


# ── 周流程（subprocess dar_weekly，SSE 日志）────────────────────
_run_state: dict = {"proc": None, "logs": [], "cmd": "", "env": "", "rc": None}


class RunReq(BaseModel):
    env: str = "test"
    steps: list[str] = []
    note: str = ""
    kind: str = "dar"      # dar=周流程（缺省）｜sink_export/sink_apply=工单沉淀编排
    dir: str = ""          # sink_apply：导出目录名 export_YYYYMMDD_HHMMSS
    reviewer: str = ""     # sink_apply：审核人


class StopReq(BaseModel):
    pass


@app.post("/api/run")
async def run(req: RunReq):
    if _run_state["proc"] and _run_state["proc"].poll() is None:
        raise HTTPException(409, "已有流程在跑（先停止）")
    if req.kind == "dar":
        if req.env not in ("test", "prod"):
            raise HTTPException(400, "env 取值 test|prod")
        if not req.steps:
            raise HTTPException(400, "steps 为空（dar 运行必须带步骤）")
        args = [sys.executable, os.path.join(HERE, "dar_weekly.py"), "--env", req.env]
        if req.note:
            args += ["--note", req.note]
        args += req.steps
        cmd_disp = f"dar_weekly --env {req.env} {' '.join(req.steps)}"
    elif req.kind in ("sink_export", "sink_apply"):
        if not os.path.isfile(os.path.join(HERE, "sink_flow.py")):
            raise HTTPException(500, "sink_flow.py 缺失")
        args = [sys.executable, os.path.join(HERE, "sink_flow.py"),
                "--export" if req.kind == "sink_export" else "--apply"]
        cmd_disp = "sink_flow --export"
        if req.kind == "sink_apply":
            if not re.fullmatch(r"export_\d{8}_\d{6}", req.dir or ""):
                raise HTTPException(400, "dir 应为 export_YYYYMMDD_HHMMSS")
            if not req.reviewer.strip():
                raise HTTPException(400, "apply 需要审核人名字")
            args += ["--dir", req.dir, "--reviewer", req.reviewer.strip()]
            cmd_disp = f"sink_flow --apply {req.dir} --reviewer {req.reviewer.strip()}"
    else:
        raise HTTPException(400, "kind 取值 dar|sink_export|sink_apply")
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        cwd=PROJ, env=_child_env())
    _run_state.update(proc=proc, logs=[], cmd=cmd_disp,
                      env=req.env if req.kind == "dar" else req.kind, rc=None)

    def reader():  # 独立线程持续读：与前端是否在线无关（防 PIPE 满卡死子进程）+ 留档供刷新恢复
        for line in proc.stdout:
            _run_state["logs"].append(line.rstrip())
        _run_state["rc"] = proc.wait()

    threading.Thread(target=reader, daemon=True).start()

    async def gen():
        i = 0
        yield f"data: {json.dumps({'event': 'begin', 'data': {'cmd': _run_state['cmd'], 'env': req.env}}, ensure_ascii=False)}\n\n"
        while True:
            logs = _run_state["logs"]
            while i < len(logs):
                yield f"data: {json.dumps({'event': 'log', 'data': {'line': logs[i]}}, ensure_ascii=False)}\n\n"
                i += 1
            if _run_state["rc"] is not None:
                break
            await asyncio.sleep(0.3)
        yield f"data: {json.dumps({'event': 'done', 'data': {'rc': _run_state['rc']}}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/run_status")
def run_status(after: int = -1):
    """运行状态 + 留档日志：页面刷新/重开后恢复现场；after=已收行数则只取增量。"""
    p = _run_state["proc"]
    running = bool(p and p.poll() is None)
    logs = _run_state["logs"]
    chunk = logs[after:after + 500] if after >= 0 else logs[-300:]
    return {"running": running, "cmd": _run_state["cmd"], "env": _run_state["env"],
            "rc": _run_state["rc"], "n": len(logs), "logs": chunk}


@app.post("/api/stop_run")
def stop_run():
    p = _run_state["proc"]
    if p and p.poll() is None:
        _kill_tree(p)
        return {"ok": True, "killed": True}
    return {"ok": True, "killed": False}


class ResetReq(BaseModel):
    env: str = "prod"
    kind: str = "retrieval"  # retrieval | l3


# 重判产物白名单：增量只认这些文件（jsonl+json 双源），删掉=下次跑全量重判
_RESET_FILES = {
    "retrieval": ["retrieval_check_{stamp}{ext}"],
    "l3": ["l3_judge_{stamp}{ext}", "l3_judge_all_{stamp}{ext}"],
}


@app.post("/api/reset_retrieval")
def reset_retrieval(req: ResetReq):
    """删当日判定产物（jsonl+json）→ 重跑对应步即全量重判（换判定模型后用）。

    kind=retrieval 删检索判定；kind=l3 删 L3 judge 两套（校准 l3_judge_* +
    预标 l3_judge_all_*）。两者都是 DAR_MODEL 判的，换模型要一起清。
    """
    if _run_state["proc"] and _run_state["proc"].poll() is None:
        raise HTTPException(409, "流程在跑，先停止")
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    pats = _RESET_FILES.get(req.kind)
    if not pats:
        raise HTTPException(400, "kind 取值 retrieval|l3")
    root = os.path.join(DATA_ROOT, req.env, "processed")
    stamp = time.strftime("%Y%m%d")
    removed = []
    for pat in pats:
        for ext in (".jsonl", ".json"):
            p = os.path.join(root, pat.format(stamp=stamp, ext=ext))
            if os.path.exists(p):
                os.remove(p)
                removed.append(os.path.basename(p))
    return {"ok": True, "removed": removed}


# ── 产物浏览（白名单）───────────────────────────────────────────
_ARTIFACT_PATTERNS = [
    "processed/weekly_*.md", "processed/weekly_*.json", "meta.json",
    "processed/unanswered_*.json",
    "segmentation_tool.html",
    "processed/retrieval_check_*.json",
    "processed/l3_judge_*.json",
    "processed/conversations_classified.jsonl",
    "processed/conversations_split.jsonl",
]
_SAFE_NAMES = set()


def _artifacts(env: str):
    root = os.path.join(DATA_ROOT, env)
    out = []
    for pat in _ARTIFACT_PATTERNS:
        for p in sorted(glob.glob(os.path.join(root, pat)), reverse=True)[:8]:
            rel = os.path.relpath(p, root).replace("\\", "/")
            _SAFE_NAMES.add((env, rel))
            out.append({"name": rel,
                        "mtime": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p))),
                        "size": os.path.getsize(p)})
    return sorted(out, key=lambda x: x["mtime"], reverse=True)


@app.get("/api/artifacts")
def artifacts(env: str = "test"):
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    return {"items": _artifacts(env)}


@app.get("/api/artifact")
def artifact(env: str = "test", name: str = ""):
    if (env, name) not in _SAFE_NAMES:
        raise HTTPException(404, "不在产物白名单内")
    p = os.path.join(DATA_ROOT, env, *name.split("/"))
    if not os.path.exists(p):
        raise HTTPException(404, "文件不存在")
    with open(p, encoding="utf-8", errors="replace") as fh:
        text = fh.read(400_000)
    return {"name": name, "truncated": len(text) >= 400_000, "content": text}


# ── 指标卡片（读最新周报 json）─────────────────────────────────
import re as _re


def _pct(s: str):
    m = _re.search(r"([\d.]+)%", s or "")
    return m.group(1) + "%" if m else (s or "")


def _weekly_files(env: str):
    return sorted(glob.glob(os.path.join(DATA_ROOT, env, "processed", "weekly_*.json")))


def _realtime_rates(env: str):
    """三口径即时读各步产物算（不依赖周报）：L1←l1 汇总、L3←judge 预标分布、
    L2←人工标注文件（保存到工作台即出）。周报（第五步吸收）出同分母口径后
    以周报为准，实时值只是过程口径。"""
    from collections import Counter as _Ctr
    proc = os.path.join(DATA_ROOT, env, "processed")
    rt = {}
    fs = sorted(glob.glob(os.path.join(proc, "direct_answer_summary_*.json")))
    if fs:
        s = json.load(open(fs[-1], encoding="utf-8"))
        ms = [v for k, v in (s.get("stats") or {}).items() if k.startswith("真实组|")]
        sq, st = (sum(m.get("segs_q", 0) for m in ms),
                  sum(m.get("segs_ticket", 0) for m in ms))
        if sq:
            rt["L1_段级"] = (f"话题级 {(1 - st / sq) * 100:.1f}%（{sq - st}/{sq}）"
                            "上界近似（未吸收标注）")
    fj = sorted(glob.glob(os.path.join(proc, "l3_judge_all_*.json")))
    if fj:
        rows = json.load(open(fj[-1], encoding="utf-8"))
        sub = [r for r in rows if r.get("grp") == "真实组" and r.get("pre")]
        p = _Ctr(r["pre"] for r in sub)
        ok, bad, unc = p.get("直答正确", 0), p.get("未直答", 0), p.get("未覆盖", 0)
        if ok + bad:
            rt["L3_AI同段"] = (
                f"端到端 {ok / (ok + bad + unc) * 100:.1f}%（{ok}/{ok + bad + unc}）"
                f"｜确定 {ok / (ok + bad) * 100:.1f}%（{ok}/{ok + bad}）"
                f"｜全段 {len(sub)}（未吸收标注）")
    mp = _manual_path(env)
    split = os.path.join(proc, "conversations_split.jsonl")
    if os.path.exists(mp) and os.path.exists(split):
        man = json.load(open(mp, encoding="utf-8"))
        convs = {}
        with open(split, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    c = json.loads(line)
                    convs[str(c["conversation_id"])] = c
        legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
        c = _Ctr(legacy.get(v, v)
                 for cid, lm in (man.get("labels") or {}).items()
                 if not (convs.get(cid) or {}).get("is_tester")
                 for v in lm.values())
        ok, bad, unc = c["直答正确"], c["未直答"], c["未覆盖"]
        if ok + bad:
            rt["L2_人工"] = (
                f"端到端 {ok / (ok + bad + unc) * 100:.1f}%（{ok}/{ok + bad + unc}）"
                f"｜确定 {ok / (ok + bad) * 100:.1f}%（{ok}/{ok + bad}）"
                f"｜已标 {ok + bad + unc} 段（标注即出，未跑吸收）")
    return rt


def _realtime_small(env: str):
    """小卡即时算：KB 缺口←第 3 步检索判定、标注进度/L3 precision/预标×人工
    对齐与召回←judge × 人工标注文件（标注现值优先于 judge 时的快照）。
    平均解决轮次仍由周报出（依赖第五步吸收 review）。"""
    from collections import Counter as _Ctr
    proc = os.path.join(DATA_ROOT, env, "processed")
    small = []
    # KB 缺口率与漏斗同口径（0916 用户定调：全页统一，漏斗为准）：
    # 未覆盖 ÷ 真实咨询已判定段
    _rows, _meta = _seg_rows(env)
    if _rows is not None:
        _L = _funnel_layers(_rows)
        if _L["qa"] and _L["qa"] - _L["undetermined"]:
            _judged = _L["qa"] - _L["undetermined"]
            small.append({"label": "KB 缺口率",
                          "value": f"{_L['uncovered'] / _judged * 100:.1f}%",
                          "sub": f"未覆盖 {_L['uncovered']}/{_judged} 段（真实咨询已判定，与漏斗一致）"})
    # 0916 精简：标注进度卡与漏斗复核进度重复，去；
    # KB 缺口率保留（用户要求三上四下对称）
    mp = _manual_path(env)
    split = os.path.join(proc, "conversations_split.jsonl")
    fj = sorted(glob.glob(os.path.join(proc, "l3_judge_all_*.json")))
    if not (os.path.exists(mp) and os.path.exists(split) and fj):
        return small
    try:
        legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
        man = json.load(open(mp, encoding="utf-8"))
        labs_man = {}
        for cid, lm in (man.get("labels") or {}).items():
            labs_man[str(cid)] = {int(k): legacy.get(v, v) for k, v in lm.items()
                                  if str(k).isdigit()}
        bounds_man = {str(k): v for k, v in (man.get("bounds") or {}).items()}
        convs = {}
        with open(split, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    c = json.loads(line)
                    convs[str(c["conversation_id"])] = c
        # 真实组总段数：与 dar_l1/dar_l3/标注工具同口径——人工边界优先、
        # 只计有咨询回合且有回答的段（LLM 切分又不滤咨询会虚到 452 vs 410，0910 实锤）
        n_real_segs = 0
        clsf = os.path.join(proc, "conversations_classified.jsonl")
        if os.path.exists(clsf):
            cls_all = {str(j["conversation_id"]): j["cls"] for j in
                       (json.loads(l) for l in open(clsf, encoding="utf-8") if l.strip())}
            for cid, c in convs.items():
                cl = cls_all.get(cid)
                if c.get("is_tester") or not cl or len(cl) != len(c["rounds"]):
                    continue
                if cid in bounds_man:
                    segs = sorted({0, *(int(x) for x in bounds_man[cid]
                                        if 0 <= int(x) < len(c["rounds"]))})
                else:
                    segs = [0] + [i for i in range(1, len(cl))
                                  if cl[i]["topic"] != cl[i - 1]["topic"]]
                for tid, s in enumerate(segs):
                    e = segs[tid + 1] if tid + 1 < len(segs) else len(c["rounds"])
                    if any(cl[i].get("q") and any(a.strip() for a in c["rounds"][i]["a"])
                           for i in range(s, e)):
                        n_real_segs += 1
        n_lab = sum(len(lm) for cid, lm in labs_man.items()
                    if cid in convs and not convs[cid].get("is_tester"))
        rows = json.load(open(fj[-1], encoding="utf-8"))
        labs4 = ("直接提单", "直答正确", "未直答", "未覆盖")
        hit, tot, cm = _Ctr(), _Ctr(), _Ctr()
        for r in rows:
            lab = labs_man.get(str(r["cid"]), {}).get(r.get("astart")) or r.get("lab")
            pre = r.get("pre")
            if lab in labs4 and pre in labs4:
                tot[pre] += 1
                hit[pre] += pre == lab
                cm[(lab, pre)] += 1
        n_hit, n_tot = sum(hit.values()), sum(tot.values())
        if n_tot:
            small.append({"label": "L3 precision",
                          "value": f"{n_hit / n_tot * 100:.0f}%",
                          "sub": (f"{n_hit}/{n_tot} · ≥90% 可放权（人工只抽检）"
                                  if n_hit / n_tot >= 0.9
                                  else f"{n_hit}/{n_tot} · 未达 90% 放权线")})
            rl = [cm[(l, l)] / sum(cm[(l, k)] for k in labs4) * 100
                  for l in labs4 if sum(cm[(l, k)] for k in labs4)]
            if rl:
                small.append({"label": "预标召回",
                              "value": f"{sum(rl) / len(rl):.0f}%",
                              "sub": "宏平均｜" + "｜".join(
                                  f"{l} {cm[(l, l)] / sum(cm[(l, k)] for k in labs4) * 100:.0f}%"
                                  for l in labs4 if sum(cm[(l, k)] for k in labs4))[:80]
                              + "（人工已标段）"})
    except Exception:
        pass
    return small


@app.get("/api/metrics")
def metrics(env: str = "prod"):
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    # 周报块（第五步产物：same_base/wow/trend/this_week/小卡）——没出就没有
    files = _weekly_files(env)
    src = env
    rt = _realtime_rates(env)
    if not files and not rt and env == "prod":
        src = "test"  # 本环境连过程产物都没有才借 test 展示
        files = _weekly_files(src)
        rt = _realtime_rates(src)
    if not files and not rt:
        return {"found": False}
    # 历史趋势：各期周报同分母三口径数值化（多周累积后成走势线）
    trend = []
    for p in files:
        try:
            r = json.load(open(p, encoding="utf-8"))
            same = r.get("dar_rates_same_base") or {}
            pt = {}
            for k in ("L1_段级", "L2_人工", "L3_AI同段"):
                v = _pct(same.get(k)).rstrip("%")
                try:
                    if v:
                        pt[k] = float(v)
                except ValueError:
                    pass
            if pt:
                trend.append({"date": r.get("date") or os.path.basename(p)[7:17],
                              **pt})
        except Exception:
            continue
    rep = {}
    if files:
        with open(files[-1], encoding="utf-8") as fh:
            rep = json.load(fh)

    hero, small = [], []
    same = rep.get("dar_rates_same_base") or {}
    # hero 优先级：本环境周报同分母口径 > 本环境过程产物实时值（各步跑完
    # 即时刷新，不等第五步）；两者皆无才落到回退环境的数
    zh = {"L1_段级": ("L1 机器信号", "未出单即算直答（上界）"),
          "L2_人工": ("L2 人工标注", "端到端真实口径（未覆盖计入分母）· 真值"),
          "L3_AI同段": ("L3 AI 预标", "端到端含未覆盖 · 以 L2 为真值评估 · judge 偏宽仅供参考")}
    for k, (label, sub) in zh.items():
        v = same.get(k) or rt.get(k)
        if v:
            hero.append({"label": label, "value": _pct(v), "sub": f"{v} · {sub}"})
    # 小卡即时算（KB 缺口/标注进度/precision/召回——产物到哪算到哪）；
    # 平均解决轮次依赖第五步吸收 review，仍由周报出
    small = _realtime_small(env if src == env else src)
    ar = rep.get("avg_rounds") or {}
    if ar.get("直答正确段"):
        rest = " · ".join(f"{k} {v}" for k, v in ar.items() if k != "直答正确段")
        m = _re.match(r"([\d.]+)", ar["直答正确段"])
        small.append({"label": "平均解决轮次", "value": (m.group(1) + " 轮") if m else ar["直答正确段"],
                      "sub": f"直答正确段 {ar['直答正确段']}" + (f" · {rest}" if rest else "")})
    return {"found": True, "source_env": src,
            "file": os.path.basename(files[-1]) if files else "",
            "date": rep.get("date"), "note": rep.get("note"),
            "meta": rep.get("meta"), "manual_progress": rep.get("manual_progress"),
            "avg_rounds": ar, "ticket_quality": rep.get("ticket_quality"),
            "dar_rates": rep.get("dar_rates"), "same_base": same,
            "wow": rep.get("wow"), "this_week": rep.get("this_week"),
            "unanswered_total": rep.get("unanswered_total"),
            "unanswered_this_week": rep.get("unanswered_this_week"),
            # 数据版本号 = 导出时刻（日期时间组成，与 git 无关——0916 用户定调）
            "export_at": _latest_export_at(env),
            "trend": trend, "hero": hero, "small": small}


def _latest_export_at(env: str):
    try:
        metas = json.load(open(os.path.join(DATA_ROOT, env, "meta.json"),
                               encoding="utf-8"))
        return (metas[-1].get("at") or "")[:16].replace("T", " ")
    except Exception:
        return ""


@app.get("/api/unanswered")
def unanswered(env: str = "prod"):
    """未直答问题全量清单（最新 unanswered_*.json），前端按周/类型过滤。"""
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    files = sorted(glob.glob(os.path.join(DATA_ROOT, env, "processed",
                                          "unanswered_*.json")))
    if not files and env == "prod":
        files = sorted(glob.glob(os.path.join(DATA_ROOT, "test", "processed",
                                              "unanswered_*.json")))
    if not files:
        return {"found": False}
    with open(files[-1], encoding="utf-8") as fh:
        ua = json.load(fh)
    return {"found": True, "file": os.path.basename(files[-1]),
            "total": ua.get("total"), "weeks": ua.get("weeks"),
            "items": ua.get("items", [])}


def _segments_from_cls(cls):
    """段边界：相邻 topic 变化即断段（与 dar_l1/_fail_items/标注工具同源）。"""
    segs, cur = [], 0
    for i in range(1, len(cls)):
        prev = cls[i - 1].get("topic", 0) or 0
        now = cls[i].get("topic", 0) or 0
        if prev != now:
            segs.append((cur, i))
            cur = i
    if cls:
        segs.append((cur, len(cls)))
    return segs


def _suggested_pool():
    """「猜你想问」推荐池（前端 suggestedQuestions.ts 的数组条目）。
    用户点推荐问题发送 → 段首问精确匹配池条目 → 判 suggested 层
    （平台主动推荐的问题非用户真实困惑，不进直答分母，旁支单列）。"""
    p = os.path.join(PROJ, "frontend", "src", "shared", "data", "suggestedQuestions.ts")
    try:
        src = open(p, encoding="utf-8").read()
    except OSError:
        return set()
    return {m.group(1).strip() for m in re.finditer(r"'([^']+)'", src)}


def _seg_rows(env):
    """全量段级清单（漏斗六层数据源）：SPLIT 段边界 × L1 cls × L3/人工有效判定。

    layer 判定顺序：tester（会话级）→ chitchat（段内无 q=true）→ ticket
    （eff=直接提单/建议转单）→ answered/unanswered/uncovered（eff）→
    undetermined（无任何判定，AI 未判到且未人工标注）。
    返回 (rows, meta)；rows 每段一行。"""
    proc = os.path.join(DATA_ROOT, env, "processed")
    split_p = os.path.join(proc, "conversations_split.jsonl")
    cls_p = os.path.join(proc, "conversations_classified.jsonl")
    jl = sorted(glob.glob(os.path.join(proc, "l3_judge_all_*.json")))
    if not (os.path.exists(split_p) and os.path.exists(cls_p) and jl):
        return None, {"reason": "缺产物：先跑 export prepare l1 l3（周流程第 1-7 步）"}
    cls_by = {}
    with open(cls_p, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                j = json.loads(line)
                cls_by[str(j["conversation_id"])] = j.get("cls") or []
    # 预过滤 cls_by 里的排除用户段（避免漏斗里残留）
    # 实际段过滤在 split_p 读取循环里
    pre_by = {}
    for r in json.load(open(jl[-1], encoding="utf-8")):
        pre_by[(str(r.get("cid")), r.get("astart"))] = r
    labs_man = {}
    bounds_man = {}  # 人工切分边界（漏斗与 dar_l3/L2 指标/标注工具统一口径：bounds 优先）
    mp = _manual_path(env)
    if os.path.exists(mp):
        man = json.load(open(mp, encoding="utf-8"))
        legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
        for cid, lm in (man.get("labels") or {}).items():
            labs_man[str(cid)] = {int(k): legacy.get(v, v) for k, v in lm.items()
                                  if str(k).isdigit()}
        bounds_man = {str(k): v for k, v in (man.get("bounds") or {}).items()}
    suggested_pool = _suggested_pool()
    rows = []
    unprocessed = []  # 0915 反馈：未标注段单独成一个可点的模块，不进漏斗
    with open(split_p, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            c = json.loads(line)
            cls = cls_by.get(str(c["conversation_id"]))
            if not cls:
                continue
            # 段边界：人工 bounds 优先（0915 实锤：L1 重跑后 LLM 切分漂移，
            # 上周人工标签/L3 预标按旧段首存的全部错位——漏斗必须与 dar_l3/
            # L2 指标/标注工具同口径），无 bounds 的会话（本周新增）用 LLM 切分
            cid_s = str(c["conversation_id"])
            if cid_s in bounds_man:
                starts = sorted({0, *(int(x) for x in bounds_man[cid_s]
                                      if 0 <= int(x) < len(cls))})
                seg_span = [(s, starts[i + 1] if i + 1 < len(starts) else len(cls))
                            for i, s in enumerate(starts)]
            else:
                seg_span = _segments_from_cls(cls)
            for a0, a1 in seg_span:
                seg_cls = cls[a0:a1]
                man = labs_man.get(str(c["conversation_id"]), {}).get(a0)
                jrow = pre_by.get((str(c["conversation_id"]), a0))
                pre = (jrow or {}).get("pre") or ""
                eff, src = (man, "manual") if man else (pre, "pre")
                # 段首个咨询回合的问题（跳过「你好」式开场）
                qi = next((i for i, s in enumerate(seg_cls) if s.get("q")), 0)
                r0 = (c["rounds"] or [])[a0 + qi] if a0 + qi < len(c["rounds"] or []) else {}
                tks = [t["id"] for t in (c.get("tasks") or [])]
                # 段内工单动作（a_seg 提取的 db_id）——用户提完删会话也能对上
                tic_ids = []
                seg_ticketed = False
                for rr in (c.get("rounds") or [])[a0:a1]:
                    for s in (rr.get("a_seg") or []):
                        if s.get("action") == "ticket_draft":
                            seg_ticketed = True
                            if s.get("db_id"):
                                tic_ids.append(s["db_id"])
                # 猜你想问=元筛选，优先于人工标签（用户口径：推荐点击不进直答
                # 统计，标没标过都一样——0916 走查实锤已标段命中池仍留在 qa）
                seg_has_sug = any(((rr.get("q") or "").strip() in suggested_pool)
                                  for rr in (c.get("rounds") or [])[a0:a1])
                # 0915 用户硬要求：SKIP_USER_IDS 静默归到「测试人员」层（不显示排除徽章）
                is_skip = str(c.get("user_id") or "") in SKIP_USER_IDS
                if c.get("is_tester") or is_skip:
                    layer = "tester"
                elif seg_has_sug:
                    layer = "suggested"
                elif eff == "寒暄":
                    layer = "chitchat"
                # 人工判非提单类优先于 seg_ticketed（0915 反馈：人工判"未覆盖"被
                # 压进 ticket 层——人工意图为准）
                elif man and eff == "未覆盖":
                    layer = "uncovered"
                elif man and eff == "直答正确":
                    layer = "answered"
                elif man and eff == "未直答":
                    layer = "unanswered"
                elif seg_ticketed and (man or pre):
                    # 真出过工单草稿 + 有判定（人工或 AI 预标）= ticket
                    # fresh import 只有 seg_ticketed 没预标 → 归 undetermined
                    # （0915 用户反馈：今天新导入的进了直接提单——没判定不应进已判定层）
                    layer = "ticket"
                elif man and eff in ("直接提单", "建议转单"):
                    # 人工确认是提单类——归转工单层
                    layer = "ticket"
                # L1 无咨询信号判寒暄——但有人工标签时标签优先（走查发现误判，
                # 点任一标签即从寒暄层捞进对应层）
                elif not any(s.get("q") for s in seg_cls) and not man:
                    layer = "chitchat"
                elif eff in ("直接提单", "建议转单"):
                    # L3 预标 + 未人工确认——不归 ticket，归 undetermined 待走查
                    layer = "undetermined"
                elif eff == "直答正确":
                    layer = "answered"
                elif eff == "未直答":
                    layer = "unanswered"
                elif eff == "未覆盖":
                    layer = "uncovered"
                else:
                    layer = "undetermined"
                # 0915 用户反馈：fresh import + 无判定（无人工 + 无 AI 预标）= 不进漏斗
                # 等用户跑 l3 / 人工标注后再进入——避免空段被错放任何"已判定"层
                # tester/suggested/寒暄 是元筛选层（不依赖 eff），保留
                # SKIP 用户也保留在 tester 层
                if (not eff and layer == "undetermined"
                    and not c.get("is_tester") and not seg_has_sug
                    and str(c.get("user_id") or "") not in SKIP_USER_IDS):
                    unprocessed.append({
                        "cid": c["conversation_id"], "astart": a0, "aend": a1,
                        "user": c.get("name") or c.get("user_id"),
                        "at": r0.get("at") or c.get("created_at"),
                        "question": (r0.get("q") or "")[:200],
                        # AI 回答预览：同漏斗行口径，走查判断标签用
                        "answer": next((ai for rr in (c.get("rounds") or [])[a0:a1]
                                        for ai in [((rr.get("a") or [""])[0] or "")[:160]]
                                        if ai.strip()), "")[:160].lstrip("` \n"),
                        "n_files": sum(len(rr.get("files") or []) for rr in
                                       (c.get("rounds") or [])[a0:a1]),
                    })
                    continue
                rows.append({
                    "cid": c["conversation_id"], "astart": a0, "aend": a1,
                    "layer": layer, "eff": eff, "src": src,
                    "question": (r0.get("q") or "")[:200],
                    # AI 回答预览：段内第一条非工单动作回答的开头（走查初判不点开也要能看）
                    "answer": next((ai for rr in (c.get("rounds") or [])[a0:a1]
                                    for ai in [((rr.get("a") or [""])[0] or "")[:160]]
                                    if ai.strip()), "")[:160].lstrip("` \n"),
                    "type": seg_cls[qi].get("type", "其他") if seg_cls else "其他",
                    "user": c.get("name") or c.get("user_id"),
                    "is_tester": bool(c.get("is_tester")),
                    "at": r0.get("at") or c.get("created_at"),
                    "task_ids": tks, "ticket_ids": tic_ids,
                    "n_files": sum(len(rr.get("files") or []) for rr in
                                   (c.get("rounds") or [])[a0:a1]),
                })
    return rows, {"split": split_p, "judge": os.path.basename(jl[-1]), "unprocessed": unprocessed}


def _funnel_layers(rows):
    def n(l):
        return sum(1 for r in rows if r["layer"] == l)

    total = len(rows)
    tester, chitchat, ticket = n("tester"), n("chitchat"), n("ticket")
    suggested = n("suggested")
    answered, unanswered, uncovered = n("answered"), n("unanswered"), n("uncovered")
    undet = n("undetermined")
    qa = total - tester - chitchat - ticket - suggested
    judged = answered + unanswered + uncovered
    # 双口径：全量（AI 预标兜底，走查过程口径）vs 人工已标段（=周报 L2_人工同口径）
    m_ok = sum(1 for r in rows if r["src"] == "manual" and r["eff"] == "直答正确")
    m_bad = sum(1 for r in rows if r["src"] == "manual" and r["eff"] in ("未直答", "未覆盖"))
    return {
        "total": total, "tester": tester, "chitchat": chitchat, "ticket": ticket,
        "suggested": suggested,
        "qa": qa, "answered": answered, "unanswered": unanswered,
        "uncovered": uncovered, "undetermined": undet,
        "direct_rate": round(answered / judged * 100, 1) if judged else None,
        "manual_rate": round(m_ok / (m_ok + m_bad) * 100, 1) if (m_ok + m_bad) else None,
        "manual_judged": m_ok + m_bad,
        "reviewed": sum(1 for r in rows if r["src"] == "manual"),
        "consistent": qa == answered + unanswered + uncovered + undet,
    }


def _week_stats(rows: list) -> dict:
    """本周（自然周，周一起）直答率：与漏斗同源同口径，人工标注后即刷。

    大字=周直答率（本周 qa 四层中直答正确占比）；小字=周新增段/已判定/直答数。
    tester/suggested/chitchat/ticket 层的段计入"周新增"，不计入直答率分母。"""
    from datetime import datetime, timedelta
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    n_new = n_ans = n_unans = n_uncov = n_undet = 0
    for r in rows:
        if (r.get("at") or "")[:10] < monday:
            continue
        n_new += 1
        layer = r.get("layer")
        if layer == "answered":
            n_ans += 1
        elif layer == "unanswered":
            n_unans += 1
        elif layer == "uncovered":
            n_uncov += 1
        elif layer == "undetermined":
            n_undet += 1
    judged = n_ans + n_unans + n_uncov + n_undet
    return {"monday": monday, "new_total": n_new, "judged": judged,
            "answered": n_ans, "unanswered": n_unans, "uncovered": n_uncov,
            "undetermined": n_undet,
            "rate": round(n_ans / judged * 100, 1) if judged else None}


@app.get("/api/funnel")
def funnel(env: str = "prod"):
    """漏斗六层计数 + 直答率（已判定口径）+ 复核进度 + 层守恒。
    unprocessed：fresh import + 无判定段（不进漏斗，独立可点的"未处理"模块）。
    week：本周（自然周）直答率——人工标注后即刷，与漏斗同源同口径。"""
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    rows, meta = _seg_rows(env)
    if rows is None:
        return {"found": False, **meta}
    return {"found": True, "layers": _funnel_layers(rows),
            "week": _week_stats(rows),
            "unprocessed": meta.get("unprocessed", []),
            "unprocessed_count": len(meta.get("unprocessed", [])),
            "meta": meta}


@app.get("/api/funnel_segs")
def funnel_segs(env: str = "prod", layer: str = "", week: str = "",
                type_: str = Query("", alias="type"), user: str = ""):
    """漏斗某层段明细（走查主战场）：layer 必填，week/type/user 可选过滤。"""
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    rows, meta = _seg_rows(env)
    if rows is None:
        return {"found": False, **meta}
    if not layer:
        raise HTTPException(400, "layer 必填")
    # qa（真实咨询/分母）= 四细分层合集——领导要求的「真实咨询全过一遍」走查入口
    qa_set = {"answered", "unanswered", "uncovered", "undetermined"}
    out = [r for r in rows if r["layer"] == layer
           or (layer == "qa" and r["layer"] in qa_set)]
    if week:
        out = [r for r in out if (r.get("at") or "")[:10] >= week]
    if type_:
        out = [r for r in out if r["type"] == type_]
    if user:
        out = [r for r in out if user in (r.get("user") or "")]
    out.sort(key=lambda r: (r.get("at") or "", r["cid"]))
    return {"found": True, "layer": layer, "total": len(out), "items": out}


class LabelSegReq(BaseModel):
    env: str = "prod"
    cid: int
    astart: int
    label: str


_LABELS_VALID = ("直答正确", "未直答", "未覆盖", "直接提单", "建议转单", "寒暄")


@app.post("/api/label_seg")
def label_seg(req: LabelSegReq):
    """漏斗走查页内单段改判：merge 进 manual_segmentation.json 的 labels
    （save_manual 是标注工具全量覆盖，走查单段改判走这里防读改写竞态），
    并后台触发 l1r（人工标签吸收，刷新指标即见）。"""
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    if req.label not in _LABELS_VALID:
        raise HTTPException(400, f"label 取值 {'/'.join(_LABELS_VALID)}")
    p = _manual_path(req.env)
    d = {"bounds": {}, "labels": {}}
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            d = {"bounds": {}, "labels": {}}
    d.setdefault("labels", {}).setdefault(str(req.cid), {})[str(req.astart)] = req.label
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    busy = bool(_run_state["proc"] and _run_state["proc"].poll() is None)
    split = os.path.join(DATA_ROOT, req.env, "processed", "conversations_split.jsonl")
    if not busy and os.path.exists(split):
        subprocess.Popen([sys.executable, os.path.join(HERE, "dar_weekly.py"),
                          "--env", req.env, "l1r"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=PROJ, env=_child_env())
        return {"ok": True, "rerun": True}
    return {"ok": True, "rerun": False}


@app.get("/api/seg_detail")
def seg_detail(env: str = "prod", cid: int = 0, a0: int = 0, a1: int = 0):
    """走查页展开段对话：返回该段 rounds 切片（q/a_seg 分段/files 图片/时间）。"""
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    if not cid or a0 < 0 or a1 <= a0:
        raise HTTPException(400, "cid/a0/a1 参数无效")
    split_p = os.path.join(DATA_ROOT, env, "processed", "conversations_split.jsonl")
    if not os.path.exists(split_p):
        raise HTTPException(404, "缺 conversations_split.jsonl")
    with open(split_p, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            c = json.loads(line)
            # SKIP 用户现在归到 tester 层，可以正常查看（不再 404）
            # csv 导出的字段全是字符串——str 比较防 int/str 不匹配漏查
            if str(c.get("conversation_id")) == str(cid):
                rounds = (c.get("rounds") or [])[a0:a1]
                return {"found": True, "cid": cid, "rounds": rounds,
                        "n_all": len(c.get("rounds") or [])}
    raise HTTPException(404, f"会话 {cid} 不存在")


@app.get("/api/seg_to_ticket")
def seg_to_ticket(env: str = "prod", cid: int = 0, a0: int = 0, a1: int = 0):
    """走查展开段时附带查关联工单：复用 task_to_dict 字段映射（与 call 端口径一致）。

    段范围 rounds 内 a_seg action="ticket_draft" 的 db_id 即 task.id。
    优先连 127.0.0.1:3306（用户日常起的 DB 隧道，可读到最新）→ fallback 本地
    tasks.csv.gz（dar_weekly export 产物，离线快照但一定在）→ 最终 fallback 仅返 id。
    """
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    if not cid or a0 < 0 or a1 <= a0:
        raise HTTPException(400, "cid/a0/a1 参数无效")
    split_p = os.path.join(DATA_ROOT, env, "processed", "conversations_split.jsonl")
    if not os.path.exists(split_p):
        return {"found": True, "cid": cid, "tickets": []}
    # 1) 找会话 → 收集 a_seg.ticket_draft 的 db_id
    task_ids: list[int] = []
    with open(split_p, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            c = json.loads(line)
            if str(c.get("conversation_id")) != str(cid):
                continue
            # SKIP 用户归到 tester 层，正常返回工单数据
            for rr in (c.get("rounds") or [])[a0:a1]:
                for s in (rr.get("a_seg") or []):
                    if s.get("action") == "ticket_draft" and s.get("db_id"):
                        task_ids.append(int(s["db_id"]))
            break
    seen = set(); task_ids = [t for t in task_ids if not (t in seen or seen.add(t))]
    if not task_ids:
        return {"found": True, "cid": cid, "tickets": []}

    # 2) 主路径：连 127.0.0.1:3306（用户日常隧道），复用 call 平台 task_to_dict
    try:
        from app.core.db import SessionLocal
        from app.models.task import Task
        from ai.core.task_adapter import task_to_dict
        db = SessionLocal()
        try:
            out = []
            for tid in task_ids:
                task = db.query(Task).filter(Task.id == tid).first()
                if task is None:
                    continue
                out.append(task_to_dict(task))
        finally:
            db.close()
        if out:
            return {"found": True, "cid": cid, "tickets": out, "source": "db"}
    except Exception as e:
        db_warn = f"DB 不可达：{e}"

    # 3) Fallback：本地产物 tasks.csv.gz（dar_weekly export 写盘，列与 DB 对齐）
    # 列序：id,title,task_type,status,created_by,project_name,source,external_id,created_at,metadata_info
    tasks_p = os.path.join(DATA_ROOT, env, "tasks.csv.gz")
    if not os.path.exists(tasks_p):
        return {"found": True, "cid": cid, "tickets": [], "ticket_ids": task_ids,
                "warn": db_warn + "；且无 tasks.csv.gz（请先跑 dar_weekly export）"}
    import gzip as _gz
    import csv as _csv
    id_set = set(task_ids)
    out = []
    try:
        with _gz.open(tasks_p, "rt", encoding="utf-8") as fh:
            r = _csv.DictReader(fh)
            for row in r:
                try:
                    rid = int(row.get("id") or 0)
                except ValueError:
                    continue
                if rid not in id_set:
                    continue
                # SKIP 用户归到 tester 层，他们的工单正常返回
                meta = {}
                try:
                    meta = json.loads(row.get("metadata_info") or "{}")
                except Exception:
                    meta = {}
                out.append({
                    "id": rid, "ticket_id": rid,
                    "title": row.get("title") or "",
                    "type": row.get("task_type") or "other",
                    "status": row.get("status") or "pending",
                    "created_by": row.get("created_by") or "",
                    "created_at": row.get("created_at") or "",
                    "project": row.get("project_name") or "",
                    "source": row.get("source") or "ai",
                    "priority": meta.get("priority") or "中",
                    "assigned_to": meta.get("assigned_to") or "",
                    "assigned_to_name": meta.get("assigned_to_name") or meta.get("assigned_to") or "",
                    "created_by_name": meta.get("created_by_name") or row.get("created_by") or "",
                    "diagnosis": meta.get("diagnosis") or {},
                    "location": meta.get("location") or "",
                    "robot_type": meta.get("robot_type") or "",
                    "fault_code": meta.get("fault_code") or "",
                })
                if len(out) >= len(task_ids):
                    break
        if out:
            return {"found": True, "cid": cid, "tickets": out, "source": "csv",
                    "warn": db_warn}
    except Exception as e:
        return {"found": True, "cid": cid, "tickets": [], "ticket_ids": task_ids,
                "warn": f"{db_warn}；读 tasks.csv.gz 也失败：{e}"}
    return {"found": True, "cid": cid, "tickets": [], "ticket_ids": task_ids,
            "warn": f"{db_warn}；csv 中未找到这些 id"}


def _esc_attr(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _img_html(f: dict, img_base: str) -> str:
    """图片 HTML：大图（>3MB）/gif 不自动加载，占位点击；小图直接 img（lazy+onerror 重试）。"""
    size = int(f.get("size") or 0)
    path = _esc_attr(img_base + f.get("object_path", ""))
    alt = _esc_attr(f.get("filename", ""))
    if size > 3 * 1024 * 1024 or path.lower().endswith(".gif"):
        mb = f"{size / 1048576:.1f}"
        return (f'<span class="imgfail" onclick="loadBig(this)" '
                f'data-path="{path}" data-alt="{alt}">🖼️ 大图 {mb}MB · 点击加载</span>')
    return (f'<img loading="lazy" src="{path}" alt="{alt}" onerror="imgFail(this)">')


def _img_js() -> str:
    """seg_page/layer_page 共用图片 JS：重试 2 次（1.5s/4s 退避）+ 大图点击加载。"""
    return """function imgFail(img){
  var n = +(img.dataset.retried || 0);
  if(n < 2){
    img.dataset.retried = n + 1;
    setTimeout(function(){ if(img.isConnected) img.src = img.src.split("?")[0] + "?r=" + Date.now(); }, n === 0 ? 1500 : 4000);
    return;
  }
  var ph = document.createElement("span");
  ph.className = "imgfail";
  ph.textContent = "图失效·点重试";
  ph.title = img.alt;
  ph.onclick = function(){
    var i = document.createElement("img");
    i.alt = ph.title;
    i.loading = "lazy";
    i.onerror = function(){ imgFail(i); };
    i.src = img.src.split("?")[0] + "?r=" + Date.now();
    ph.replaceWith(i);
  };
  img.replaceWith(ph);
}
function loadBig(el){
  var i = document.createElement("img");
  i.alt = el.dataset.alt;
  i.loading = "lazy";
  i.onerror = function(){ imgFail(i); };
  i.src = el.dataset.path;
  el.replaceWith(i);
}"""


@app.get("/seg_page")
def seg_page(env: str = "prod", cid: int = 0, a0: int = 0, a1: int = 0):
    """走查「新开网页」：独立页面看段完整对话（大图直出，无需在漏斗页内滚动）。"""
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    if not cid or a0 < 0 or a1 <= a0:
        raise HTTPException(400, "cid/a0/a1 参数无效")
    split_p = os.path.join(DATA_ROOT, env, "processed", "conversations_split.jsonl")
    conv = None
    if os.path.exists(split_p):
        with open(split_p, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                c = json.loads(line)
                if str(c.get("conversation_id")) == str(cid):
                    conv = c
                    break
    if not conv:
        raise HTTPException(404, f"会话 {cid} 不存在")
    img_base = ("https://usp.ep-zl.com/p" if env == "prod"
                else "http://125.122.97.107/t") + "/api/call/files/"

    def esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    parts = []
    for rr in (conv.get("rounds") or [])[a0:a1]:
        imgs = "".join(_img_html(f, img_base) for f in (rr.get("files") or []))
        acts = "".join(
            f'<div class="act">🎫 生成工单草稿 #{esc(s.get("db_id") or "?")}</div>'
            for s in (rr.get("a_seg") or []) if s.get("action") == "ticket_draft")
        ans = "".join(
            f'<div class="ai">{esc(s.get("text", ""))}</div>'
            for s in (rr.get("a_seg") or [])
            if s.get("action") != "ticket_draft" and (s.get("text") or "").strip())
        parts.append(
            f'<div class="turn"><div class="uq"><b>用户</b> · {esc(rr.get("at", ""))[:19]}'
            f'<div>{esc(rr.get("q", ""))}</div>{imgs}</div>{acts}{ans}</div>')
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>会话 {esc(cid)} · 段 {a0}-{a1}（{esc(env)}）</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f1f5f9;margin:0;padding:24px}}
.wrap{{max-width:760px;margin:0 auto}}
h2{{font-size:15px;color:#475569}}
.turn{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px;margin-bottom:14px}}
.uq{{color:#0f172a;font-size:14px;line-height:1.7}}
.uq b{{color:#3d76c4;margin-right:8px}}
.ai{{background:#f8fafc;border-left:3px solid #3d76c4;border-radius:8px;
padding:10px 14px;margin:10px 0 4px 16px;color:#334155;font-size:13.5px;
white-space:pre-wrap;line-height:1.7}}
.act{{display:inline-block;background:#fef3c7;color:#92400e;border-radius:6px;
padding:2px 10px;font-size:12px;margin:4px 0 4px 16px}}
img{{max-width:420px;border-radius:10px;border:1px solid #e2e8f0;margin:8px 0;display:block}}
.imgfail{{display:inline-block;font-size:12px;color:#64748b;background:#f1f5f9;border:1px dashed #cbd5e1;
border-radius:6px;padding:3px 10px;cursor:pointer}}
.imgfail:hover{{color:#3d76c4;border-color:#3d76c4}}
</style></head><body><div class="wrap">
<h2>会话 {esc(cid)} · 第 {a0}-{a1} 回合 · 用户 {esc(conv.get("name") or conv.get("user_id"))} · {esc(env)} 数据</h2>
{''.join(parts) or '<p>（空段）</p>'}
</div>
<script>
{_img_js()}
</script></body></html>"""
    return HTMLResponse(html)


@app.get("/layer_page")
def layer_page(env: str = "prod", layer: str = ""):
    """整层走查网页：该层全部段连续排版（对话+图片+行内改判）——和领导一起审的入口。"""
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    if not layer:
        raise HTTPException(400, "layer 必填")
    rows, meta = _seg_rows(env)
    if rows is None:
        raise HTTPException(404, meta.get("reason", "缺产物"))
    qa_set = {"answered", "unanswered", "uncovered", "undetermined"}
    items = [r for r in rows if r["layer"] == layer
             or (layer == "qa" and r["layer"] in qa_set)]
    items.sort(key=lambda r: (r.get("at") or "", r["cid"]))
    split_rounds = {}

    def esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    split_p = os.path.join(DATA_ROOT, env, "processed", "conversations_split.jsonl")
    if os.path.exists(split_p):
        with open(split_p, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                c = json.loads(line)
                split_rounds[str(c.get("conversation_id"))] = c
    img_base = ("https://usp.ep-zl.com/p" if env == "prod"
                else "http://125.122.97.107/t") + "/api/call/files/"
    names = {"total": "全部对话", "tester": "测试人员对话", "chitchat": "寒暄",
             "suggested": "猜你想问（平台推荐）", "ticket": "直接连/转工单",
             "qa": "真实咨询问题（分母·走查全量）", "answered": "直答 ✓",
             "unanswered": "未直答·覆盖答不好", "uncovered": "未直答·库未覆盖",
             "undetermined": "未判定"}
    parts = []
    for idx, r in enumerate(items):
        c = split_rounds.get(str(r["cid"])) or {}
        turns = []
        for rr in (c.get("rounds") or [])[r["astart"]:r["aend"]]:
            imgs = "".join(_img_html(f, img_base) for f in (rr.get("files") or []))
            acts = "".join(
                f'<div class="act">🎫 生成工单草稿 #{esc(s.get("db_id") or "?")}</div>'
                for s in (rr.get("a_seg") or []) if s.get("action") == "ticket_draft")
            ans = "".join(
                f'<div class="ai">{esc(s.get("text", ""))}</div>'
                for s in (rr.get("a_seg") or [])
                if s.get("action") != "ticket_draft" and (s.get("text") or "").strip())
            turns.append(
                f'<div class="turn"><div class="uq"><b>用户</b> · {esc(rr.get("at", ""))[:19]}'
                f'<div>{esc(rr.get("q", ""))}</div>{imgs}</div>{acts}{ans}</div>')
        lbls = [("直答正确", "#2e9e5b"), ("未直答", "#d9534f"), ("未覆盖", "#d9534f"),
                ("直接提单", "#d97706"), ("建议转单", "#d97706"), ("寒暄", "#98a2b3")]
        # 标签只属于真实咨询层（0916 用户定调：其他层只是浏览）
        btns = ("".join(
            f'<button class="lb{" on" if r["eff"] == lb and r["src"] == "manual" else ""}" '
            f'style="{"" if r["eff"] == lb and r["src"] == "manual" else f"--c:{col};"}" '
            f'onclick="lab(this,{r["cid"]},{r["astart"]},\'{lb}\')">{lb}</button>'
            for lb, col in lbls) if layer in ("qa", "chitchat") else "")
        tks = [str(t) for t in (r.get("ticket_ids") or []) + (r.get("task_ids") or [])]
        tk_span = ""
        if tks:
            tk_span = '<span class="mt">🎫 ' + " ".join("#" + t for t in dict.fromkeys(tks)) + "</span>"
        nf_span = ""
        if r.get("n_files"):
            nf_span = '<span class="mt" style="color:#3d76c4">📷 ' + str(r["n_files"]) + "</span>"
        parts.append(
            f'<div class="seg {"is-man" if r["src"] == "manual" else "is-pre"}" id="s{r["cid"]}_{r["astart"]}">'
            f'<div class="sh"><span class="idx">#{idx + 1}</span>'
            f'<span class="eff {r["src"]}">{esc(r["eff"] or "未判定")}·{"人工" if r["src"] == "manual" else "AI预标"}</span>'
            f'<span class="mt">{esc(r["type"])}</span><span class="mt">{esc(r["user"])}</span>'
            f'<span class="mt">{esc((r["at"] or "")[:16])}</span>'
            f'<span class="mt">会话{r["cid"]}</span>'
            f'{tk_span}{nf_span}'
            f'</div>{btns}<div class="segs-turns">{"".join(turns) or "<p>（空段）</p>"}</div></div>')
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>走查 · {esc(names.get(layer, layer))}（{len(items)} 段）</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#eef2f7;margin:0;padding:0}}
.top{{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.92);backdrop-filter:blur(8px);
border-bottom:1px solid #e2e8f0;padding:12px 24px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}}
.top h2{{font-size:16px;color:#0f172a;margin:0}}
.top .n{{font-size:12.5px;color:#64748b}}
.top .bk{{margin-left:auto;font-size:12.5px;color:#3d76c4;text-decoration:none;
border:1px solid #c9dcf2;border-radius:7px;padding:4px 12px;background:#f4f9fe}}
.wrap{{max-width:860px;margin:18px auto 40px;padding:0 16px}}
.seg{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px 16px;margin-bottom:16px;
box-shadow:0 1px 3px rgba(15,23,42,.05);transition:.15s}}
.seg:hover{{border-color:#b9c4d6;box-shadow:0 2px 8px rgba(15,23,42,.08)}}
.seg.is-pre{{border-left:4px solid #d97706}}
.seg.is-man{{border-left:4px solid #2e9e5b;opacity:.88}}
.sh{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:#64748b;margin-bottom:8px}}
.idx{{font-weight:700;color:#fff;background:#3d76c4;border-radius:999px;min-width:34px;height:22px;
display:inline-flex;align-items:center;justify-content:center;font-size:11.5px;padding:0 6px}}
.eff{{border-radius:6px;padding:1px 9px;font-size:11.5px}}
.eff.manual{{background:#dcfce7;color:#166534}} .eff.pre{{background:#fff7ed;color:#c2410c;border:1px dashed #fdba74}}
.mt{{color:#94a3b8}}
button.lb{{font-size:12px;padding:3px 11px;border-radius:6px;border:1.5px solid #cbd5e1;
background:#fff;cursor:pointer;margin:2px 6px 2px 0;color:var(--c,#475569);transition:.12s}}
button.lb:hover{{border-color:#3d76c4;transform:translateY(-1px)}}
button.lb.on{{background:#3d76c4;color:#fff;border-color:#3d76c4}}
.turn{{margin-top:8px}}
.uq{{color:#0f172a;font-size:13.5px;line-height:1.7}}
.uq b{{color:#3d76c4;margin-right:8px}}
.ai{{background:#f8fafc;border-left:3px solid #3d76c4;border-radius:8px;
padding:9px 13px;margin:8px 0 4px 16px;color:#334155;font-size:13px;white-space:pre-wrap;line-height:1.7}}
.act{{display:inline-block;background:#fef3c7;color:#92400e;border-radius:6px;
padding:2px 10px;font-size:12px;margin:4px 0 4px 16px}}
img{{max-width:400px;border-radius:10px;border:1px solid #e2e8f0;margin:6px 0;display:block}}
.imgfail{{display:inline-block;font-size:12px;color:#64748b;background:#f1f5f9;border:1px dashed #cbd5e1;
border-radius:6px;padding:3px 10px;cursor:pointer}}
.imgfail:hover{{color:#3d76c4;border-color:#3d76c4}}
</style></head><body>
<div class="top"><h2>走查 · {esc(names.get(layer, layer))}</h2>
<span class="n">{len(items)} 段 · {esc(env)} 数据 · 左边条：橙=AI预标待复核，绿=已人工 · 点标签即改判</span>
<a class="bk" href="/" target="_self">← 工作台</a></div>
<div class="wrap">
{''.join(parts) or '<p>（该层无段）</p>'}
</div>
<script>
{_img_js()}
async function lab(btn, cid, astart, lb){{
  btn.disabled = true;
  try{{
    const r = await (await fetch('/api/label_seg', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{env:'{env}', cid, astart, label: lb}})}})).json();
    if(!r.ok) throw new Error(r.detail || '失败');
    const seg = btn.closest('.seg');
    seg.querySelectorAll('button.lb').forEach(b=>b.classList.toggle('on', b===btn));
    const be = seg.querySelector('.eff');
    be.className = 'eff manual'; be.textContent = lb + '·人工';
  }}catch(e){{ alert('改判失败：' + e.message); btn.disabled = false; }}
}}
</script></body></html>"""
    return HTMLResponse(html)


def _mtime_str(p: str) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
    except OSError:
        return ""


def _manual_path(env: str) -> str:
    """人工切分/标注文件：随数据集放 export_dar/{env}/（0910-6 迁出 Downloads，
    浏览器下载列表清理会误删；两环境目录隔离，文件同名不混用）。"""
    return os.path.join(DATA_ROOT, env, "manual_segmentation.json")


class SaveManualReq(BaseModel):
    env: str = "test"
    data: dict


@app.post("/api/save_manual")
def save_manual(req: SaveManualReq):
    """标注工具「保存到工作台」：store 直写 export_dar/{env}/manual_segmentation.json，
    并后台按人工边界重算 L1（l1r 本地无 LLM，十几秒）——刷新指标即见。"""
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    d = req.data
    if not isinstance(d, dict) or not isinstance(d.get("bounds"), dict) \
            or not isinstance(d.get("labels"), dict):
        raise HTTPException(400, "数据缺 bounds/labels")
    p = _manual_path(req.env)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    busy = bool(_run_state["proc"] and _run_state["proc"].poll() is None)
    split = os.path.join(DATA_ROOT, req.env, "processed", "conversations_split.jsonl")
    if not busy and os.path.exists(split):
        subprocess.Popen([sys.executable, os.path.join(HERE, "dar_weekly.py"),
                          "--env", req.env, "l1r"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=PROJ, env=_child_env())
        return {"ok": True, "path": p, "rerun": True}
    return {"ok": True, "path": p, "rerun": False}


@app.get("/api/progress")
def progress(env: str = "prod"):
    """向导各步完成状态：从产物文件 mtime 推断（页面刷新不丢）。"""
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    proc = os.path.join(DATA_ROOT, env, "processed")

    def latest(pat: str) -> str:
        files = sorted(glob.glob(os.path.join(proc, pat)))
        return _mtime_str(files[-1]) if files else ""

    return {"env": env, "steps": {
        "export": _mtime_str(os.path.join(proc, "conversations_split.jsonl")),
        "l1": latest("direct_answer_summary_*.json"),
        "tool0": latest("segmentation_tool.html"),
        "l3": latest("segmentation_tool.html") or latest("l3_judge_all_*.json"),
        "label": _mtime_str(_manual_path(env)),
        "report": latest("weekly_*.json"),
    }}


@app.get("/label_tool")
def label_tool(env: str = "test"):
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    p = os.path.join(DATA_ROOT, env, "processed", "segmentation_tool.html")
    if not os.path.exists(p):
        raise HTTPException(404, "标注工具未生成（先运行「生成标注工具」步骤）")
    # no-cache：0909 实锤浏览器缓存旧页面——导数后重开工具还看到前天的会话与旧预标
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


# ── 工单沉淀审核（review.html 自包含页 + CSV 收发）──────────────
_DIR_RE = re.compile(r"export_\d{8}_\d{6}$")


def _latest_sink_dir() -> str:
    dirs = sorted(d for d in os.listdir(SINK_ROOT) if _DIR_RE.fullmatch(d)) \
        if os.path.isdir(SINK_ROOT) else []
    return dirs[-1] if dirs else ""


class SinkSaveReq(BaseModel):
    dir: str = ""
    csv: str = ""


@app.post("/api/sink_save")
def sink_save(req: SinkSaveReq):
    """审核页「保存标注结果」：CSV 直写导出目录 review.csv（apply 按钮读它）。"""
    if not _DIR_RE.fullmatch(req.dir or ""):
        raise HTTPException(400, "dir 应为 export_YYYYMMDD_HHMMSS")
    csv_path = os.path.join(SINK_ROOT, req.dir, "review.csv")
    if not os.path.isdir(os.path.dirname(csv_path)):
        raise HTTPException(404, f"导出目录不存在：{req.dir}")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(req.csv)
    from collections import Counter as _Ctr
    rows = [r.split(",") for r in req.csv.splitlines() if r.strip()]
    verdicts = _Ctr((r[3].strip().strip('"') if len(r) > 3 else "") for r in rows[1:])
    judged = sum(verdicts.get(v, 0) for v in ("approved", "rejected", "test"))
    return {"ok": True, "judged": judged, "total": max(len(rows) - 1, 0),
            "path": csv_path}


@app.get("/api/sink_csv")
def sink_csv(dir: str = ""):
    """审核页打开时加载已判基线：CSV 判定解析成 JSON 行（服务端解析，页面
    不用手写 CSV 引号解析）。CSV 是保存后的真相——重拉合并的判定靠它回显。"""
    d = dir or _latest_sink_dir()
    if not d or not _DIR_RE.fullmatch(d):
        raise HTTPException(400, "dir 应为 export_YYYYMMDD_HHMMSS")
    p = os.path.join(SINK_ROOT, d, "review.csv")
    if not os.path.isfile(p):
        raise HTTPException(404, "review.csv 不存在")
    import csv as _csv
    rows = []
    with open(p, encoding="utf-8-sig", newline="") as fh:
        for r in _csv.DictReader(fh):
            rows.append({k: (r.get(k) or "").strip() for k in
                         ("point_id", "task_id", "title", "verdict", "reason", "note")})
    return {"rows": rows}


@app.get("/api/sink_status")
def sink_status():
    """最新导出目录 + review.csv 判定进度（无导出则 found=false）。"""
    d = _latest_sink_dir()
    if not d:
        return {"found": False}
    csv_path = os.path.join(SINK_ROOT, d, "review.csv")
    out = {"found": True, "dir": d,
           "mtime": _mtime_str(os.path.join(SINK_ROOT, d, "review.html")),
           "total": 0, "judged": 0, "approved": 0, "rejected": 0, "test": 0}
    if os.path.isfile(csv_path):
        import csv as _csv
        from collections import Counter as _Ctr
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            rows = list(_csv.DictReader(fh))
        c = _Ctr((r.get("verdict") or "").strip().lower() for r in rows)
        out.update(total=len(rows), judged=sum(c.get(v, 0) for v in ("approved", "rejected", "test")),
                   approved=c.get("approved", 0), rejected=c.get("rejected", 0), test=c.get("test", 0))
    return out


@app.get("/sink_review")
def sink_review(dir: str = ""):
    """服务最新（或指定）导出目录的审核页；dir 校验防路径穿越。"""
    d = dir or _latest_sink_dir()
    if not d or not _DIR_RE.fullmatch(d):
        raise HTTPException(400, "dir 应为 export_YYYYMMDD_HHMMSS")
    p = os.path.join(SINK_ROOT, d, "review.html")
    if not os.path.isfile(p):
        raise HTTPException(404, "审核页不存在（先「拉取待审」）")
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "dar_studio.html"))


if __name__ == "__main__":
    print(f"AI 质量工作台 → http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
