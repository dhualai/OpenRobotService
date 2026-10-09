"""USP 现场车态探测：SSH → Ray DYNAMIC-MAP Actor → JSON 快照。

用于「路径规划中」缺环：在日志缺 TMS-MAP 时，仍能读到
taskId / taskNodeId / pathId / mapId / canMove / cur_node。
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.server_pull.env_client import fetch_usp_env_ssh_config
from ai.agents.AiTaskPlatform.server_pull.ssh_client import SshClient

logger = get_logger("TASK_AGENT")

_MARKER_START = "###USP_LIVE_PROBE_JSON###"
_MARKER_END = "###USP_LIVE_PROBE_END###"

# 远程探测脚本：不依赖仓库文件，通过 base64 落到目标机执行
_REMOTE_SCRIPT = r'''
import json
import os
import re
import subprocess
import sys

DMAP = "DYNAMIC-MAP-SERVICES-ACTOR"
ROBOT_FILTER = os.environ.get("USP_PROBE_ROBOT", "").strip().lower()
MAP_FILTER = os.environ.get("USP_PROBE_MAP", "").strip()
PYTHONPATH_HINT = os.environ.get("USP_PROBE_PYTHONPATH", "").strip()

def _out(obj):
    print("###USP_LIVE_PROBE_JSON###")
    print(json.dumps(obj, ensure_ascii=False, default=str))
    print("###USP_LIVE_PROBE_END###")

def _enum_name(v):
    if v is None:
        return None
    return getattr(v, "name", None) or str(v)

def _node_id(n):
    if n is None:
        return None
    return getattr(n, "id", None)

def _ser_robot(rid, data):
    cts = getattr(data, "cur_task_states", None)
    bs = getattr(data, "backend_states", None)
    cn = getattr(data, "cur_node", None)
    tasks_brief = []
    for t in (getattr(data, "tasks", None) or [])[:3]:
        nodes = []
        for tn in (getattr(t, "task_nodes", None) or [])[:5]:
            pt = getattr(tn, "point", None)
            nd = getattr(tn, "node", None)
            nodes.append({
                "id": getattr(tn, "id", None),
                "pointId": getattr(pt, "id", None) if pt is not None else getattr(tn, "point_id", None),
                "nodeId": getattr(nd, "id", None) if nd is not None else getattr(tn, "node_id", None),
            })
        tasks_brief.append({"id": getattr(t, "id", None), "taskNodeList": nodes})
    path_id = getattr(cts, "path_id", None) if cts else None
    return {
        "robotId": rid,
        "mapId": getattr(data, "map_id", None),
        "canMove": getattr(bs, "can_move", None) if bs else None,
        "workingState": _enum_name(getattr(bs, "working_state", None) if bs else None),
        "curNodeId": _node_id(cn),
        "hasCurNode": cn is not None,
        "currentTaskState": {
            "taskId": getattr(cts, "task_id", None) if cts else None,
            "taskNodeId": getattr(cts, "task_node_id", None) if cts else None,
            "pathId": path_id,
        },
        "tasks": tasks_brief,
        "pathIdIsNone": path_id is None,
    }

def _detect_ns():
    from ray.util.state import list_actors
    for a in list_actors():
        if getattr(a, "name", None) == DMAP and "ALIVE" in str(getattr(a, "state", "")):
            return getattr(a, "ray_namespace", None)
    return None

def _scan_raylets():
    found = []
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "raylet" not in line:
                continue
            for m in re.finditer(r"((?:\d{1,3}\.){3}\d{1,3}):(\d{4,5})", line):
                addr = "%s:%s" % (m.group(1), m.group(2))
                if addr not in found:
                    found.append(addr)
    except Exception:
        pass
    return found

def main():
    try:
        import ray
    except Exception as e:
        _out({"ok": False, "error": "ray_import: %s" % e})
        return 2

    env_vars = {
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
    if PYTHONPATH_HINT:
        env_vars["PYTHONPATH"] = PYTHONPATH_HINT

    def _init(addr):
        ray.init(address=addr, ignore_reinit_error=True, runtime_env={"env_vars": env_vars})

    try:
        _init("auto")
    except Exception as e:
        _out({"ok": False, "error": "ray_init_auto: %s" % e})
        return 3

    ns = _detect_ns()
    used = "auto"
    if not ns:
        ray.shutdown()
        cands = []
        if os.environ.get("RAY_ADDRESS"):
            cands.append(os.environ["RAY_ADDRESS"])
        for a in _scan_raylets():
            if a not in cands:
                cands.append(a)
        for addr in cands:
            try:
                _init(addr)
                ns = _detect_ns()
                if ns:
                    used = addr
                    break
                ray.shutdown()
            except Exception:
                try:
                    ray.shutdown()
                except Exception:
                    pass
        if not ns:
            _out({"ok": False, "error": "no DYNAMIC-MAP-SERVICES-ACTOR in any ray cluster"})
            return 4

    try:
        dmap = ray.get_actor(DMAP, namespace=ns)
    except Exception as e:
        _out({"ok": False, "error": "get_actor: %s" % e, "ray_addr": used, "namespace": ns})
        return 5

    # 现场 Actor 签名可能只有 self，或要关键字 map_id；勿传位置参数 None
    snap = None
    source = ""
    err_bits = []
    for label, call in (
        ("snapshot()", lambda: ray.get(dmap.get_robots_data_snapshot.remote())),
        ("snapshot(map_id=kw)", lambda: ray.get(
            dmap.get_robots_data_snapshot.remote(map_id=MAP_FILTER or None)
        )),
        ("get_robots_data()", lambda: ray.get(dmap.get_robots_data.remote())),
    ):
        try:
            raw = call()
            # get_robots_data 返回 (robots_dict, path_ids, extra, obstacles, t)
            if isinstance(raw, tuple):
                snap = raw[0] if raw else None
                source = "ray:DYNAMIC-MAP.get_robots_data"
            else:
                snap = raw
                source = "ray:DYNAMIC-MAP.get_robots_data_snapshot"
            break
        except Exception as e:
            err_bits.append("%s: %s" % (label, e))
            snap = None

    if snap is None and err_bits:
        _out({
            "ok": False,
            "error": "get_snapshot: " + " | ".join(err_bits),
            "ray_addr": used,
            "namespace": ns,
        })
        return 5

    if snap is None:
        _out({
            "ok": False,
            "error": "robots_data_updating",
            "hint": "DYNAMIC_MAP 正在更新车态，请稍后重试",
            "ray_addr": used,
            "namespace": ns,
        })
        return 6

    if not isinstance(snap, dict):
        _out({
            "ok": False,
            "error": "unexpected_snapshot_type: %s" % type(snap).__name__,
            "ray_addr": used,
            "namespace": ns,
        })
        return 5

    robots = {}
    for rid, data in (snap or {}).items():
        rid_s = str(rid)
        if ROBOT_FILTER and rid_s.lower() != ROBOT_FILTER and ROBOT_FILTER not in rid_s.lower():
            continue
        try:
            row = _ser_robot(rid_s, data)
        except Exception as e:
            row = {"robotId": rid_s, "error": str(e)}
        if MAP_FILTER and str(row.get("mapId") or "") != MAP_FILTER:
            continue
        robots[rid_s] = row

    # 若按车号过滤后为空，回退全量，避免误筛
    if ROBOT_FILTER and not robots and snap:
        for rid, data in snap.items():
            try:
                robots[str(rid)] = _ser_robot(str(rid), data)
            except Exception as e:
                robots[str(rid)] = {"robotId": str(rid), "error": str(e)}

    focus = None
    if ROBOT_FILTER:
        for k, v in robots.items():
            if k.lower() == ROBOT_FILTER or ROBOT_FILTER in k.lower():
                focus = v
                break

    _out({
        "ok": True,
        "source": source,
        "ray_addr": used,
        "namespace": ns,
        "robot_filter": ROBOT_FILTER or None,
        "map_filter": MAP_FILTER or None,
        "robot_count": len(robots),
        "focus": focus,
        "robots": robots,
    })
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
'''


def _sh_quote(s: str) -> str:
    return "'" + (s or "").replace("'", "'\"'\"'") + "'"


def _parse_probe_stdout(stdout: str) -> Dict[str, Any]:
    text = stdout or ""
    i = text.find(_MARKER_START)
    j = text.find(_MARKER_END)
    if i < 0 or j < 0 or j <= i:
        raise RuntimeError(f"探测脚本未输出 JSON 标记 stdout={text[-500:]!r}")
    raw = text[i + len(_MARKER_START) : j].strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("探测结果不是 JSON 对象")
    return data


def _pick_python_candidates(workdir: str) -> List[str]:
    return [
        "/opt/conda-envs/usp-algorithm/bin/python",
        f"{workdir.rstrip('/')}/.venv/bin/python",
        "python3",
        "python",
    ]


def _build_remote_cmd(
    *,
    workdir: str,
    docker_container: str = "",
    docker_sudo: bool = False,
    robot_id: str = "",
    map_id: str = "",
) -> str:
    """写临时脚本并用 conda/系统 python 执行；有 docker 则进容器。"""
    b64 = base64.b64encode(_REMOTE_SCRIPT.encode("utf-8")).decode("ascii")
    remote_py = "/tmp/usp_live_probe_ors.py"
    py_cands = _pick_python_candidates(workdir)
    # 逐个试 python，直到成功写出 JSON 标记
    py_try = " || ".join(
        f"{_sh_quote(p)} {_sh_quote(remote_py)}" for p in py_cands
    )
    env_exports = (
        f"export USP_PROBE_PYTHONPATH={_sh_quote(workdir)}; "
        f"export USP_PROBE_ROBOT={_sh_quote((robot_id or '').strip())}; "
        f"export USP_PROBE_MAP={_sh_quote((map_id or '').strip())}; "
    )
    inner = (
        f"printf '%s' {_sh_quote(b64)} | base64 -d > {_sh_quote(remote_py)} && "
        f"cd {_sh_quote(workdir)} && {env_exports} ({py_try})"
    )
    container = (docker_container or "").strip()
    if not container:
        return f"bash -lc {_sh_quote(inner)}"
    docker = "sudo docker" if docker_sudo else "docker"
    # 脚本写在宿主机 /tmp，再 docker cp 进容器执行（容器未必有宿主机 /tmp）
    host_tmp = "/tmp/usp_live_probe_ors.py"
    write_host = f"printf '%s' {_sh_quote(b64)} | base64 -d > {_sh_quote(host_tmp)}"
    copy_in = f"{docker} cp {_sh_quote(host_tmp)} {_sh_quote(container)}:{_sh_quote(remote_py)}"
    run_in = (
        f"{docker} exec -w {_sh_quote(workdir)} "
        f"-e USP_PROBE_PYTHONPATH={_sh_quote(workdir)} "
        f"-e USP_PROBE_ROBOT={_sh_quote((robot_id or '').strip())} "
        f"-e USP_PROBE_MAP={_sh_quote((map_id or '').strip())} "
        f"{_sh_quote(container)} bash -lc {_sh_quote(f'({py_try})')}"
    )
    return f"{write_host} && {copy_in} && {run_in}"


def summarize_probe(result: Dict[str, Any], robot_id: str = "") -> Dict[str, Any]:
    """从原始探测结果抽出缺环判定常用字段。"""
    out: Dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "error": result.get("error"),
        "source": result.get("source"),
        "notes": [],
    }
    if not result.get("ok"):
        if result.get("error"):
            out["notes"].append(f"现场探测失败：{result.get('error')}")
        return out

    focus = result.get("focus")
    robots = result.get("robots") or {}
    if not focus and robot_id:
        rid = robot_id.strip().lower()
        for k, v in robots.items():
            if str(k).lower() == rid or rid in str(k).lower():
                focus = v
                break
    if not focus and len(robots) == 1:
        focus = next(iter(robots.values()))

    out["robot_count"] = int(result.get("robot_count") or len(robots))
    out["focus"] = focus
    if not focus:
        out["notes"].append(
            f"现场探测成功，共 {out['robot_count']} 台车，但未匹配到目标车号"
            + (f" `{robot_id}`" if robot_id else "")
        )
        return out

    cts = (focus.get("currentTaskState") or {}) if isinstance(focus, dict) else {}
    map_id = ""
    if isinstance(focus, dict):
        map_id = (focus.get("map_id") or focus.get("mapId") or "") or ""
    path_id = cts.get("pathId")
    task_id = cts.get("taskId")
    node_id = cts.get("taskNodeId")
    rid = focus.get("robotId") if isinstance(focus, dict) else ""

    out["robot_id"] = rid
    out["map_id"] = map_id or ""
    out["task_id"] = "" if task_id in (None, "None") else str(task_id)
    out["task_node_id"] = "" if node_id in (None, "None") else str(node_id)
    out["path_id"] = None if path_id in (None, "None") else str(path_id)
    out["path_id_none"] = path_id is None or path_id == "None"
    out["can_move"] = focus.get("canMove") if isinstance(focus, dict) else None
    out["working_state"] = focus.get("workingState") if isinstance(focus, dict) else None
    out["has_cur_node"] = bool(focus.get("hasCurNode")) if isinstance(focus, dict) else False
    out["cur_node_id"] = focus.get("curNodeId") if isinstance(focus, dict) else None

    notes = [
        f"【现场 Ray 快照】车=`{rid}` map_id=`{map_id or '?'}` "
        f"workingState=`{out['working_state']}` canMove=`{out['can_move']}` "
        f"hasCurNode=`{out['has_cur_node']}` curNode=`{out['cur_node_id']}`"
    ]
    notes.append(
        f"currentTaskState: taskId=`{out['task_id'] or None}` "
        f"taskNodeId=`{out['task_node_id'] or None}` pathId=`{out['path_id']}`"
    )
    if out["path_id_none"] and (out["task_id"] or out["task_node_id"]):
        notes.append(
            "现场确认：有任务挂车但 pathId=None → 卡在等规划下发；"
            f"应优先查 TMS-MAP-{map_id or '*'}-USPA-LOGS"
        )
    elif out["path_id"]:
        notes.append(f"现场已有 pathId=`{out['path_id']}`（若监控仍显示规划中，查下发/收路径环）")
    if map_id:
        notes.append(f"map_id=`{map_id}` → 规划服务名 `TMS-MAP-{map_id}`")
    # 目标点
    for t in (focus.get("tasks") or [])[:1]:
        for n in (t.get("taskNodeList") or [])[:1]:
            notes.append(
                f"目标 pointId=`{n.get('pointId')}` nodeId=`{n.get('nodeId')}` "
                f"taskNode=`{n.get('id')}`"
            )
    out["notes"] = notes
    return out


async def probe_usp_robot_live(
    env_id: int,
    robot_id: str = "",
    map_id: str = "",
) -> Dict[str, Any]:
    """按环境 SSH 探测车态。失败不抛（返回 ok=False），由上层降级。"""
    try:
        cfg = await fetch_usp_env_ssh_config(int(env_id))
        if not cfg:
            return {"ok": False, "error": f"无法获取环境 SSH 配置 env_id={env_id}", "notes": []}

        host = (cfg.get("ssh_host") or "").strip()
        user = (cfg.get("ssh_user") or "").strip()
        workdir = (cfg.get("export_workdir") or "").strip()
        if not host or not user or not workdir:
            return {"ok": False, "error": "SSH 配置不完整（host/user/workdir）", "notes": []}

        auth_type = (cfg.get("ssh_auth_type") or "password").strip().lower()
        key_path = (cfg.get("ssh_private_key_path") or "").strip() if auth_type == "key" else ""
        password = (cfg.get("ssh_password") or "") if auth_type != "key" else ""
        timeout = float(cfg.get("ssh_connect_timeout_s") or 8.0)
        docker_container = (cfg.get("docker_container") or "").strip()
        docker_sudo = bool(cfg.get("docker_sudo", False))

        cmd = _build_remote_cmd(
            workdir=workdir,
            docker_container=docker_container,
            docker_sudo=docker_sudo,
            robot_id=robot_id,
            map_id=map_id,
        )
        with SshClient(
            host=host,
            port=int(cfg.get("ssh_port") or 22),
            username=user,
            password=password,
            private_key_path=key_path,
            connect_timeout=timeout,
        ) as ssh:
            logger.info(
                f"[usp_probe] env={env_id} robot={robot_id or '-'} "
                f"docker={docker_container or '-'} map={map_id or '-'}"
            )
            code, out, err = ssh.run(cmd, timeout=120.0)
            blob = f"{out}\n{err}"
            try:
                raw = _parse_probe_stdout(blob)
            except Exception as parse_e:
                return {
                    "ok": False,
                    "error": f"parse_failed code={code}: {parse_e}; stderr={err[:300]}",
                    "notes": [f"现场探测解析失败 code={code}"],
                    "stdout_tail": (out or "")[-400:],
                }
            summary = summarize_probe(raw, robot_id=robot_id)
            summary["raw_ok"] = bool(raw.get("ok"))
            summary["ray_addr"] = raw.get("ray_addr")
            summary["namespace"] = raw.get("namespace")
            if code != 0 and summary.get("ok"):
                # 脚本可能用备用 python 成功后仍有非 0（前几个 python 失败）
                pass
            logger.info(
                f"[usp_probe] env={env_id} ok={summary.get('ok')} "
                f"robot={summary.get('robot_id')} map={summary.get('map_id')} "
                f"path_none={summary.get('path_id_none')} "
                f"task={summary.get('task_id')} node={summary.get('task_node_id')}"
            )
            return summary
    except Exception as e:
        logger.warning(f"[usp_probe] env={env_id} 异常: {e}")
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "notes": [f"现场探测异常：{type(e).__name__}"],
        }


def prefer_logs_for_map(log_files: List[str], map_id: str = "") -> List[str]:
    """有 map_id 时把 TMS-MAP-{map_id} 提到 DYNAMIC_MAP 之后最前。"""
    if not log_files or not (map_id or "").strip():
        return list(log_files or [])
    mid = map_id.strip()
    needle = f"TMS-MAP-{mid}".upper()

    def rank(path: str) -> tuple:
        name = path.replace("\\", "/").split("/")[-1].upper()
        if "DYNAMIC_MAP" in name:
            return (0, name)
        if needle in name:
            return (1, name)
        if "TMS-MAP-" in name:
            return (2, name)
        if name.startswith("TMS-"):
            return (3, name)
        return (4, name)

    return sorted(log_files, key=rank)
