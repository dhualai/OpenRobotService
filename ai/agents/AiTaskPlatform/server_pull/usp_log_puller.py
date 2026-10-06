"""拉取 USP 算法日志：SSH（可选 docker exec）调 export_logs.sh → SFTP 拉回 → 解压。"""
from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.server_pull.env_client import fetch_usp_env_ssh_config
from ai.agents.AiTaskPlatform.server_pull.ssh_client import SshClient
from ai.agents.AiTaskPlatform.server_pull.timeutil import to_export_time_str

logger = get_logger("TASK_AGENT")

_ARTIFACT_RE = re.compile(r"algo_log_\d{12}_\d+min\.(?:zip|tar\.gz|tgz)", re.I)

_SERVICE_PRIORITY = (
    "DYNAMIC_MAP",
    "TMS-MAP-",
    "TMS-",
    "AI_map",
    "MapPreprocess",
    "TASK-MANAGER",
)

# 问题关键词 → 优先看的服务（命中则把对应服务提前）
_QUERY_SERVICE_HINTS = (
    (
        # 「路径规划中 / 不动」：手册 6.1 先 DYNAMIC_MAP（刷新+上轨），再 TMS
        ("路径规划中", "规划中", "规划卡住", "一直规划", "没有路径下发",
         "机器人不动", "车不动", "mapf", "下发路径", "路径下发"),
        ("DYNAMIC_MAP", "TMS-MAP-", "TMS-", "TASK-MANAGER", "AI_map", "MapPreprocess"),
    ),
    (
        ("不可达", "可达", "topo", "求解", "导航", "reach", "goal", "目标点", "无解"),
        # 地图专属 TMS（如 TMS-MAP-jingmen）常含该图求解；再 DYNAMIC_MAP / 通用 TMS
        ("TMS-MAP-", "DYNAMIC_MAP", "TMS-", "AI_map", "MapPreprocess", "TASK-MANAGER"),
    ),
    (
        ("路径", "规划", "path", "plan"),
        ("DYNAMIC_MAP", "TMS-MAP-", "TMS-", "AI_map", "MapPreprocess", "TASK-MANAGER"),
    ),
    (
        ("调度", "任务池", "充电", "休息点", "派车", "task-manager", "task_manager"),
        ("TASK-MANAGER", "DYNAMIC_MAP", "TMS-MAP-", "TMS-", "AI_map", "MapPreprocess"),
    ),
    (
        ("地图预处理", "preprocess"),
        ("MapPreprocess", "DYNAMIC_MAP", "TMS-MAP-", "TMS-", "AI_map", "TASK-MANAGER"),
    ),
)


def _sh_quote(s: str) -> str:
    return "'" + (s or "").replace("'", "'\"'\"'") + "'"


def _pick_log_files(root: Path) -> List[str]:
    logs = [p for p in root.rglob("*.log") if p.is_file()]
    if not logs:
        return []

    def rank(p: Path) -> tuple:
        name = p.name
        for i, key in enumerate(_SERVICE_PRIORITY):
            if key in name:
                return (i, name)
        return (len(_SERVICE_PRIORITY), name)

    logs.sort(key=rank)
    return [str(p) for p in logs]


def select_log_for_query(
    log_files: List[str],
    query: str = "",
    prefer_map_id: str = "",
) -> List[str]:
    """按用户问题重排日志文件；「不可达/路径/求解」优先 DYNAMIC_MAP/TMS，避免先读 TASK-MANAGER 心跳。

    prefer_map_id：现场探测拿到 map_id 时，把 TMS-MAP-{map_id} 提到对应优先级最前。
    """
    if not log_files:
        return []
    q = (query or "").lower()
    priority = list(_SERVICE_PRIORITY)
    for keywords, order in _QUERY_SERVICE_HINTS:
        if any(k.lower() in q for k in keywords):
            priority = list(order)
            break

    map_needle = ""
    mid = (prefer_map_id or "").strip()
    if mid:
        map_needle = f"TMS-MAP-{mid}".upper()

    def rank(path: str) -> tuple:
        name = Path(path).name
        blob = path.replace("\\", "/").upper()
        # 精确地图 Actor：目录名 TMS-MAP-{map_id} 也可命中（上传 zip 内常叫 debug_logs.log）
        if map_needle and map_needle in blob:
            for i, key in enumerate(priority):
                ku = key.upper()
                if ku == "TMS-MAP-" or ku.startswith("TMS-MAP"):
                    return (i, -1, name)
                if ku in blob:
                    return (i, -1, name)
            return (0, -1, name)
        for i, key in enumerate(priority):
            if key.upper() in blob:
                return (i, 0, name)
        return (len(priority), 0, name)

    return sorted(log_files, key=rank)


def _extract_archive(archive: Path, dest: Path) -> None:
    lower = archive.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest)
        return
    if lower.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:*") as tf:
            tf.extractall(dest)
        return
    raise ValueError(f"不支持的产物格式: {archive.name}")


def _build_export_commands(
    *,
    script: str,
    workdir: str,
    time_str: str,
    interval: int,
    docker_container: str = "",
    docker_sudo: bool = False,
) -> Tuple[str, str]:
    """返回 (远程执行命令, 宿主机上产物所在目录)。

    配了 docker_container 时：在容器内跑脚本，再 docker cp 到宿主机 workdir。
    """
    expected_stem = f"algo_log_{time_str}_{interval}min"
    container = (docker_container or "").strip()
    if not container:
        cmd = (
            f"cd {_sh_quote(workdir)} && "
            f"bash {_sh_quote(script)} {time_str} --interval {interval}"
        )
        return cmd, workdir

    docker = "sudo docker" if docker_sudo else "docker"
    # 容器内执行；产物落在容器 workdir，再拷到宿主机同名目录
    # 用 bash -lc 拼路径，避免宿主机没有该容器路径
    inner = (
        f"cd {_sh_quote(workdir)} && "
        f"bash {_sh_quote(script)} {time_str} --interval {interval}"
    )
    # 拷贝常见产物名；哪个存在拷哪个
    copy_bits = []
    for ext in (".zip", ".tar.gz", ".tgz"):
        name = f"{expected_stem}{ext}"
        src = f"{workdir.rstrip('/')}/{name}"
        copy_bits.append(
            f"{docker} cp {_sh_quote(container + ':' + src)} {_sh_quote(workdir + '/' + name)} 2>/dev/null || true"
        )
    cmd = (
        f"mkdir -p {_sh_quote(workdir)} && "
        f"{docker} exec -w {_sh_quote(workdir)} {_sh_quote(container)} bash -lc {_sh_quote(inner)} && "
        + " ; ".join(copy_bits)
    )
    return cmd, workdir


async def pull_recent_usp_logs(
    env_id: int,
    occurrence_time: str = "",
    minutes: Optional[int] = None,
) -> Tuple[List[str], List[str]]:
    """按环境 ID 拉取最近算法日志。返回 (log_files, tmp_dirs)。失败抛异常。"""
    cfg = await fetch_usp_env_ssh_config(int(env_id))
    if not cfg:
        raise RuntimeError(f"无法获取环境 SSH 配置 env_id={env_id}")

    host = (cfg.get("ssh_host") or "").strip()
    user = (cfg.get("ssh_user") or "").strip()
    script = (cfg.get("export_script") or "").strip()
    workdir = (cfg.get("export_workdir") or "").strip()
    if not host or not user or not script or not workdir:
        raise RuntimeError("SSH 配置不完整（host/user/script/workdir）")

    interval = int(minutes if minutes is not None else (cfg.get("log_interval_min") or 15))
    time_str = to_export_time_str(occurrence_time)
    auth_type = (cfg.get("ssh_auth_type") or "password").strip().lower()
    key_path = (cfg.get("ssh_private_key_path") or "").strip() if auth_type == "key" else ""
    password = (cfg.get("ssh_password") or "") if auth_type != "key" else ""
    timeout = float(cfg.get("ssh_connect_timeout_s") or 8.0)
    docker_container = (cfg.get("docker_container") or "").strip()
    docker_sudo = bool(cfg.get("docker_sudo", False))

    cmd, host_workdir = _build_export_commands(
        script=script,
        workdir=workdir,
        time_str=time_str,
        interval=interval,
        docker_container=docker_container,
        docker_sudo=docker_sudo,
    )
    expected_stem = f"algo_log_{time_str}_{interval}min"

    tmp_root = Path(tempfile.mkdtemp(prefix="usp_pull_"))
    tmp_dirs = [str(tmp_root)]
    try:
        with SshClient(
            host=host,
            port=int(cfg.get("ssh_port") or 22),
            username=user,
            password=password,
            private_key_path=key_path,
            connect_timeout=timeout,
        ) as ssh:
            logger.info(
                f"[usp_pull] env={env_id} docker={docker_container or '-'} "
                f"sudo={docker_sudo} time={time_str}"
            )
            code, out, err = ssh.run(cmd, timeout=180.0)
            if code != 0:
                raise RuntimeError(
                    f"export_logs 失败 code={code} stderr={err[:400]} stdout={out[:200]}"
                )
            blob = f"{out}\n{err}"
            m = _ARTIFACT_RE.search(blob)
            candidates = []
            if m:
                candidates.append(m.group(0))
            for ext in (".zip", ".tar.gz", ".tgz"):
                name = f"{expected_stem}{ext}"
                if name not in candidates:
                    candidates.append(name)

            archive: Optional[Path] = None
            last_err = ""
            for remote_name in candidates:
                local_path = tmp_root / Path(remote_name).name
                remote_path = f"{host_workdir.rstrip('/')}/{remote_name}"
                try:
                    ssh.download(remote_path, str(local_path))
                    archive = local_path
                    break
                except Exception as e:
                    last_err = str(e)
                    if local_path.exists():
                        local_path.unlink(missing_ok=True)
            if archive is None:
                raise RuntimeError(f"未找到 export_logs 产物: {last_err or candidates}")

        extract_dir = tmp_root / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _extract_archive(archive, extract_dir)
        log_files = _pick_log_files(extract_dir)
        if not log_files:
            raise RuntimeError("解压后未找到 .log 文件")
        logger.info(
            f"[usp_pull] env={env_id} time={time_str} interval={interval} "
            f"logs={len(log_files)} first={Path(log_files[0]).name}"
        )
        return log_files, tmp_dirs
    except Exception:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        raise


def _safe_log_needle(s: str) -> str:
    """只允许文件名检索用的安全片段。"""
    out = re.sub(r"[^A-Za-z0-9_\-]", "", s or "")
    return out[:80]


async def pull_usp_logs_matching(
    env_id: int,
    name_contains: str,
) -> Tuple[List[str], List[str]]:
    """在 USP 机上按文件名片段定向拉日志（二次补拉 TMS-MAP-{map_id}）。

    不重跑 export_logs.sh：首次拉包常只有通用 TMS。在 /usp_algorithm_logs 等目录
    找匹配 .log，SFTP / docker cp 回来。找不到则返回空列表，不抛。
    """
    needle = _safe_log_needle(name_contains)
    if not needle or "TMS-MAP" not in needle.upper():
        return [], []

    cfg = await fetch_usp_env_ssh_config(int(env_id))
    if not cfg:
        logger.warning(f"[usp_refeed] 无法获取 SSH 配置 env_id={env_id}")
        return [], []

    host = (cfg.get("ssh_host") or "").strip()
    user = (cfg.get("ssh_user") or "").strip()
    workdir = (cfg.get("export_workdir") or "").strip()
    if not host or not user:
        return [], []

    auth_type = (cfg.get("ssh_auth_type") or "password").strip().lower()
    key_path = (cfg.get("ssh_private_key_path") or "").strip() if auth_type == "key" else ""
    password = (cfg.get("ssh_password") or "") if auth_type != "key" else ""
    timeout = float(cfg.get("ssh_connect_timeout_s") or 8.0)
    docker_container = (cfg.get("docker_container") or "").strip()
    docker_sudo = bool(cfg.get("docker_sudo", False))
    docker = "sudo docker" if docker_sudo else "docker"

    find_roots = " ".join(
        _sh_quote(p)
        for p in (
            "/usp_algorithm_logs",
            workdir or "",
            "/home/ubuntu/usp",
            "/opt/usp",
        )
        if p
    )
    find_cmd = (
        f"find {find_roots} -type f "
        f"\\( -name '*.log' -o -name '*.log.*' -o -name 'debug_logs*' \\) "
        f"2>/dev/null | grep -i {_sh_quote(needle)} | head -20"
    )

    tmp_root = Path(tempfile.mkdtemp(prefix="usp_refeed_"))
    tmp_dirs = [str(tmp_root)]
    found: List[str] = []
    try:
        with SshClient(
            host=host,
            port=int(cfg.get("ssh_port") or 22),
            username=user,
            password=password,
            private_key_path=key_path,
            connect_timeout=timeout,
        ) as ssh:
            logger.info(
                f"[usp_refeed] env={env_id} needle={needle} "
                f"docker={docker_container or '-'}"
            )
            code, out, err = ssh.run(find_cmd, timeout=30.0)
            host_hits = [
                ln.strip()
                for ln in (out or "").splitlines()
                if ln.strip().startswith("/") and "TMS-MAP" in ln.upper()
            ]
            for remote in host_hits[:8]:
                local = tmp_root / Path(remote).name
                if local.suffix.lower() != ".log":
                    local = tmp_root / (Path(remote).name.replace("/", "_") + ".log")
                try:
                    ssh.download(remote, str(local))
                    if local.is_file() and local.stat().st_size > 0:
                        found.append(str(local))
                except Exception as e:
                    logger.info(f"[usp_refeed] 宿主机下载失败 {remote}: {e}")

            if not found and docker_container:
                inner = (
                    "find /usp_algorithm_logs /var/log /logs "
                    + (f"{_sh_quote(workdir)} " if workdir else "")
                    + "-type f \\( -name '*.log' -o -name 'debug_logs*' \\) "
                    f"2>/dev/null | grep -i {_sh_quote(needle)} | head -20"
                )
                dcmd = (
                    f"{docker} exec {_sh_quote(docker_container)} "
                    f"bash -lc {_sh_quote(inner)}"
                )
                dcode, dout, derr = ssh.run(dcmd, timeout=30.0)
                c_hits = [
                    ln.strip()
                    for ln in (dout or "").splitlines()
                    if ln.strip().startswith("/") and "TMS-MAP" in ln.upper()
                ]
                for remote in c_hits[:8]:
                    staging = f"/tmp/_ors_refeed_{Path(remote).name}"
                    cp = (
                        f"{docker} cp {_sh_quote(docker_container + ':' + remote)} "
                        f"{_sh_quote(staging)}"
                    )
                    ccode, _, cerr = ssh.run(cp, timeout=30.0)
                    if ccode != 0:
                        logger.info(f"[usp_refeed] docker cp 失败 {remote}: {cerr[:200]}")
                        continue
                    local = tmp_root / Path(remote).name
                    try:
                        ssh.download(staging, str(local))
                        if local.is_file() and local.stat().st_size > 0:
                            found.append(str(local))
                    except Exception as e:
                        logger.info(f"[usp_refeed] 容器文件下载失败 {remote}: {e}")

        logger.info(
            f"[usp_refeed] env={env_id} needle={needle} files={len(found)} "
            f"names={[Path(p).name for p in found[:4]]}"
        )
        if not found:
            shutil.rmtree(tmp_root, ignore_errors=True)
            return [], []
        return found, tmp_dirs
    except Exception as e:
        logger.warning(f"[usp_refeed] env={env_id} 异常: {e}")
        shutil.rmtree(tmp_root, ignore_errors=True)
        return [], []
