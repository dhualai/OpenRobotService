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
from typing import List
import glob
import hashlib
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
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)  # 让 from ai.config import _KB_DIR 可解析（知识库页签用）
if HERE not in sys.path:
    sys.path.insert(0, HERE)  # 同目录共享口径模块（dar_segs 段首展开）

import dar_segs  # noqa: E402  漏斗段口径：bounds 优先 + 老窗口续聊追加（0920）

# seg_to_ticket 的 DB 路径（app.core.db）连接串：独立 DB 隧道 13306 → 测试库。
# setdefault 不覆盖外部环境变量；DB 不可达时 /api/seg_to_ticket 自动 fallback csv。
os.environ.setdefault("DATABASE_URL",
                      "mysql+pymysql://root:123456@127.0.0.1:13306/helpdesk_test")

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
_tunnel = {"proc": None, "ready": False, "db_proc": None}


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
    """主隧道：本地 19640/19641 → 服务器 9400/9401（测试环境后端/AI）。
    DB 隧道独立进程：本地 13306 → 服务器 3306（0916 拆分——原先三条转发同
    进程 + ExitOnForwardFailure，本地 3306 被任何进程占用即整条隧道秒退，
    测试环境登录一起挂）。两条隧道互不影响，已有实例直接复用。"""
    if not _tunnel["ready"] and not _port_open(19640):
        if not _tunnel["proc"] or _tunnel["proc"].poll() is not None:
            _tunnel["proc"] = subprocess.Popen(
                ["ssh", "-p", SSH_PORT, "-N",
                 "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
                 "-o", "ServerAliveInterval=30",
                 "-L", "19640:127.0.0.1:9400", "-L", "19641:127.0.0.1:9401",
                 SSH_HOST],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):  # 最多等 10s
            if _port_open(19640):
                _tunnel["ready"] = True
                print(f"ssh 主隧道就绪：19640→9400 / 19641→9401（{SSH_HOST}:{SSH_PORT}）")
                break
            time.sleep(0.25)
    else:
        _tunnel["ready"] = True

    # DB 隧道（13306→3306）：独立进程独立守护，bind 失败只影响 DB 查询
    # （seg_to_ticket 已有 csv fallback），绝不拖累测试环境登录。
    if _tunnel["db_proc"] is None or _tunnel["db_proc"].poll() is not None:
        if not _port_open(13306):
            _tunnel["db_proc"] = subprocess.Popen(
                ["ssh", "-p", SSH_PORT, "-N",
                 "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
                 "-o", "ServerAliveInterval=30",
                 "-L", "13306:127.0.0.1:3306",
                 SSH_HOST],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("ssh DB 隧道启动：13306→3306（独立进程，挂了不影响主隧道）")
    return _tunnel["ready"]


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


class BatchProbeReq(BaseModel):
    queries: List[str]
    qdrant: str = "local"


@app.post("/api/probe_batch")
def probe_batch(req: BatchProbeReq):
    """批量探针：每条 subprocess 跑，按命中 chunk 的 route 统计覆盖度。
    用途：未直答问题清单 -> 批量探测 -> 模块覆盖度看板 -> 驱动症状化优先级。"""
    if req.qdrant not in ("test", "prod", "local"):
        raise HTTPException(400, "qdrant 取值 test|prod|local")
    rows = []
    for q in req.queries:
        q = q.strip()
        if not q:
            continue
        result, diag = _run_probe_sync(q, req.qdrant)
        if not result:
            rows.append({"q": q, "ok": False, "diag": (diag or "")[:200], "routes": [], "top_title": "", "top_route": ""})
            continue
        chunks = result.get("chunks", [])
        routes = []
        for c in chunks:
            r = (c.get("route") or "").strip()
            if r and r not in routes:
                routes.append(r)
        top = chunks[0] if chunks else {}
        sub_domains = []
        for c in chunks:
            sd = (c.get("sub_domain") or c.get("route") or "").strip()
            if sd and sd not in sub_domains:
                sub_domains.append(sd)
        rows.append({
            "q": q, "ok": True, "n_chunks": len(chunks),
            "top_title": (top.get("title") or "")[:60],
            "top_route": top.get("route", ""),
            "routes": routes[:6],
            "sub_domains": sub_domains[:6],
        })
    # 覆盖度看板用 sub_domain 聚合（模块维度，不受 title 漂移影响）
    sd_hits = {}
    for r in rows:
        for sd in r.get("sub_domains", []):
            sd_hits[sd] = sd_hits.get(sd, 0) + 1
    return {"rows": rows, "route_hits": sd_hits, "total": len(rows),
            "note": "route_hits 已按 sub_domain 聚合（模块维度）"}


def _run_probe_sync(q: str, qdrant: str):
    """单条探针同步跑（subprocess 包装 dar_probe.py，180s 超时，避开冷启动 30s 吞域）。"""
    import subprocess as _sp, json as _json
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        r = _sp.run(
            [sys.executable, os.path.join(HERE, "dar_probe.py"),
             "--q", q, "--qdrant", qdrant],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, cwd=PROJ, env=env)
    except _sp.TimeoutExpired:
        return None, "timeout 180s"
    out = (r.stdout or "").strip()
    try:
        return _json.loads(out[out.index("{"):]), (r.stderr or "")[:200]
    except Exception:
        return None, out[-200:] or (r.stderr or "")[-200:]


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
    "supplement_queue.json",
    "processed/segmentation_tool.html", "processed/segmentation_tool_bounds.html",
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
        frozen_len_m = {str(k): v for k, v in (man.get("frozen_len") or {}).items()
                        if isinstance(v, int)}
        # L3 预标段首（口径与漏斗 _seg_rows 一致：末段有预标也算「已判定」）
        pre_cids_m = {}
        for r in json.load(open(fj[-1], encoding="utf-8")):
            pre_cids_m.setdefault(str(r.get("cid")), set()).add(r.get("astart"))
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
                    # 段首统一展开（0920）：续聊追加与漏斗/标注工具同口径
                    segs = dar_segs.effective_starts(
                        c["rounds"], cid, bounds_man, labs_man,
                        pre_starts=pre_cids_m.get(cid) or set(),
                        frozen_len=frozen_len_m, n=len(cl))
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
    # L3 预标覆盖的段首（按会话归组）——段首展开的「末段已判定」判定锚之一
    pre_cids = {}
    for c_, a_ in pre_by:
        pre_cids.setdefault(c_, set()).add(a_)
    labs_man = {}
    bounds_man = {}  # 人工切分边界（漏斗与 dar_l3/L2 指标/标注工具统一口径：bounds 优先）
    frozen_len = {}  # 标注时的回合总数（save_manual 注入）——续聊追加的主规则锚点
    mp = _manual_path(env)
    if os.path.exists(mp):
        man = json.load(open(mp, encoding="utf-8"))
        legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
        for cid, lm in (man.get("labels") or {}).items():
            labs_man[str(cid)] = {int(k): legacy.get(v, v) for k, v in lm.items()
                                  if str(k).isdigit()}
        bounds_man = {str(k): v for k, v in (man.get("bounds") or {}).items()}
        frozen_len = {str(k): v for k, v in (man.get("frozen_len") or {}).items()
                      if isinstance(v, int)}
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
                # 段首统一展开（0920）：bounds 优先 + 末段已判定时续聊追加
                # （老窗口新回合不再折叠进旧判定段，落到未标注待走查）
                starts = dar_segs.effective_starts(
                    c["rounds"], cid_s, bounds_man, labs_man,
                    pre_starts=pre_cids.get(cid_s) or set(),
                    frozen_len=frozen_len, n=len(cls))
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
                # 猜你想问=元筛选（0920 修正口径）：仅段内**全部**咨询回合都命中
                # 推荐池才判 suggested——多轮段碰巧含一条推荐问题不再整段旁支
                # （用户拍板：混合段不过滤，真实提问跟着陪葬没道理）
                seg_qs = [(rr.get("q") or "").strip()
                          for rr in (c.get("rounds") or [])[a0:a1] if rr.get("q")]
                # 0920 定稿：层判定用 ANY——段内只要有一轮命中推荐池即归 suggested
                # 层（混合段不进直答层，用户实锤「急停」段混进直答正确）；seg_any_sug
                # 同时作直答率剔除标记（0916 口径：推荐点击不进直答统计）。
                # 工具标注列表不受此影响（混合段照常列为待标注，见
                # build_segmentation_tool._has_pending_seg 的 ALL 语义）
                seg_any_sug = any(q in suggested_pool for q in seg_qs)
                # 0915 用户硬要求：SKIP_USER_IDS 静默归到「测试人员」层（不显示排除徽章）
                is_skip = str(c.get("user_id") or "") in SKIP_USER_IDS
                if c.get("is_tester") or is_skip:
                    layer = "tester"
                elif seg_any_sug:
                    layer = "suggested"
                elif eff == "寒暄":
                    layer = "chitchat"
                elif man and eff == "猜你想问":
                    # 人工判推荐命中（自动池匹配漏掉的措辞变体）→ 归 suggested 层
                    layer = "suggested"
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
                # 段内须有「提问且 AI 有回答」的回合（0920：AI 未回答/回答全空的
                # 服务异常段不可标注，不进未标注——dar_l3 分母同口径）
                seg_answerable = any(
                    cls[j].get("q") and any(a.strip() for a in (c["rounds"][j].get("a") or []))
                    for j in range(a0, min(a1, len(cls), len(c["rounds"] or []))))
                # 0915 用户反馈：fresh import + 无判定（无人工 + 无 AI 预标）= 不进漏斗
                # 等用户跑 l3 / 人工标注后再进入——避免空段被错放任何"已判定"层
                # tester/suggested/寒暄 是元筛选层（不依赖 eff），保留
                # SKIP 用户也保留在 tester 层
                if (not eff and layer == "undetermined" and seg_answerable
                    and not c.get("is_tester") and not seg_any_sug
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
                    # 段内含推荐池命中轮（混合段）：直答率统计时剔除，标注照常
                    "any_sug": seg_any_sug,
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


def _week_stats(rows: list, unprocessed: list | None = None) -> dict:
    """本周（自然周，周一起）直答率：与漏斗同源同口径，人工标注后即刷。

    大字=周直答率（本周 qa 四层中直答正确占比）；小字=周新增段/已判定/直答数。
    tester/suggested/chitchat/ticket 层的段计入"周新增"，不计入直答率分母。
    0920：走查进度分母改「可标注段」=qa 四层已判定+未标注（本周）——tester/寒暄/
    猜你想问/提单层本就无需人工标签，按全量新增算分母会让进度永不到 100
    （用户实锤：全标完仍显示 36%）。"""
    from datetime import datetime, timedelta
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    n_new = n_ans = n_unans = n_uncov = n_undet = n_sug_mix = 0
    for r in rows:
        if (r.get("at") or "")[:10] < monday:
            continue
        n_new += 1
        layer = r.get("layer")
        # 0916 口径：含推荐命中轮的混合段不进直答率分子分母（推荐点击不算
        # 真实困惑），但照常计入新增/已判定/走查进度——段仍需人工标注
        if r.get("any_sug"):
            if layer in ("answered", "unanswered", "uncovered", "undetermined"):
                n_sug_mix += 1
            continue
        if layer == "answered":
            n_ans += 1
        elif layer == "unanswered":
            n_unans += 1
        elif layer == "uncovered":
            n_uncov += 1
        elif layer == "undetermined":
            n_undet += 1
    judged = n_ans + n_unans + n_uncov + n_undet
    n_pending = sum(1 for i in (unprocessed or [])
                    if (i.get("at") or "")[:10] >= monday)
    labelable = judged + n_pending + n_sug_mix
    return {"monday": monday, "new_total": n_new, "judged": judged,
            "answered": n_ans, "unanswered": n_unans, "uncovered": n_uncov,
            "undetermined": n_undet, "pending": n_pending, "sug_mix": n_sug_mix,
            "labelable": labelable,
            "rate": round(n_ans / judged * 100, 1) if judged else None,
            "progress": round(judged / labelable * 100, 1) if labelable else None}


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
            "week": _week_stats(rows, meta.get("unprocessed", [])),
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


_LABELS_VALID = ("直答正确", "未直答", "未覆盖", "直接提单", "建议转单", "寒暄", "猜你想问")


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


_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico")


def _img_html(f: dict, img_base: str) -> str:
    """附件渲染（0920 两修）：
    ① 去掉 loading="lazy"——本页嵌入方式下懒加载永远不触发，图片停留在
       alt 文本状态（用户实锤「image.webp 看不了」，eager 实测秒开）；
    ② 非图片扩展（zip/log/txt…）渲染为下载链接——此前塞进 <img> 永远裂图。
    大图（>3MB）/gif 仍占位点击加载。"""
    path = img_base + f.get("object_path", "")
    name = f.get("filename") or path.rsplit("/", 1)[-1]
    size = int(f.get("size") or 0)
    lower = path.lower()
    if not lower.endswith(_IMG_EXTS):
        kb = f"{size / 1024:.0f}KB" if size < 1048576 else f"{size / 1048576:.1f}MB"
        return (f'<a href="{_esc_attr(path)}" download="{_esc_attr(name)}" '
                f'style="display:inline-block;margin:4px 0;font-size:12.5px;color:#3d76c4">'
                f'📎 {_esc_attr(name)}（{kb}）· 点击下载</a>')
    alt = _esc_attr(name)
    if size > 3 * 1024 * 1024 or lower.endswith(".gif"):
        mb = f"{size / 1048576:.1f}"
        return (f'<span class="imgfail" onclick="loadBig(this)" '
                f'data-path="{_esc_attr(path)}" data-alt="{alt}">🖼️ 大图 {mb}MB · 点击加载</span>')
    return f'<img src="{_esc_attr(path)}" alt="{alt}" onerror="imgFail(this)">'


_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _md_img_html(text: str, site_base: str) -> str:
    """AI 回答 markdown 图转 <img>（0920）：纯文本渲染时 ![](...) 只会显示原始字符。

    URL 解析：http(s) 原样；站内绝对路径（/api/...）补站点前缀
    （prod=https://usp.ep-zl.com/p、test=http://125.122.97.107/t）；
    其余按 KB 相对引用兜底。加载失败显示占位+原 URL（404 可见可查）。
    非图片部分照常 HTML 转义。"""
    out, pos = [], 0
    for m in _MD_IMG_RE.finditer(text or ""):
        out.append(_esc_attr(text[pos:m.start()]))
        alt, url = (m.group(1) or "").strip(), m.group(2).strip()
        if url.startswith("http"):
            full = url
        elif url.startswith("/"):
            full = site_base + url
        else:
            full = f"{site_base}/api/ai/media/kb/{url}"
        out.append(
            f'<span class="mdimg"><img loading="lazy" src="{_esc_attr(full)}" alt="{_esc_attr(alt)}" '
            f'style="max-width:100%;border-radius:8px;margin:4px 0;display:block" '
            f'onerror="this.style.display=\'none\';this.parentNode.querySelector(\'.mdalt\').style.display=\'block\'">'
            f'<span class="mdalt" style="display:none;font-size:12px;color:#d9534f">'
            f'🖼️ 图片未能加载（{_esc_attr(url[:90])}）</span></span>')
        pos = m.end()
    out.append(_esc_attr(text[pos:]))
    return "".join(out)


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
    site_base = ("https://usp.ep-zl.com/p" if env == "prod"
                 else "http://125.122.97.107/t")
    img_base = site_base + "/api/call/files/"

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
            f'<div class="ai">{_md_img_html(s.get("text", ""), site_base)}</div>'
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
    site_base = ("https://usp.ep-zl.com/p" if env == "prod"
                 else "http://125.122.97.107/t")
    img_base = site_base + "/api/call/files/"
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
                f'<div class="ai">{_md_img_html(s.get("text", ""), site_base)}</div>'
                for s in (rr.get("a_seg") or [])
                if s.get("action") != "ticket_draft" and (s.get("text") or "").strip())
            turns.append(
                f'<div class="turn"><div class="uq"><b>用户</b> · {esc(rr.get("at", ""))[:19]}'
                f'<div>{esc(rr.get("q", ""))}</div>{imgs}</div>{acts}{ans}</div>')
        lbls = [("直答正确", "#2e9e5b"), ("未直答", "#d9534f"), ("未覆盖", "#d9534f"),
                ("直接提单", "#d97706"), ("建议转单", "#d97706"), ("寒暄", "#98a2b3"),
                ("猜你想问", "#b45309")]
        # 标签只属于真实咨询层+猜你想问层（0916 定调真实层走查改判；0920 补
        # suggested——自动归层误判时就地改判，含「猜你想问」人工标签）。
        # qa 的三个子层（answered/unanswered/uncovered）同样是走查主战场，
        # 必须出按钮——此前只有 qa 聚合层有，子层页面一个按钮都没有（0920 实锤）
        btns = ("".join(
            f'<button class="lb{" on" if r["eff"] == lb and r["src"] == "manual" else ""}" '
            f'style="{"" if r["eff"] == lb and r["src"] == "manual" else f"--c:{col};"}" '
            f'onclick="lab(this,{r["cid"]},{r["astart"]},\'{lb}\')">{lb}</button>'
            for lb, col in lbls) if layer not in ("tester", "ticket") else "")
        tks = [str(t) for t in (r.get("ticket_ids") or []) + (r.get("task_ids") or [])]
        tk_span = ""
        if tks:
            tk_span = '<span class="mt">🎫 ' + " ".join("#" + t for t in dict.fromkeys(tks)) + "</span>"
        nf_span = ""
        if r.get("n_files"):
            nf_span = '<span class="mt" style="color:#3d76c4">📷 ' + str(r["n_files"]) + "</span>"
        # 0920：含推荐命中轮的混合段打徽标——直答率已剔除，走查时一眼可辨
        sug_span = ('<span class="mt" style="color:#b45309">◈ 含推荐</span>'
                    if r.get("any_sug") else "")
        parts.append(
            f'<div class="seg {"is-man" if r["src"] == "manual" else "is-pre"}" id="s{r["cid"]}_{r["astart"]}">'
            f'<div class="sh"><span class="idx">#{idx + 1}</span>'
            f'<span class="eff {r["src"]}">{esc(r["eff"] or "未判定")}·{"人工" if r["src"] == "manual" else "AI预标"}</span>'
            f'<span class="mt">{esc(r["type"])}</span><span class="mt">{esc(r["user"])}</span>'
            f'<span class="mt">{esc((r["at"] or "")[:16])}</span>'
            f'<span class="mt">会话{r["cid"]}</span>'
            f'{tk_span}{nf_span}{sug_span}'
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
    # 冻结标注时的回合总数（0920）：续聊追加主规则的锚点——rounds 超过
    # frozen_len 的部分视为标注后新增，段首展开时从冻结点切新段。
    # 服务端统一注入，标注工具零改动；读不到 split 时保留旧值（保守）。
    old_frozen = {}
    if os.path.exists(p):
        try:
            old_frozen = json.load(open(p, encoding="utf-8")).get("frozen_len") or {}
        except Exception:
            old_frozen = {}
    frozen = dict(old_frozen)
    split = os.path.join(DATA_ROOT, req.env, "processed", "conversations_split.jsonl")
    if os.path.exists(split):
        n_rounds = {}
        with open(split, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    c = json.loads(line)
                    n_rounds[str(c["conversation_id"])] = len(c.get("rounds") or [])
        for cid in (d.get("bounds") or {}):
            if cid in n_rounds:
                frozen[str(cid)] = n_rounds[str(cid)]
    d["frozen_len"] = frozen
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    busy = bool(_run_state["proc"] and _run_state["proc"].poll() is None)
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
        "tool0": latest("segmentation_tool_bounds.html") or latest("segmentation_tool.html"),
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


# ── 知识补充（页签5：漏斗未覆盖 → AI 整理 → 人工补答案 → 一键入库）──
# 链路：漏斗 uncovered 层「补知识」→ 段对话进队列（supplement_queue.json）→
# LLM 整理（问题规范化重写 + 探针验覆盖 + 类型归类；答案纯人工写，AI 不碰
# 内容——未覆盖=库无料，AI 起草即无根之木，用户定调）→ 编辑器写答案（图文，
# 图片统一落 kb/team/chat_qa/media/）→ 保存成 md（frontmatter 溯源；正文
# H1+答案 ≤2000 字走 _split_generic 短文档路径整卡一 chunk，图文同在）→
# 一键入库（ingest_all --domain team 增量，只有新文件会嵌入）→ 探针 local
# 复跑验「已补齐」。入库只进本地知识库；生产同步走既有入库流程（UI 明示）。
# sub_domain 无需注册：KBDomainIngester 按父目录自动推断 → "chat_qa"。


def _supp_qfile(env: str) -> str:
    return os.path.join(DATA_ROOT, env, "supplement_queue.json")


def _supp_kb_dir():
    from ai.config import _KB_DIR
    return _KB_DIR / "team" / "chat_qa"


def _supp_media_dir():
    return _supp_kb_dir() / "media"


_supp_lock = threading.Lock()
_supp_tasks: dict = {}      # f"{env}:{item_id}" -> "analyzing" | "done" | str(error)
_sup_ing: dict = {"busy": False, "logs": [], "rc": None, "verify": ""}


def _supp_load(env: str) -> dict:
    p = _supp_qfile(env)
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("items"), list):
                return d
        except Exception:
            pass
    return {"items": []}


def _supp_save(env: str, d: dict):
    os.makedirs(os.path.dirname(_supp_qfile(env)), exist_ok=True)
    with open(_supp_qfile(env), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)


def _supp_item(d: dict, item_id: str):
    return next((it for it in d["items"] if it.get("id") == item_id), None)


def _load_conv(env: str, cid) -> dict | None:
    """split 里找会话（整会话原始数据，段切片由调用方做）。仅后台任务/线程池
    里调（jsonl 几十 MB，同步读别放事件循环线程）。"""
    split_p = os.path.join(DATA_ROOT, env, "processed", "conversations_split.jsonl")
    if not os.path.exists(split_p):
        return None
    with open(split_p, encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and str(json.loads(line).get("conversation_id")) == str(cid):
                return json.loads(line)
    return None


def _load_conv_map(env: str) -> dict:
    """一次全读 split → {cid_str: conv}（批量补知识用，避免逐条全文件扫）。"""
    split_p = os.path.join(DATA_ROOT, env, "processed", "conversations_split.jsonl")
    out = {}
    if os.path.exists(split_p):
        with open(split_p, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    c = json.loads(line)
                    out[str(c.get("conversation_id"))] = c
    return out


_bg_tasks: set = set()  # 持有后台整理 task 引用（不持有会被 GC 半路取消）


class SuppAddReq(BaseModel):
    env: str = "prod"
    segs: list[dict]  # [{cid, astart, aend}]


@app.post("/api/supplement/add")
async def supp_add(req: SuppAddReq):
    """漏斗行「补知识」/层头「批量加入」：段对话进队列并触发后台 AI 整理。"""
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    if not req.segs:
        raise HTTPException(400, "segs 为空")
    added, dup = [], 0
    conv_map = await asyncio.to_thread(_load_conv_map, req.env)  # 一次全读，批量不逐条扫
    with _supp_lock:
        d = _supp_load(req.env)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for s in req.segs:
            cid, a0, a1 = s.get("cid"), s.get("astart"), s.get("aend")
            if not cid or a0 is None or a1 is None or a1 <= a0:
                continue
            key = (str(cid), int(a0))
            if any((str(it.get("cid")), int(it.get("astart") or 0)) == key
                   for it in d["items"]):
                dup += 1
                continue
            conv = conv_map.get(str(cid))
            rounds = (conv or {}).get("rounds") or []
            seg_rounds = rounds[a0:a1]
            q_orig = next((rr.get("q") or "" for rr in seg_rounds if (rr.get("q") or "").strip()), "")
            item = {
                "id": f"s{uuid.uuid4().hex[:10]}",
                "cid": cid, "astart": a0, "aend": a1,
                "question_orig": q_orig[:300],
                "question_norm": "", "qtype": "",
                "status": "added",        # added→ready(整理完)→saved(已存md)→ingested(已入库)
                "task": "analyzing",      # 实时任务态：analyzing|done|错误信息
                "probe": None,            # {n_chunks, hits:[{title,route}]}（prod 源）
                "verify": None,           # 入库后 local 源复跑 {ok, n_chunks, top_title}
                "answer_md": "", "media": [],
                "file": "", "note": "",
                "created_at": now, "updated_at": now,
                "ingested_at": "", "error": "",
            }
            d["items"].insert(0, item)
            added.append(item["id"])
        _supp_save(req.env, d)
    for iid in added:
        t = asyncio.create_task(_supp_analyze(req.env, iid))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    return {"ok": True, "added": len(added), "dup": dup}


async def _supp_analyze(env: str, item_id: str):
    """后台 AI 整理：问题规范化重写（症状化语言）+ 探针验覆盖 + 类型归类。

    只做整理侧三件事，不生成答案内容（用户定调：未覆盖=库无料，AI 起草
    即无根之木）。探针打 prod 只读——验证的是线上知识库真实覆盖状态。"""
    key = f"{env}:{item_id}"
    _supp_tasks[key] = "analyzing"
    try:
        with _supp_lock:
            d = _supp_load(env)
            it = _supp_item(d, item_id)
            if not it:
                _supp_tasks[key] = "条目不存在"
                return
            cid, a0, a1 = it["cid"], it["astart"], it["aend"]
        conv = await asyncio.to_thread(_load_conv, env, cid)
        if not conv:
            raise RuntimeError(f"会话 {cid} 不在 conversations_split.jsonl")
        rounds = (conv.get("rounds") or [])[a0:a1]
        lines = []
        for rr in rounds:
            q = (rr.get("q") or "").strip()
            if q:
                lines.append(f"用户：{q[:300]}")
            n_img = len(rr.get("files") or [])
            if n_img:
                lines.append(f"[用户发送了 {n_img} 张图片]")
            a_all = " ".join((s.get("text") or "") for s in (rr.get("a_seg") or [])
                             if s.get("action") != "ticket_draft").strip()
            if a_all:
                lines.append(f"AI：{a_all[:800]}")
        dialog = "\n".join(lines)[:4000]
        prompt = (
            "你是知识库管理助手。下面是一段用户与 AI 客服的对话，用户的问题在知识库"
            "中没有覆盖（AI 当时无法直接回答）。你的任务：\n"
            "1. 提炼用户的真实询问意图，重写为一个规范化的知识条目标题（question_norm）："
            "用症状化、具体的表述；保留对话中的设备型号、功能名、错误码、界面名称等关键"
            "实体；把指代词还原成实际对象；不超过 40 个字，以疑问句式收尾。\n"
            "2. 判断问题类型（qtype），只能从这些取值里选：产品、平台操作、车端、算法、"
            "业务流程、其他。\n"
            "只输出 JSON：{\"question_norm\": \"...\", \"qtype\": \"...\"}\n\n"
            f"对话记录：\n{dialog}")
        from dar_llm import get_dar_client
        client = await get_dar_client()
        raw = await client.chat([{"role": "user", "content": prompt}],
                                max_tokens=300, temperature=0.1)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise RuntimeError(f"LLM 输出非 JSON：{raw[:120]}")
        obj = json.loads(m.group(0))
        q_norm = str(obj.get("question_norm") or "").strip()[:80]
        qtype = str(obj.get("qtype") or "其他").strip()
        if qtype not in ("产品", "平台操作", "车端", "算法", "业务流程", "其他"):
            qtype = "其他"
        if not q_norm:
            raise RuntimeError("LLM 未给出重写问题")
        # 探针 prod：命中块数只是辅助信号（原文探针也可能碰上弱相关块），
        # ≥3 块给「疑似已覆盖」徽标，最终由人判断
        probe_res, _ = await asyncio.to_thread(_run_probe_sync, q_norm, "prod")
        chunks = (probe_res or {}).get("chunks") or []
        probe = {"n_chunks": len(chunks), "suspicious": len(chunks) >= 3,
                 "hits": [{"title": (c.get("title") or "")[:60],
                           "route": c.get("route", "")} for c in chunks[:4]]}
        with _supp_lock:
            d = _supp_load(env)
            it = _supp_item(d, item_id)
            if it:
                it.update(question_norm=q_norm, qtype=qtype,
                          status="ready" if it["status"] == "added" else it["status"],
                          probe=probe, task="done", error="",
                          updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
                _supp_save(env, d)
        _supp_tasks[key] = "done"
    except Exception as e:
        _supp_tasks[key] = f"{type(e).__name__}: {e}"
        with _supp_lock:
            d = _supp_load(env)
            it = _supp_item(d, item_id)
            if it:
                it["task"] = f"{type(e).__name__}: {e}"
                it["status"] = "ready" if it["status"] == "added" else it["status"]
                _supp_save(env, d)


@app.get("/api/supplement/list")
def supp_list(env: str = "prod"):
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    with _supp_lock:
        items = _supp_load(env)["items"]
    return {"items": [{k: it.get(k) for k in
                       ("id", "cid", "astart", "aend", "question_orig", "question_norm",
                        "qtype", "status", "task", "probe", "verify", "file",
                        "created_at", "updated_at", "ingested_at", "error")}
                      for it in items]}


@app.post("/api/supplement/analyze")
async def supp_analyze(req: SuppAddReq):  # 复用请求模型：env + segs 里带 id
    """重跑 AI 整理（重写+探针+归类）。前端传 {env, segs:[{id}]}。"""
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    ids = [s.get("id") for s in req.segs if s.get("id")]
    if not ids:
        raise HTTPException(400, "缺 id")
    for iid in ids:
        with _supp_lock:
            d = _supp_load(req.env)
            it = _supp_item(d, iid)
            if it and it.get("task") != "analyzing":
                it["task"] = "analyzing"
                it["error"] = ""
                _supp_save(req.env, d)
        t = asyncio.create_task(_supp_analyze(req.env, iid))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    return {"ok": True, "triggered": len(ids)}


@app.get("/api/supplement/item")
def supp_item(env: str = "prod", id: str = ""):
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    with _supp_lock:
        it = _supp_item(_supp_load(env), id)
        if not it:
            raise HTTPException(404, "条目不存在")
        data = dict(it)
    conv = _load_conv(env, it.get("cid"))
    a0, a1 = int(it.get("astart") or 0), int(it.get("aend") or 0)
    rounds = ((conv or {}).get("rounds") or [])[a0:a1] if conv else []
    return {"item": data, "rounds": rounds}


class SuppSaveReq(BaseModel):
    env: str = "prod"
    id: str
    question_norm: str = ""
    answer_md: str
    note: str = ""


@app.post("/api/supplement/save")
def supp_save(req: SuppSaveReq):
    """答案写盘：md 落 kb/team/chat_qa/（frontmatter 溯源；H1=规范化问题，
    正文=人工答案。≤2000 字整卡一 chunk，问题标题与答案同块）。"""
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    if not req.answer_md.strip():
        raise HTTPException(400, "答案内容为空")
    with _supp_lock:
        d = _supp_load(req.env)
        it = _supp_item(d, req.id)
        if not it:
            raise HTTPException(404, "条目不存在")
        q_norm = (req.question_norm or it.get("question_norm")
                  or it.get("question_orig") or "").strip()[:80]
        if not q_norm:
            raise HTTPException(400, "缺规范化问题（先跑 AI 整理或手动填写）")
        it["question_norm"] = q_norm
        it["answer_md"] = req.answer_md
        it["note"] = req.note
        # 同段重复保存覆盖同名文件；文件名全 ASCII（qa0921_12345_0.md）
        fname = time.strftime("qa%m%d_") + f"{it['cid']}_{it['astart']}.md"
        media = it.get("media") or []
        orig = (it.get("question_orig") or "")[:120]
        fm = (f"---\nsource: chat_qa\ncid: {it['cid']}\nastart: {it['astart']}\n"
              f"qtype: {it.get('qtype') or '其他'}\n"
              f"orig_question: {orig}\n"
              f"author: {_tokens.get(DEFAULT_AI, {}).get('username') or '本地'}\n"
              f"created: {time.strftime('%Y-%m-%d')}\n---\n\n")
        content = fm + f"# {q_norm}\n\n" + req.answer_md.strip() + "\n"
        out = _supp_kb_dir() / fname
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        it["file"] = f"team/chat_qa/{fname}"
        it["status"] = "saved"
        it["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _supp_save(req.env, d)
    return {"ok": True, "file": it["file"], "media": media}


class SuppMediaReq(BaseModel):
    env: str = "prod"
    id: str
    # 二选一：data_b64（粘贴/本地上传，dataURL 或裸 base64）或 url（对话附件完整 URL）
    data_b64: str = ""
    ext: str = "png"     # data_b64 时的扩展名
    url: str = ""        # 对话附件：后端代下载落盘（知识库自持，不依赖原站点存活）


@app.post("/api/supplement/media")
def supp_media(req: SuppMediaReq):
    """图片落盘到 kb/team/chat_qa/media/，返回 md 引用片段。两张来路：
    ① 对话附件（url=完整附件 URL，后端代下载）② 本地/粘贴图（data_b64）。"""
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    mdir = _supp_media_dir()
    mdir.mkdir(parents=True, exist_ok=True)
    if req.url:
        if not req.url.startswith(("http://", "https://")):
            raise HTTPException(400, "url 非法")
        import httpx as _hx
        r = _hx.get(req.url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        blob = r.content
        ext = "." + (req.url.rsplit(".", 1)[-1].lower()[:5] or "png")
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            ext = ".png"
        name = f"att_{uuid.uuid4().hex[:8]}{ext}"
    elif req.data_b64:
        import base64 as _b64
        raw = req.data_b64.split(",", 1)[-1]
        blob = _b64.b64decode(raw)
        ext = "." + req.ext.lstrip(".").lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            ext = ".png"
        name = f"img_{uuid.uuid4().hex[:8]}{ext}"
    else:
        raise HTTPException(400, "data_b64 与 url 至少给一个")
    if len(blob) > 20 * 1024 * 1024:
        raise HTTPException(400, "图片超过 20MB")
    (mdir / name).write_bytes(blob)
    with _supp_lock:
        d = _supp_load(req.env)
        it = _supp_item(d, req.id)
        if it:
            it.setdefault("media", []).append(name)
            it["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _supp_save(req.env, d)
    return {"ok": True, "name": name,
            "md": f"![图片](media/{name})", "preview": f"/api/kb_media?rel=team/chat_qa/media/{name}"}


class SuppRemoveReq(BaseModel):
    env: str = "prod"
    id: str
    del_file: bool = False   # 已保存的连 md 一起删（media 留存，可能被别的卡引用）


@app.post("/api/supplement/remove")
def supp_remove(req: SuppRemoveReq):
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    with _supp_lock:
        d = _supp_load(req.env)
        it = _supp_item(d, req.id)
        if not it:
            raise HTTPException(404, "条目不存在")
        if req.del_file and it.get("file"):
            p = _supp_kb_dir().parent.parent / it["file"]
            if p.is_file():
                p.unlink()
        d["items"] = [x for x in d["items"] if x.get("id") != req.id]
        _supp_save(req.env, d)
    return {"ok": True}


class SuppPreviewReq(BaseModel):
    md: str


@app.post("/api/supplement/preview")
def supp_preview(req: SuppPreviewReq):
    """答案 md → 渲染 html（与知识库预览同款渲染 kb_md.md_to_html，
    media/ 相对引用走 /api/kb_media）。"""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from kb_md import md_to_html
    from urllib.parse import quote as _q
    html = md_to_html(req.md or "")
    html = re.sub(r'src="(media/[^"]+)"',
                  lambda m: 'src="/api/kb_media?rel=' + _q("team/chat_qa/" + m.group(1), safe="") + '"',
                  html)
    return {"html": html}


class SuppIngestReq(BaseModel):
    env: str = "prod"


@app.post("/api/supplement/ingest")
async def supp_ingest(req: SuppIngestReq):
    """一键入库（单卡增量）：已保存的卡逐张 parse→嵌入→upsert 进 team 当前
    活动集合。不走 ingest_all——那边 rebuild=True 恒全量重建集合（重嵌全域
    1438 chunks，一张卡没道理背这个成本）；单卡 upsert 不建集合不切指针，
    卡在 kb/ 源目录里，下次全量入库天然被扫描，两轨兼容。
    完成后后台逐卡探针 local 验「已补齐」。只进本地知识库。"""
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    if _sup_ing.get("busy"):
        raise HTTPException(409, "已有入库在跑")
    with _supp_lock:
        saved = [dict(it) for it in _supp_load(req.env)["items"] if it.get("status") == "saved"]
    if not saved:
        raise HTTPException(400, "没有已保存待入库的条目（先在编辑器保存答案）")
    _sup_ing.update(busy=True, logs=[], rc=None, verify="")

    async def gen():
        yield f"data: {json.dumps({'event': 'begin', 'data': {'n_saved': len(saved)}}, ensure_ascii=False)}\n\n"

        def L(msg):
            _sup_ing["logs"].append(msg)
            return f"data: {json.dumps({'event': 'log', 'data': {'line': msg}}, ensure_ascii=False)}\n\n"

        rc = 0
        try:
            from ai.config import get_ai_config, get_active_collection_for
            from ai.ingestion.parsers.kb_markdown import KBDomainIngester
            from ai.ingestion.base import BaseIngester
            col = get_active_collection_for("team")
            # 指针失效自愈（dar-qdrant-pointer-traps 老坑：指针指向已清理
            # 的集合）——不越权改指针文件，仅在现存 team_* 集合里挑名字最新
            # 的用，并明写日志；下次全量入库会正常切指针。
            ing = KBDomainIngester(domain="team")
            qc = BaseIngester._make_qdrant_client(get_ai_config())
            try:
                if not col or not qc.collection_exists(col):
                    avail = sorted(c.name for c in qc.get_collections().collections
                                   if c.name.startswith("team_"))
                    if not avail:
                        raise RuntimeError("本地无任何 team_* 集合（先全量入库一次）")
                    yield L(f"[WARN] 活动指针 {col or '（空）'} 指向不存在集合，fallback → {avail[-1]}")
                    col = avail[-1]
                yield L(f"[INFO] 目标集合 {col}（单卡增量，不动指针）")
                done = 0
                for it in saved:
                    done += 1
                    fname = os.path.basename(it.get("file") or "")
                    p = _supp_kb_dir() / fname
                    tag = f"[{done}/{len(saved)}] {fname}"
                    if not p.is_file():
                        yield L(f"[MISS] {tag} 卡文件不在盘上，跳过")
                        continue
                    ing.source_paths = [p]
                    chunks = [ing.to_chunk(e) for e in ing.parse()]
                    if not chunks:
                        yield L(f"[WARN] {tag} 解析出 0 chunk，跳过")
                        continue
                    await ing.embed_and_upsert(chunks, col, client=qc)
                    yield L(f"[OK] {tag} → {len(chunks)} chunk 已入")
            finally:
                qc.close()
        except Exception as e:
            rc = 1
            yield L(f"[ERR] {type(e).__name__}: {e}")
        _sup_ing["rc"] = rc
        if rc == 0:
            _sup_ing["verify"] = "running"
            yield L("入库完成，逐卡探针验证「已补齐」中…（结果稍后刷新清单可见）")
            t = asyncio.create_task(_supp_verify_ingested(req.env))
            _bg_tasks.add(t)
            t.add_done_callback(_bg_tasks.discard)
        yield f"data: {json.dumps({'event': 'done', 'data': {'rc': rc, 'verify': _sup_ing['verify']}}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        _sup_ing["busy"] = False

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _supp_verify_ingested(env: str):
    """入库后逐条探针 local 验「已补齐」：命中→status=ingested；未命中保留
    saved 并记 verify 供排查（可能是冷启动吞域——重跑一次即知）。"""
    with _supp_lock:
        items = [it for it in _supp_load(env)["items"] if it.get("status") == "saved"]
    for it in items:
        q = it.get("question_norm") or it.get("question_orig") or ""
        if not q:
            continue
        res, _ = await asyncio.to_thread(_run_probe_sync, q, "local")
        chunks = (res or {}).get("chunks") or []
        top = chunks[0] if chunks else {}
        # 命中块的 title=卡片 H1=规范化问题（_split_generic 短文档路径
        # doc_title 即 H1）——top 标题含问题前缀即认「已补齐」
        ok = bool(chunks) and q[:12] in (top.get("title") or "")
        with _supp_lock:
            d = _supp_load(env)
            cur = _supp_item(d, it["id"])
            if cur:
                cur["verify"] = {"ok": ok, "n_chunks": len(chunks),
                                 "top_title": (top.get("title") or "")[:60]}
                if ok:
                    cur["status"] = "ingested"
                    cur["ingested_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                _supp_save(env, d)


@app.get("/api/supplement/ingest_status")
def supp_ingest_status():
    """入库任务状态（页面刷新恢复现场；SSE 断开任务即中止，重跑即可）。"""
    return {"running": bool(_sup_ing.get("busy")), "rc": _sup_ing["rc"],
            "verify": _sup_ing["verify"],
            "n": len(_sup_ing["logs"]),
            "logs": _sup_ing["logs"][-40:]}


# ── 知识库结构（页签4 知识库）────────────────────────────────
# sub_domain → 检索标签（仅展示用；与 retrieval.py / pipeline.py 的 _sub_labels
# 同源，那边新增子域后这里补一行即可，漏了只影响着色不影响功能）
_KB_LABELS = {
    "navigation": "📐 导航", "standards": "📐 标准",
    "product_catalog": "🏢 产品", "vda5050_protocol": "🏢 协议",
    "vehicle_errors": "🚗 车端", "vehicle_implementation": "🚗 车端",
    "vehicle_calibration": "🚗 车端", "vehicle_io": "🚗 车端", "vehicle_motion": "🚗 车端",
    "vehicle_implementation/huarui": "🤖 华睿", "vehicle_implementation/科钛VDA5050接入": "🤖 科钛",
    "ORS": "🎫 服务号",
    "team/diagnosis_cards": "🔍 诊断卡", "USP/faq": "📋 FAQ", "USP/manual": "📖 手册",
    "team/chat_qa": "💬 对话补充",
    "USP/error_codes": "🚨 平台错误码", "USP/overview": "📘 模块文档",
    "USP/troubleshooting": "🏭 排查树", "USP/translation": "🌐 翻译",
    "USP/terminology": "🔤 术语表", "USP/ui_pages": "🧭 页面导航",
}

# 目录名 → 中文名（树图/列表展示用；原名进 tooltip 和过滤）
_KB_DIR_CN = {
    "navigation": "导航原理", "standards": "国标文档",
    "product_catalog": "产品目录", "vda5050_protocol": "VDA5050 协议",
    "vehicle_errors": "车端错误码", "vehicle_implementation": "车端实施",
    "自研车实施": "🚚 自研车",
    "华睿VDA5050接入": "🤖 华睿",
    "科钛VDA5050接入": "🤖 科钛",
    "vehicle_calibration": "车辆标定", "vehicle_io": "IO 定义", "vehicle_motion": "运动控制",
    "ORS": "服务号平台", "USP": "USP 平台",
    "chat_qa": "对话补充",
    "diagnosis_cards": "诊断知识卡", "error_codes": "平台错误码", "faq": "常见问答",
    "manual": "操作手册", "overview": "模块概览", "terminology": "术语表",
    "translation": "翻译对照", "troubleshooting": "故障排查树", "ui_pages": "页面导航",
    "map": "地图", "monitor": "监控", "peripheral": "外设", "robot": "机器人",
    "simulator": "仿真", "system": "系统", "task": "任务", "warehousing": "仓储",
    "algorithm": "算法原理", "algorithms": "算法原理", "算法": "算法原理",
    "xmover": "🚚 自研车", "huarui": "🤖 华睿", "ksec": "🤖 科钛", "common": "🧩 通用",
}

_KB_CACHE: dict = {"sig": None, "data": None}
_KB_DISK_CACHE = os.path.join(PROJ, "ai", "kb", "kb_structure_cache.json")


def _kb_disk_load(sig: str):
    """磁盘缓存：进程重启后指纹没变就免 parse 秒开（首次部署/文件变更才重算）。"""
    try:
        if os.path.isfile(_KB_DISK_CACHE):
            with open(_KB_DISK_CACHE, encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("sig") == sig and d.get("data"):
                return d["data"]
    except Exception:
        pass
    return None


def _kb_disk_save(sig: str, data):
    try:
        os.makedirs(os.path.dirname(_KB_DISK_CACHE), exist_ok=True)
        with open(_KB_DISK_CACHE, "w", encoding="utf-8") as fh:
            json.dump({"sig": sig, "data": data}, fh, ensure_ascii=False)
    except Exception:
        pass


def _kb_fingerprint() -> str:
    """全部 KB md 文件的 mtime+size 指纹——没变就直接用缓存，页面秒开。"""
    from ai.config import _KB_DIR
    h = hashlib.md5()
    for d in ("industry", "company", "team", "project", "personal"):
        root = _KB_DIR / d
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            st = p.stat()
            h.update(f"{p}|{st.st_mtime_ns}|{st.st_size};".encode())
    sc = _KB_DIR / "sink_cards"
    if sc.is_dir():
        for p in sorted(sc.glob("*.md")):
            st = p.stat()
            h.update(f"{p}|{st.st_mtime_ns}|{st.st_size};".encode())
    return h.hexdigest()


def _kb_build() -> dict:
    """扫 KB 源目录 → 真实 parser 算 chunk 数 → 目录树 + 域级指针/入库时间。"""
    from ai.config import _KB_DIR, get_active_collection_for
    from ai.ingestion.parsers.kb_markdown import KBDomainIngester

    domains = []
    total_files = total_chunks = 0
    for d in ("industry", "company", "team", "project", "personal"):
        ing = KBDomainIngester(domain=d)
        per_file: dict = {}
        first_title: dict = {}
        file_brand: dict = {}
        for e in ing.parse():
            per_file[e.source_file] = per_file.get(e.source_file, 0) + 1
            first_title.setdefault(e.source_file, e.title)
            if d == "company":
                file_brand[e.source_file] = ing._infer_brand(e.sub_domain, e.source_file)

        def _file_cn(rel: str) -> str:
            """文件中文标题：优先 H1（卡片无 H1 用 frontmatter title 即 chunk 首题）。"""
            try:
                with open(ing._domain_dir / rel, encoding="utf-8") as fh:
                    text = fh.read(4000)
                m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
                if m:
                    return m.group(1).strip()
            except Exception:
                pass
            return (first_title.get(rel) or "").split(" / ")[0].strip()

        cn_file = {rel: _file_cn(rel) for rel in per_file}

        _KB_DOMAIN_CN = {"industry": "行业知识", "company": "公司 · 车端", "team": "团队知识",
                         "project": "项目知识", "personal": "个人知识"}
        root = {"name": d, "dir": True, "chunks": 0, "files": 0, "children": [],
                "cn": _KB_DOMAIN_CN.get(d, d)}
        for rel in sorted(per_file):
            node = root
            parts = rel.split("/")
            for seg in parts[:-1]:
                child = next((c for c in node["children"] if c["dir"] and c["name"] == seg), None)
                if child is None:
                    child = {"name": seg, "dir": True, "chunks": 0, "files": 0, "children": [],
                             "cn": _KB_DIR_CN.get(seg, ""), "label": ""}
                    # 深两级子目录（如 USP/算法、华睿VDA5050接入）按完整路径匹配 label
                    deep_key = parts[0] + "/" + seg if len(parts) > 1 else seg
                    child["label"] = _KB_DIR_CN.get(deep_key, "") or _KB_DIR_CN.get(seg, "")
                    node["children"].append(child)
                node = child
            node["children"].append({"name": parts[-1], "dir": False,
                                     "chunks": per_file[rel], "cn": cn_file.get(rel, ""),
                                     "brand": file_brand.get(rel, ""), "path": d + "/" + rel})

        def _agg(n):
            if not n["dir"]:
                return n["chunks"], 1
            cs = fs = 0
            for c in n["children"]:
                cc, cf = _agg(c)
                c["chunks"], c["files"] = cc, cf
                cs += cc
                fs += cf
            return cs, fs

        root["chunks"], root["files"] = _agg(root)

        def _brand_agg(n):
            if not n["dir"]:
                return n.get("brand", "")
            brands = {_brand_agg(c) for c in n["children"]}
            brands.discard("")
            n["brand"] = brands.pop() if len(brands) == 1 else "混合"
            return n["brand"]
        _brand_agg(root)

        # 公司域：品牌优先分组（0916 用户定稿——公司树第一层 自研车/华睿/科钛/通用/产品目录）。
        # 纯视图层重排：文件不动、sub_domain 不动、检索零影响；文件节点带 path 供预览。
        if d == "company":
            file_list = []
            def _collect_files(n, prefix):
                if not n["dir"]:
                    file_list.append((prefix + n["name"], n))
                    return
                for c in n["children"]:
                    _collect_files(c, prefix + n["name"] + "/")
            for c0 in root["children"]:
                _collect_files(c0, "")
            group_order = ["自研车", "华睿", "科钛", "通用", "产品目录"]
            gbrand = {"自研车": "自研", "华睿": "华睿", "科钛": "科钛",
                      "通用": "通用", "产品目录": "自研"}
            groups = {g: {"name": g, "dir": True, "chunks": 0, "files": 0,
                          "children": [], "cn": g, "label": "", "brand": gbrand[g],
                          "path": ""} for g in group_order}
            for rel, node in file_list:
                b = node.get("brand", "通用")
                if rel.startswith("product_catalog/"):
                    gname, inner = "产品目录", rel[len("product_catalog/"):]
                elif b == "华睿":
                    gname = "华睿"
                    inner = rel[len("vehicle_implementation/"):] if rel.startswith("vehicle_implementation/") else rel
                    if inner.startswith("华睿VDA5050接入/"):  # 组名已表意，剥掉同名层
                        inner = inner[len("华睿VDA5050接入/"):]
                elif b == "科钛":
                    gname = "科钛"
                    inner = rel[len("vehicle_implementation/"):] if rel.startswith("vehicle_implementation/") else rel
                    if inner.startswith("科钛VDA5050接入/"):
                        inner = inner[len("科钛VDA5050接入/"):]
                elif b == "自研":
                    gname, inner = "自研车", rel
                    if inner.startswith("vehicle_implementation/"):  # 组名已表意，剥掉冗余层
                        inner = inner[len("vehicle_implementation/"):]
                else:
                    gname, inner = "通用", rel
                node = dict(node)  # path 已是 KB 根相对（company/...），保留
                cur = groups[gname]
                for seg in inner.split("/")[:-1]:
                    child = next((c for c in cur["children"] if c["dir"] and c["name"] == seg), None)
                    if child is None:
                        child = {"name": seg, "dir": True, "chunks": 0, "files": 0,
                                 "children": [], "cn": _KB_DIR_CN.get(seg, ""), "path": ""}
                        cur["children"].append(child)
                    cur = child
                cur["children"].append(node)
            def _agg2(n):
                if not n["dir"]:
                    return n["chunks"], 1
                cs = fs = 0
                for c in n["children"]:
                    cc, cf = _agg2(c)
                    c["chunks"], c["files"] = cc, cf
                    cs += cc
                    fs += cf
                return cs, fs
            root["children"] = [groups[g] for g in group_order]
            root["chunks"], root["files"] = _agg2(root)
        for c in root["children"]:
            if c["dir"]:
                c["label"] = _KB_LABELS.get(f"{d}/{c['name']}", _KB_LABELS.get(c["name"], ""))

        state_p = _KB_DIR / ".ingest_state" / f"{d}.json"
        ing_at = ""
        if state_p.is_file():
            ing_at = time.strftime("%m-%d %H:%M", time.localtime(state_p.stat().st_mtime))
        domains.append({"domain": d, "files": root["files"], "chunks": root["chunks"],
                        "collection": get_active_collection_for(d) or "",
                        "ingest_at": ing_at, "tree": root})
        total_files += root["files"]
        total_chunks += root["chunks"]

    # 工单沉淀卡（kb/sink_cards/*.md，审核 approved 的本地留存；一卡一 chunk）
    sink_dir = _KB_DIR / "sink_cards"
    sink_files = sorted(sink_dir.glob("*.md")) if sink_dir.is_dir() else []
    sink_children = []
    for f in sink_files:
        head = f.read_text(encoding="utf-8")[:400]
        m = re.search(r"^# (.+)$", head, re.MULTILINE)
        v = re.search(r"- 判定: (✅|❌|🧪)", head)
        cn = (m.group(1).strip() if m else f.stem)
        if v:
            cn = v.group(1) + " " + cn
        sink_children.append({"name": f.name, "dir": False, "chunks": 1, "cn": cn})
    sink_node = {"name": "sink_cards", "dir": True, "chunks": len(sink_files),
                 "files": len(sink_files), "cn": "工单沉淀卡",
                 "label": "📥 沉淀", "children": sink_children}
    domains.append({"domain": "sink_cards", "files": len(sink_files),
                    "chunks": len(sink_files), "collection": "", "ingest_at": "",
                    "tree": sink_node})
    total_files += len(sink_files)
    total_chunks += len(sink_files)
    return {"domains": domains, "total_files": total_files, "total_chunks": total_chunks,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}



_SINK_QCACHE: dict = {"v": -1, "at": 0.0}


def _kb_sink_info() -> dict:
    """工单沉淀卡概览：本地在库数 + 最新审核导出进度（卡片本体在生产库）。"""
    out = {"local_cards": -1, "found": False}
    # 本地 qdrant 是嵌入式 path 模式，每次开库数一遍要秒级——结果缓存 10 分钟
    if time.time() - _SINK_QCACHE["at"] < 600:
        out["local_cards"] = _SINK_QCACHE["v"]
    else:
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            from ai.ingestion.base import BaseIngester
            from ai.config import get_ai_config, get_active_collection_for
            col = get_active_collection_for("company")
            if col:
                qc = BaseIngester._make_qdrant_client(get_ai_config())
                try:
                    flt = Filter(must=[FieldCondition(key="sub_domain",
                                                      match=MatchValue(value="ticket_resolutions"))])
                    _SINK_QCACHE["v"] = qc.count(col, count_filter=flt).count
                    _SINK_QCACHE["at"] = time.time()
                    out["local_cards"] = _SINK_QCACHE["v"]
                    out["collection"] = col
                finally:
                    qc.close()
        except Exception:
            pass
    try:
        out.update(sink_status())
    except Exception:
        pass
    return out


_SINK_SYNC: dict = {"sig": None}


def _sync_sink_if_stale():
    """导出批次有变化时增量同步沉淀卡留存文件（打开知识库页签即自动同步）。"""
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        from sink_cards_store import exports_signature, sync_sink_cards
        sig = exports_signature()
        if _SINK_SYNC["sig"] != sig:
            r = sync_sink_cards()
            _SINK_SYNC["sig"] = sig
            print(f"[sink-cards] 留存同步: {r}")
    except Exception as e:
        print(f"[sink-cards] 同步失败: {e}")


@app.get("/api/kb_structure")
def kb_structure(refresh: int = 0):
    """KB 目录树 + chunk 统计（复用真实 parser；源文件 mtime 没变走缓存，refresh=1 强制重算）。"""
    _sync_sink_if_stale()
    try:
        sig = _kb_fingerprint()
    except Exception as e:
        raise HTTPException(500, f"扫描 KB 目录失败: {e}")
    if refresh or _KB_CACHE["data"] is None or _KB_CACHE["sig"] != sig:
        data = None if refresh else _kb_disk_load(sig)  # 重启后免 parse 秒开
        if data is None:
            try:
                data = _kb_build()
                _kb_disk_save(sig, data)
            except Exception as e:
                raise HTTPException(500, f"解析 KB 失败: {e}")
        _KB_CACHE["sig"] = sig
        _KB_CACHE["data"] = data
    data = dict(_KB_CACHE["data"])
    data["sink"] = _kb_sink_info()
    return data


@app.get("/vendor/echarts.min.js")
def vendor_echarts():
    """本地 vendored echarts（知识库树图用），避免外网 CDN 依赖。"""
    return FileResponse(os.path.join(HERE, "vendor", "echarts.min.js"),
                        media_type="application/javascript",
                        headers={"Cache-Control": "max-age=86400"})


def _kb_safe_path(rel: str, exts: tuple):
    """KB 相对路径校验：禁止穿越、限扩展名、必须存在。返回绝对 Path。"""
    from ai.config import _KB_DIR
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or any(seg in ("..", "") for seg in rel.split("/")):
        raise HTTPException(400, "非法路径")
    p = (_KB_DIR / rel).resolve()
    if not str(p).startswith(str(_KB_DIR.resolve())):
        raise HTTPException(400, "路径越界")
    if p.suffix.lower() not in exts or not p.is_file():
        raise HTTPException(404, "文件不存在")
    return p


@app.get("/api/kb_file")
def kb_file(rel: str = ""):
    """读单个 KB md 源文件：渲染 html + chunk 划分（预览抽屉用）。"""
    p = _kb_safe_path(rel, (".md",))
    rel_n = rel.replace("\\", "/").lstrip("/")
    base = rel_n.rsplit("/", 1)[0] + "/" if "/" in rel_n else ""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from kb_md import md_to_html
    from urllib.parse import quote
    text = p.read_text(encoding="utf-8")
    html = md_to_html(text)
    html = re.sub(r'src="(media/[^"]+)"',
                  lambda m: 'src="/api/kb_media?rel=' + quote(base + m.group(1), safe="") + '"',
                  html)
    domain = rel_n.split("/", 1)[0]
    chunks = []
    try:
        from ai.ingestion.parsers.kb_markdown import KBDomainIngester
        ing = KBDomainIngester(domain=domain)
        ing.source_paths = [p]
        chunks = [{"title": e.title, "chars": len(e.content), "preview": e.content[:150]}
                  for e in ing.parse()]
    except Exception:
        # 非域目录文件（如 sink_cards 沉淀卡）：整文件即一块
        chunks = [{"title": p.name + "（整卡一块）", "chars": len(text),
                   "preview": text[:150]}]
    return {"rel": rel_n, "html": html, "chunks": chunks}


_IMG_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}


@app.get("/api/kb_media")
def kb_media(rel: str = ""):
    """KB 内图片（预览抽屉里 md 引用的 media/xxx.png）。"""
    p = _kb_safe_path(rel, tuple(_IMG_TYPES))
    return FileResponse(p, media_type=_IMG_TYPES[p.suffix.lower()],
                        headers={"Cache-Control": "max-age=3600"})


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "dar_studio.html"))


if __name__ == "__main__":
    print(f"AI 质量工作台 → http://127.0.0.1:{PORT}")
    # 预热：本地 qdrant 数沉淀卡（嵌入式库首次打开 ~3s）挪到启动时后台做，
    # 否则知识库页签首次打开会被这 3s 卡住
    threading.Thread(target=_kb_sink_info, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
