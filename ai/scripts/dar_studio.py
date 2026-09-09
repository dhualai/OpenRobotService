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
import subprocess
import sys
import threading
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
DATA_ROOT = r"C:/Users/PAJ26020/Desktop/export_dar"
PORT = 9527

DEFAULT_BACKEND = "http://127.0.0.1:19640"      # 经 ssh 隧道 → 测试环境后端 9400（login）
DEFAULT_AI = "http://127.0.0.1:19641"            # 经 ssh 隧道 → 测试环境 AI 服务 9401（ask/stream）
SSH_HOST = "usp-a@125.122.97.107"
SSH_PORT = "8802"

app = FastAPI(title="AI 质量工作台")

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
    """本地 19640/19641 → 服务器 9400/9401。已有隧道（含上次实例残留）直接复用。"""
    if _tunnel["ready"] or _port_open(19640):
        _tunnel["ready"] = True
        return True
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
            print(f"ssh 隧道就绪：19640→9400 / 19641→9401（{SSH_HOST}:{SSH_PORT}）")
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
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


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


@app.post("/api/stop_regression")
def stop_regression():
    p = _reg_state["proc"]
    if p and p.poll() is None:
        p.kill()
        return {"ok": True, "killed": True}
    return {"ok": True, "killed": False}


# ── 周流程（subprocess dar_weekly，SSE 日志）────────────────────
_run_state: dict = {"proc": None, "logs": [], "cmd": "", "env": "", "rc": None}


class RunReq(BaseModel):
    env: str = "test"
    steps: list[str]
    note: str = ""


class StopReq(BaseModel):
    pass


@app.post("/api/run")
async def run(req: RunReq):
    if _run_state["proc"] and _run_state["proc"].poll() is None:
        raise HTTPException(409, "已有流程在跑（先停止）")
    if req.env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    args = [sys.executable, os.path.join(HERE, "dar_weekly.py"), "--env", req.env]
    if req.note:
        args += ["--note", req.note]
    args += req.steps
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        cwd=PROJ, env=_child_env())
    _run_state.update(proc=proc, logs=[], cmd=" ".join(req.steps),
                      env=req.env, rc=None)

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
        p.kill()
        return {"ok": True, "killed": True}
    return {"ok": True, "killed": False}


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


@app.get("/api/metrics")
def metrics(env: str = "prod"):
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    files = _weekly_files(env)
    src = env
    if not files and env == "prod":  # 生产尚未出周报 → 回退展示 test 数据（标注来源）
        src = "test"
        files = _weekly_files(src)
    if not files:
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
    with open(files[-1], encoding="utf-8") as fh:
        rep = json.load(fh)

    hero, small = [], []
    same = rep.get("dar_rates_same_base") or {}
    zh = {"L1_段级": ("L1 机器信号", "未出单即算直答（上界）"),
          "L2_人工": ("L2 人工标注", "端到端真实口径"),
          "L3_AI同段": ("L3 AI 预标", "judge 偏宽仅供参考")}
    for k, (label, sub) in zh.items():
        if same.get(k):
            hero.append({"label": label, "value": _pct(same[k]), "sub": f"{same[k]} · {sub}"})
    if rep.get("kb_gap"):
        small.append({"label": "KB 缺口率", "value": _pct(rep["kb_gap"]),
                      "sub": "真实组检索 no（知识库没有答案）"})
    ar = rep.get("avg_rounds") or {}
    if ar.get("直答正确段"):
        rest = " · ".join(f"{k} {v}" for k, v in ar.items() if k != "直答正确段")
        m = _re.match(r"([\d.]+)", ar["直答正确段"])
        small.append({"label": "平均解决轮次", "value": (m.group(1) + " 轮") if m else ar["直答正确段"],
                      "sub": f"直答正确段 {ar['直答正确段']}" + (f" · {rest}" if rest else "")})
    prec = rep.get("precision") or {}
    if prec.get("overall"):
        small.append({"label": "L3 precision", "value": _pct(prec["overall"]),
                      "sub": ("≥90% 可放权（人工只抽检）" if prec.get("delegable")
                              else "未达 90% 放权线，预标仅供参考")})
    if rep.get("manual_progress"):
        small.append({"label": "标注进度", "value": rep["manual_progress"],
                      "sub": "人工标签（L2）覆盖"})
    return {"found": True, "source_env": src, "file": os.path.basename(files[-1]),
            "date": rep.get("date"), "note": rep.get("note"),
            "meta": rep.get("meta"), "manual_progress": rep.get("manual_progress"),
            "avg_rounds": ar, "ticket_quality": rep.get("ticket_quality"),
            "dar_rates": rep.get("dar_rates"), "same_base": same,
            "wow": rep.get("wow"), "this_week": rep.get("this_week"),
            "unanswered_total": rep.get("unanswered_total"),
            "unanswered_this_week": rep.get("unanswered_this_week"),
            "trend": trend, "hero": hero, "small": small}


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


def _mtime_str(p: str) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
    except OSError:
        return ""


@app.get("/api/progress")
def progress(env: str = "prod"):
    """向导各步完成状态：从产物文件 mtime 推断（页面刷新不丢）。"""
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    proc = os.path.join(DATA_ROOT, env, "processed")

    def latest(pat: str) -> str:
        files = sorted(glob.glob(os.path.join(proc, pat)))
        return _mtime_str(files[-1]) if files else ""

    manual = {"test": "manual_segmentation.json",
              "prod": "manual_segmentation_prod.json"}[env]
    return {"env": env, "steps": {
        "export": _mtime_str(os.path.join(proc, "conversations_split.jsonl")),
        "l1": latest("direct_answer_summary_*.json"),
        "l3": latest("segmentation_tool.html") or latest("l3_judge_all_*.json"),
        "label": _mtime_str(os.path.join(os.path.expanduser("~"), "Downloads", manual)),
        "report": latest("weekly_*.json"),
    }}


@app.get("/label_tool")
def label_tool(env: str = "test"):
    if env not in ("test", "prod"):
        raise HTTPException(400, "env 取值 test|prod")
    p = os.path.join(DATA_ROOT, env, "processed", "segmentation_tool.html")
    if not os.path.exists(p):
        raise HTTPException(404, "标注工具未生成（先运行「生成标注工具」步骤）")
    return FileResponse(p)


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "dar_studio.html"))


if __name__ == "__main__":
    print(f"AI 质量工作台 → http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
