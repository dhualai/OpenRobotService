#!/usr/bin/env python3
"""
OpenRobotService 一键部署脚本（前端 + 后端 + 算法）。

默认以图形界面启动：
  python deploy.py                       # 无参数即打开 GUI
  python deploy.py --gui                 # 显式启动 GUI
带参数则进入命令行（CLI）模式：
  python deploy.py -e test -c all --ssh-host 10.0.0.1

功能：
  - 本地构建前端 (npm run build:test / build:prod)，未安装依赖时自动先 npm install
  - 上传 dist 内容到 nginx html 目录
  - 上传 backend/app，重启 supervisor 后端服务
  - 上传 ai 代码（忽略 run.py），重启 supervisor 算法服务
  - 部署前自动备份远端现状，失败可一键回滚（--list-backups / --rollback / --no-backup）
使用 tar 打包 + scp 上传 + ssh 远程执行，兼容 Windows 10+ 自带 OpenSSH 与 bsdtar。

敏感信息说明：
  sudo 密码等连接配置不再硬编码于脚本，而是保存在用户本地配置文件
  (~/.openrobotservice/deploy_config.json)，该文件位于用户主目录、不在此仓库内，
  因此不会被提交到云端仓库。

依赖：系统 PATH 中需有 tar、scp、ssh、npm。仅使用 Python 标准库（含 tkinter）。
"""
import argparse
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

# 防御性修复：Windows 控制台默认 GBK 编码，无法输出 vite 构建日志中的 ✓（\u2713）等非 GBK
# 字符，会导致 print 抛 UnicodeEncodeError 崩溃（前端部署时踩过，脚本构建到一半中断）。
# 这里强制 stdout/stderr 用 UTF-8 输出、无法编码的字符替换为 '?'，避免因控制台编码中断部署。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
    _HAS_TK = True
except ImportError:  # 极少数精简环境可能无 tkinter
    _HAS_TK = False


# ====================== 静态默认值（非敏感连接信息） ======================
# 这里仅保留非敏感的连接默认值以便首次使用时表单预填；敏感的 sudo 密码不写入脚本，
# 而是保存到下方本地配置文件，由用户在界面填写。
DEFAULTS = {
    "ssh_host": "",              # 默认服务器地址
    "ssh_user": "",                       # 默认 SSH 用户名
    "ssh_port": 80,                          # 默认 SSH 端口
    "ssh_identity": r"",  # 默认私钥路径
    # sudo_password 不在此处，避免硬编码敏感信息
}

# 本地配置文件（位于用户主目录，不在此仓库内，不会被提交到云端）
CONFIG_DIR = Path.home() / ".openrobotservice"
CONFIG_FILE = CONFIG_DIR / "deploy_config.json"


def load_user_config():
    """读取本地配置文件，返回 dict（不存在或损坏时返回空 dict）。"""
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_user_config(cfg_dict):
    """保存配置到本地文件（含 sudo 密码），并尝试限制文件权限。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg_dict, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)  # Windows 上作用有限，但加一层无害
    except OSError:
        pass


def effective_defaults():
    """合并：DEFAULTS < 本地配置文件（文件优先）。"""
    merged = dict(DEFAULTS)
    merged.update(load_user_config())
    return merged


def default_project_path():
    """默认项目根：脚本位于 deploy/ 子目录，项目根为其上一级。
    打包为 exe 后无脚本目录概念，改用可执行文件所在目录作为起点
    （用户应在界面配置真实项目路径，配置会保存到本地）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ---------- 输出辅助 ----------
class Colors:
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    DARKGRAY = "\033[90m"
    RESET = "\033[0m"


_TAG_COLORS = {
    "step": Colors.CYAN,
    "ok": Colors.GREEN,
    "err": Colors.RED,
    "info": Colors.DARKGRAY,
    "cmd": "",  # 子进程原始输出，不着色
}

# 输出 sink：None 表示打印到 stdout；GUI 启动时设为回调以写入日志区。
_output_handler = None


def _supports_color():
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


_COLOR = _supports_color()


def _c(text, color):
    if not _COLOR or not color:
        return text
    return f"{color}{text}{Colors.RESET}"


def _emit(text, tag):
    """统一输出入口：CLI 打印到 stdout（带色），GUI 路由到日志区。"""
    if _output_handler is None:
        print(_c(text, _TAG_COLORS.get(tag, "")))
    else:
        _output_handler(text, tag)


def write_step(msg):
    _emit(f"\n[*] {msg}", "step")


def write_ok(msg):
    _emit(f"    [OK] {msg}", "ok")


def write_err(msg):
    _emit(f"    [ERR] {msg}", "err")


def write_info(msg):
    _emit(f"    ->  {msg}", "info")


def test_command(name):
    return shutil.which(name) is not None


def _run(cmd, cwd=None, input_bytes=None):
    """运行子进程，合并 stdout/stderr 逐行流式输出到日志 sink。返回 returncode。

    cmd 为 list 时 shell=False（用于 tar/scp/ssh 等可执行文件）；
    cmd 为 str 时 shell=True（用于 npm 等需经由 shell 解释的 .cmd 脚本，跨平台兼容）。
    input_bytes 不为 None 时先写入 stdin 再读取输出（用于 sudo -S 免交互喂密码）。
    """
    shell = isinstance(cmd, str)
    stdin = subprocess.PIPE if input_bytes is not None else None
    proc = subprocess.Popen(
        cmd, cwd=cwd, shell=shell,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=stdin,
    )
    if input_bytes is not None and proc.stdin is not None:
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    if proc.stdout is not None:
        for raw in iter(proc.stdout.readline, b""):
            _emit(raw.decode("utf-8", "replace").rstrip("\n"), "cmd")
    proc.wait()
    return proc.returncode


# ---------- SSH 配置 ----------
@dataclass
class SshConfig:
    host: str
    user: str
    port: int
    identity: str
    sudo_password: str
    dry_run: bool = False
    no_sudo: bool = False      # True 时 supervisorctl 不加 sudo（CI 走 supervisor 组权限）
    remote_tmp: str = "~/tmp"  # 远端暂存目录（避开 /tmp 的 sticky/root 权限限制；scp 展开 ~，bash 侧转 $HOME）

    def target(self):
        return f"{self.user}@{self.host}"

    def ssh_args(self, tool):
        """生成 ssh/scp 通用参数。注意：ssh 端口参数 -p，scp 端口参数 -P。"""
        args = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
        if tool == "ssh":
            args += ["-p", str(self.port)]
        else:
            args += ["-P", str(self.port)]
        if self.identity:
            args += ["-i", self.identity]
        return args


def invoke_remote_cmd(cfg: SshConfig, command, *, sudo=False):
    """通过 ssh 在远程执行命令。

    -Sudo: 命令需要 sudo 权限。若配置了 SudoPassword，则将远程命令改写为
           sudo -S 从 stdin 读取密码（免交互，密码不经命令行暴露）；
           未配置则回退为 ssh -t 分配 TTY 交互式输入密码（仅 CLI 适用）。
    """
    use_stdin_pwd = sudo and cfg.sudo_password
    if use_stdin_pwd:
        # sudo supervisorctl ... -> sudo -S -p '' supervisorctl ...
        command = re.sub(r"(^|\s)sudo\s+", r"\1sudo -S -p '' ", command)

    ssh_args = []
    if sudo and not use_stdin_pwd:
        ssh_args.append("-t")
    ssh_args += cfg.ssh_args("ssh")
    ssh_args += [cfg.target(), command]

    if cfg.dry_run:
        write_info(f"[dryrun] ssh {' '.join(ssh_args)}")
        return

    input_bytes = (cfg.sudo_password + "\n").encode() if use_stdin_pwd else None
    rc = _run(["ssh"] + ssh_args, input_bytes=input_bytes)
    if rc != 0:
        raise RuntimeError(f"远程命令执行失败: {command}")


def _run_capture(cmd, input_bytes=None, label=""):
    """运行子进程：逐行流式输出到日志，同时累积并返回 stdout 文本。失败抛 RuntimeError。"""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.PIPE if input_bytes is not None else None)
    chunks = []
    if input_bytes is not None and proc.stdin is not None:
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    if proc.stdout is not None:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\n")
            chunks.append(line)
            _emit(line, "cmd")
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"{label or '远程命令'}执行失败（exit={proc.returncode}）")
    return "\n".join(chunks)


def run_remote_script(cfg: SshConfig, script, *, label="远程脚本"):
    """把多行脚本经 stdin 送达远端 bash 执行（规避命令行引号转义问题）。

    返回远端 stdout 文本；同时流式打印到日志。失败抛 RuntimeError。
    """
    ssh_args = cfg.ssh_args("ssh") + [cfg.target(), "bash -s"]
    if cfg.dry_run:
        write_info(f"[dryrun] ssh {' '.join(ssh_args)} <<'EOS'")
        for line in script.splitlines():
            write_info(f"[dryrun] | {line}")
        return ""
    return _run_capture(["ssh"] + ssh_args,
                        input_bytes=script.encode("utf-8"), label=label)


def restart_supervisor(cfg: SshConfig, service: str):
    """重启 supervisor 服务：默认 sudo；no_sudo 时直接用 supervisor 组 socket 权限。"""
    if cfg.no_sudo:
        write_info(f"重启 supervisor 服务: {service}（免 sudo）")
        invoke_remote_cmd(cfg, f"supervisorctl restart {service} && echo RESTART_DONE")
    else:
        write_info(f"重启 supervisor 服务: {service}")
        invoke_remote_cmd(cfg,
                          f"sudo supervisorctl restart {service} && echo RESTART_DONE",
                          sudo=True)


_REMOTE_PATH_RE = re.compile(r"^(~|/)[A-Za-z0-9._/~-]*$")


def validate_remote_path(path, label="远端路径"):
    """校验远端路径：须以 / 或 ~ 开头且只含安全字符（防路径穿越与命令注入）。"""
    p = (path or "").strip().rstrip("/")
    if not p or ".." in p or not _REMOTE_PATH_RE.match(p):
        raise RuntimeError(f"{label}非法: {path!r}（须以 / 或 ~ 开头，不含 .. 与特殊字符）")
    return p


def to_bash_path(path):
    """把 scp 形式的远端路径转成 bash 可用形式。

    scp 会展开 ~ 为家目录，但 bash 双引号内 ~ 不展开，需替换为 $HOME。
    """
    if path == "~":
        return "$HOME"
    if path.startswith("~/"):
        return "$HOME/" + path[2:]
    return path


def send_tarball(cfg: SshConfig, local_tar, remote_name):
    """将本地 tar 包 scp 到远端暂存目录，返回 bash 可用的远端 tar 路径。

    说明：
    - 暂存目录由 --remote-tmp 指定（默认 ~/tmp），避开 /tmp 或 /data/tmp 的
      sticky/root 所有权权限问题；
    - scp 目标用 ~ 形式（scp 会展开），bash 命令用 $HOME 形式（双引号内 ~ 不展开）。
    """
    scp_dir = validate_remote_path(cfg.remote_tmp, "远端暂存目录")
    bash_dir = to_bash_path(scp_dir)
    invoke_remote_cmd(cfg, f"mkdir -p {bash_dir}")

    scp_args = cfg.ssh_args("scp") + [local_tar, f"{cfg.target()}:{scp_dir}/{remote_name}"]
    if cfg.dry_run:
        write_info(f"[dryrun] scp {' '.join(scp_args)}")
        return f"{bash_dir}/{remote_name}"
    rc = _run(["scp"] + scp_args)
    if rc != 0:
        raise RuntimeError(f"scp 上传失败: {local_tar}")
    return f"{bash_dir}/{remote_name}"


def new_local_tar(source_dir, paths, excludes=None, dry_run=False):
    """生成本地 tar.gz：-C 指定源目录，后续参数为要打包的内容。"""
    source_dir = str(source_dir)
    if not os.path.isdir(source_dir):
        raise RuntimeError(f"源目录不存在: {source_dir}")
    tarball = os.path.join(tempfile.gettempdir(),
                           f"ors_deploy_{random.randint(0, 1 << 30)}.tar.gz")
    tar_args = ["-czf", tarball, "-C", source_dir]
    for ex in (excludes or []):
        tar_args += ["--exclude", ex]
    tar_args += paths
    if dry_run:
        write_info(f"[dryrun] tar {' '.join(tar_args)}")
        return tarball
    rc = _run(["tar"] + tar_args)
    if rc != 0:
        raise RuntimeError(f"tar 打包失败: {source_dir}")
    return tarball


# ---------- 环境配置 ----------
def get_env_config(environment):
    """远端路径 / supervisor 服务名 / conda 环境按环境区分。"""
    if environment == "test":
        return {
            "RemoteBase": "/data/apps/TestOpenRobotService",
            "NginxHtml": "/data/apps/TestOpenRobotService/nginx/html/test",
            "BackendRemote": "/data/apps/TestOpenRobotService/backend",
            "AiRemote": "/data/apps/TestOpenRobotService/ai",
            "CondaEnv": "test-ai",
            "SupBackend": "test-open-robot",
            "SupAi": "test-open-robot-ai",
            "NpmScript": "build:test",
        }
    else:  # prod
        return {
            "RemoteBase": "/data/apps/OpenRobotService",
            "NginxHtml": "/data/apps/OpenRobotService/nginx/html/prod",
            "BackendRemote": "/data/apps/OpenRobotService/backend",
            "AiRemote": "/data/apps/OpenRobotService/ai",
            "CondaEnv": "/data/workspace/ai",
            "SupBackend": "openrobot",
            "SupAi": "openrobotAI",
            "NpmScript": "build:prod",
        }


# ---------- 各组件部署 ----------
def deploy_frontend(cfg: SshConfig, repo_root: Path, env: dict, skip_build: bool):
    write_step("【前端】构建与上传")

    frontend_dir = repo_root / "frontend"
    dist_dir = frontend_dir / "dist"

    if not skip_build:
        node_modules = frontend_dir / "node_modules"
        if not node_modules.exists():
            write_info(f"未检测到 node_modules，先执行 npm install (在 {frontend_dir})")
            if not cfg.dry_run:
                rc = _run("npm install", cwd=str(frontend_dir))
                if rc != 0:
                    raise RuntimeError("npm install 失败")
        write_info(f"执行 npm run {env['NpmScript']} (在 {frontend_dir})")
        if not cfg.dry_run:
            rc = _run(f"npm run {env['NpmScript']}", cwd=str(frontend_dir))
            if rc != 0:
                raise RuntimeError("前端构建失败")
    else:
        write_info("已跳过构建，使用现有 dist")

    if not dist_dir.exists():
        raise RuntimeError(f"前端 dist 目录不存在: {dist_dir}（请先构建或去掉 --skip-build）")
    write_ok(f"前端产物目录: {dist_dir}")

    tarball = new_local_tar(dist_dir, ["."], dry_run=cfg.dry_run)
    remote_tar = send_tarball(cfg, tarball, "frontend_dist.tar.gz")

    nginx_html = env["NginxHtml"]
    extract_cmd = (
        f'mkdir -p "{nginx_html}" && rm -rf "$HOME/tmp/_ors_extract/frontend" '
        f'&& mkdir -p "$HOME/tmp/_ors_extract/frontend" '
        f'&& tar -xzf "{remote_tar}" -C "$HOME/tmp/_ors_extract/frontend" -m --no-same-permissions --no-same-owner '
        f'&& rm -rf "{nginx_html}"/* '
        f'&& cp -rf "$HOME/tmp/_ors_extract/frontend/." "{nginx_html}/" '
        f'&& rm -rf "$HOME/tmp/_ors_extract/frontend" '
        f'&& rm -f "{remote_tar}" && echo FRONTEND_DONE'
    )
    write_info(f"远端解压到 {nginx_html}")
    invoke_remote_cmd(cfg, extract_cmd)
    write_ok("前端部署完成")

    if tarball and os.path.exists(tarball):
        try:
            os.remove(tarball)
        except OSError:
            pass


def deploy_backend(cfg: SshConfig, repo_root: Path, env: dict, clean_remote: bool):
    write_step("【后端】上传 app 与 main.py 并重启")

    backend_dir = repo_root / "backend"
    excludes = ["__pycache__", "*.pyc", "*.pyo",
                ".pytest_cache", ".mypy_cache", ".ruff_cache"]
    tarball = new_local_tar(backend_dir, ["app", "main.py"], excludes=excludes, dry_run=cfg.dry_run)
    remote_tar = send_tarball(cfg, tarball, "backend_app.tar.gz")

    backend_remote = env["BackendRemote"]
    clean_cmd = f'rm -rf "{backend_remote}/app" && ' if clean_remote else ""
    extract_cmd = (
        f'mkdir -p "{backend_remote}" && rm -rf "$HOME/tmp/_ors_extract/backend" '
        f'&& mkdir -p "$HOME/tmp/_ors_extract/backend" '
        f'&& tar -xzf "{remote_tar}" -C "$HOME/tmp/_ors_extract/backend" -m --no-same-permissions --no-same-owner '
        f'&& {clean_cmd}'
        f'cp -rf "$HOME/tmp/_ors_extract/backend/." "{backend_remote}/" '
        f'&& rm -rf "$HOME/tmp/_ors_extract/backend" '
        f'&& rm -f "{remote_tar}" && echo BACKEND_UPLOAD_DONE'
    )
    write_info(f"远端解压到 {backend_remote}")
    invoke_remote_cmd(cfg, extract_cmd)
    write_ok("后端代码上传完成")

    restart_supervisor(cfg, env["SupBackend"])
    write_ok("后端部署完成")

    if tarball and os.path.exists(tarball):
        try:
            os.remove(tarball)
        except OSError:
            pass


def deploy_ai(cfg: SshConfig, repo_root: Path, env: dict):
    write_step("【算法】上传 ai 代码并重启")

    ai_dir = repo_root / "ai"
    # 排除虚拟环境、向量库、模型、缓存、测试、本地数据等
    excludes = [
        "__pycache__", "*.pyc", "*.pyo",
        ".venv", "venv", "env",
        ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "kb", "embed_models", "docs", "tests", "tools", "uploads",
        ".env", ".env.*", "*.log", "logs", "*.sqlite3", "*.db",
        ".git", ".idea", ".vscode",
    ]
    tarball = new_local_tar(ai_dir, ["."], excludes=excludes, dry_run=cfg.dry_run)
    remote_tar = send_tarball(cfg, tarball, "ai_code.tar.gz")

    ai_remote = env["AiRemote"]
    extract_cmd = (
        f'mkdir -p "{ai_remote}" && rm -rf "$HOME/tmp/_ors_extract/ai" '
        f'&& mkdir -p "$HOME/tmp/_ors_extract/ai" '
        f'&& tar -xzf "{remote_tar}" -C "$HOME/tmp/_ors_extract/ai" -m --no-same-permissions --no-same-owner '
        f'&& cp -rf "$HOME/tmp/_ors_extract/ai/." "{ai_remote}/" '
        f'&& rm -rf "$HOME/tmp/_ors_extract/ai" '
        f'&& rm -f "{remote_tar}" && echo AI_UPLOAD_DONE'
    )
    write_info(f"远端解压到 {ai_remote}")
    invoke_remote_cmd(cfg, extract_cmd)
    write_ok("算法代码上传完成")

    restart_supervisor(cfg, env["SupAi"])
    write_ok("算法部署完成")

    if tarball and os.path.exists(tarball):
        try:
            os.remove(tarball)
        except OSError:
            pass


# ---------- 组件列表解析 ----------
def parse_components(components):
    """支持逗号/分号/空格分隔，如 ["backend,ai"] / ["backend ai"] / ["all"]。"""
    valid = ["frontend", "backend", "ai", "all"]
    joined = " ".join(components)
    parts = [p.strip() for p in re.split(r"[,;\s]+", joined) if p.strip()]
    if not parts:
        parts = ["all"]
    for c in parts:
        if c not in valid:
            raise RuntimeError(f"无效组件: '{c}'（允许: {', '.join(valid)}）")
    if "all" in parts:
        parts = ["frontend", "backend", "ai"]
    return parts


# ====================== 备份 / 回滚 ======================
# 远端备份结构：$HOME/deploy_backups/{env}/{backup_id}/
#   ├── frontend.tar.gz / backend.tar.gz / ai.tar.gz（按备份的组件存在）
#   └── manifest.json（含提交号、时间、组件与各包大小/校验和）
# 仅含 manifest.json 的目录才被视为「有效备份」，可被 --list-backups 列出与回滚。
BACKUP_ROOT = "deploy_backups"
BACKUP_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
BACKUP_KEEP_DEFAULT = 10

# 备份时排除的大目录/易变内容：与上传时的排除表同源，避免备份体积失控
# （ai/kb、ai/embed_models、日志、虚拟环境等不属于「代码」，无需备份）
_AI_EXCLUDES = [
    "__pycache__", "*.pyc", "*.pyo",
    ".venv", "venv", "env",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "kb", "embed_models", "docs", "tests", "tools", "uploads",
    ".env", ".env.*", "*.log", "logs", "*.sqlite3", "*.db",
    ".git", ".idea", ".vscode",
]
_BACKEND_EXCLUDES = ["__pycache__", "*.pyc", "*.pyo",
                     ".pytest_cache", ".mypy_cache", ".ruff_cache"]


def make_backup_id():
    """备份 id：时间戳 + 随机短哈希（白名单格式，可安全拼进远端命令）。"""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def validate_backup_id(backup_id):
    """校验备份 id 格式，防路径穿越 / 命令注入。"""
    bid = (backup_id or "").strip()
    if not BACKUP_ID_RE.match(bid):
        raise RuntimeError(
            f"备份 id 非法: {backup_id!r}（应形如 20260923-153000-ab12cd）")
    return bid


def _tar_exclude_args(excludes):
    """排除项转 tar 参数；用 shlex 加引号，避免远端 shell 展开 *.pyc 等通配符。"""
    return " ".join(f"--exclude={shlex.quote(e)}" for e in excludes)


def _backup_script(environment, env, components, backup_id):
    """生成远端备份脚本（路径全部来自环境常量；backup_id 已通过白名单校验）。"""
    lines = [
        "set -euo pipefail",
        f'BK="$HOME/{BACKUP_ROOT}/{environment}/{backup_id}"',
        'mkdir -p "$BK"',
        "emit_bkfile() {",
        "  printf 'BKFILE\\t%s\\t%s\\t%s\\n' \"$1\" \"$(stat -c %s \"$BK/$1\")\""
        " \"$(sha256sum \"$BK/$1\" | cut -d' ' -f1)\"",
        "}",
    ]
    if "frontend" in components:
        html = env["NginxHtml"]
        lines += [
            f'HTML="{html}"',
            'if [ -d "$HTML" ] && [ -n "$(ls -A "$HTML" 2>/dev/null)" ]; then',
            '  tar -czf "$BK/frontend.tar.gz" -C "$HTML" .',
            '  emit_bkfile frontend.tar.gz',
            'else',
            '  echo "BACKUP_SKIP frontend 远端目录为空或不存在"',
            'fi',
        ]
    if "backend" in components:
        be = env["BackendRemote"]
        lines += [
            f'BE="{be}"',
            'BK_ITEMS=""',
            'if [ -d "$BE/app" ]; then BK_ITEMS="$BK_ITEMS app"; fi',
            'if [ -f "$BE/main.py" ]; then BK_ITEMS="$BK_ITEMS main.py"; fi',
            'if [ -n "$BK_ITEMS" ]; then',
            f'  tar -czf "$BK/backend.tar.gz" -C "$BE" '
            f'{_tar_exclude_args(_BACKEND_EXCLUDES)} $BK_ITEMS',
            '  emit_bkfile backend.tar.gz',
            'else',
            '  echo "BACKUP_SKIP backend 远端代码不存在"',
            'fi',
        ]
    if "ai" in components:
        ai = env["AiRemote"]
        lines += [
            f'AI="{ai}"',
            'if [ -d "$AI" ]; then',
            f'  tar -czf "$BK/ai.tar.gz" -C "$AI" '
            f'{_tar_exclude_args(_AI_EXCLUDES)} .',
            '  emit_bkfile ai.tar.gz',
            'else',
            '  echo "BACKUP_SKIP ai 远端目录不存在"',
            'fi',
        ]
    lines.append("echo BACKUP_DONE")
    return "\n".join(lines)


def prune_old_backups(cfg: SshConfig, environment, keep):
    """只保留最近 keep 份有效备份（按目录名时间戳倒序）；失败仅告警不阻断。"""
    if keep is None or keep <= 0:
        return
    script = "\n".join([
        "set -euo pipefail",
        f'cd "$HOME/{BACKUP_ROOT}/{environment}" 2>/dev/null || exit 0',
        f"ls -1dt */ 2>/dev/null | tail -n +{keep + 1} | while read -r d; do",
        '  if [ -f "$d/manifest.json" ]; then rm -rf -- "$d"; echo "PRUNED $d"; fi',
        "done || true",
        "echo PRUNE_DONE",
    ])
    try:
        run_remote_script(cfg, script, label="清理旧备份")
    except RuntimeError as e:
        write_info(f"清理旧备份未成功（不影响本次部署）: {e}")


def create_backup(cfg: SshConfig, environment, env, components, *,
                  git_commit="", keep=BACKUP_KEEP_DEFAULT):
    """部署前备份远端现状，返回 backup_id。

    只备份本次将要覆盖的组件；远端打包 → 本地写 manifest（含 sha256）→ 上传 → 存在性复核。
    任一步失败即抛 RuntimeError，调用方应中止部署（绝不带着「无备份」继续覆盖）。
    """
    write_step("【备份】部署前备份远端现状")
    backup_id = make_backup_id()
    out = run_remote_script(cfg, _backup_script(environment, env, components, backup_id),
                            label="远端备份")
    if cfg.dry_run:
        return backup_id

    files = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[0] == "BKFILE":
            try:
                files[parts[1]] = {"size": int(parts[2]), "sha256": parts[3]}
            except ValueError:
                pass
    if not files:
        raise RuntimeError("备份失败：远端未生成任何备份包（已中止部署）")

    manifest = {
        "backup_id": backup_id,
        "environment": environment,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "created_by": "ci" if os.environ.get("CI") else "local",
        "git_commit": git_commit or "",
        "components": sorted(components),
        "files": files,
    }
    local_manifest = os.path.join(tempfile.gettempdir(), f"ors_backup_{backup_id}.json")
    with open(local_manifest, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    try:
        # scp 远端用相对路径（相对远端 HOME），兼容新版 sftp 后端与旧版 scp
        rel_target = f"{BACKUP_ROOT}/{environment}/{backup_id}/manifest.json"
        scp_args = cfg.ssh_args("scp") + [local_manifest, f"{cfg.target()}:{rel_target}"]
        if _run(["scp"] + scp_args) != 0:
            raise RuntimeError("备份清单上传失败（已中止部署）")
    finally:
        try:
            os.remove(local_manifest)
        except OSError:
            pass

    verify = run_remote_script(
        cfg,
        f'test -f "$HOME/{BACKUP_ROOT}/{environment}/{backup_id}/manifest.json" '
        f'&& echo BACKUP_VERIFIED',
        label="备份复核")
    if "BACKUP_VERIFIED" not in verify:
        raise RuntimeError("备份复核失败：清单未落盘（已中止部署）")

    write_ok("备份内容: " + ", ".join(
        f"{k}({v['size']}B)" for k, v in sorted(files.items())))
    prune_old_backups(cfg, environment, keep)
    write_ok(f"备份完成: {backup_id}")
    return backup_id


def _parse_manifests(out):
    """从远端输出中提取所有 manifest.json 内容（按备份 id 倒序）。"""
    items, buf, inside = [], [], False
    for line in out.splitlines():
        if line.startswith("MANIFEST_BEGIN"):
            inside, buf = True, []
            continue
        if inside and "MANIFEST_END" in line:
            # 兼容 manifest 末尾无换行的情况（此时 END 标记与 "}" 粘在同一行）
            head = line.split("MANIFEST_END", 1)[0]
            if head.strip():
                buf.append(head)
            inside = False
            if buf:
                try:
                    items.append(json.loads("\n".join(buf)))
                except json.JSONDecodeError:
                    pass
            continue
        if inside:
            buf.append(line)
    items.sort(key=lambda m: m.get("backup_id", ""), reverse=True)
    return items


def list_backups(cfg: SshConfig, environment):
    """列出远端有效备份（存在 manifest.json 的目录），供 --list-backups / 回滚选择。"""
    script = "\n".join([
        "set -euo pipefail",
        f'cd "$HOME/{BACKUP_ROOT}/{environment}" 2>/dev/null || exit 0',
        "for d in */; do",
        '  if [ -f "$d/manifest.json" ]; then',
        '    echo "MANIFEST_BEGIN $d"',
        '    cat "$d/manifest.json"',
        '    echo "MANIFEST_END"',
        "  fi",
        "done || true",
    ])
    return _parse_manifests(run_remote_script(cfg, script, label="列出备份"))


def rollback_to_backup(cfg: SshConfig, environment, env, backup_id):
    """把远端还原到指定备份并重启受影响的服务。

    还原动作与部署动作保持对称：前端「清空后解压」、后端「删除 app 后解压」、
    AI「覆盖解压（不动 kb/embed_models/.env 等数据目录）」。
    """
    bid = validate_backup_id(backup_id)
    write_step(f"【回滚】还原到备份 {bid}")
    html, be, ai = env["NginxHtml"], env["BackendRemote"], env["AiRemote"]
    lines = [
        "set -euo pipefail",
        f'BK="$HOME/{BACKUP_ROOT}/{environment}/{bid}"',
        'if [ ! -f "$BK/manifest.json" ]; then echo ROLLBACK_NO_MANIFEST; exit 3; fi',
        f'HTML="{html}"',
        'if [ -f "$BK/frontend.tar.gz" ]; then',
        '  mkdir -p "$HTML"',
        '  find "$HTML" -mindepth 1 -maxdepth 1 -exec rm -rf {} +',
        '  tar -xzf "$BK/frontend.tar.gz" -C "$HTML"',
        '  echo "ROLLBACK_APPLIED frontend"',
        'fi',
        f'BE="{be}"',
        'if [ -f "$BK/backend.tar.gz" ]; then',
        '  mkdir -p "$BE"',
        '  rm -rf "$BE/app"',
        '  tar -xzf "$BK/backend.tar.gz" -C "$BE"',
        '  echo "ROLLBACK_APPLIED backend"',
        'fi',
        f'AI="{ai}"',
        'if [ -f "$BK/ai.tar.gz" ]; then',
        '  mkdir -p "$AI"',
        '  tar -xzf "$BK/ai.tar.gz" -C "$AI"',
        '  echo "ROLLBACK_APPLIED ai"',
        'fi',
        "echo ROLLBACK_DONE",
    ]
    out = run_remote_script(cfg, "\n".join(lines), label="回滚还原")
    if cfg.dry_run:
        return bid
    applied = [ln.split()[-1] for ln in out.splitlines() if ln.startswith("ROLLBACK_APPLIED")]
    if not applied:
        raise RuntimeError(f"备份 {bid} 未包含任何可还原的组件包")
    if "backend" in applied:
        restart_supervisor(cfg, env["SupBackend"])
    if "ai" in applied:
        restart_supervisor(cfg, env["SupAi"])
    write_ok(f"回滚完成（{bid}）: {', '.join(applied)}")
    return bid


def _validate_health_urls(raw):
    """解析并校验健康检查 URL（逗号分隔）：仅允许 http/https 且不含 shell 危险字符。"""
    urls = [u.strip() for u in (raw or "").split(",") if u.strip()]
    for u in urls:
        if not re.match(r"^https?://", u) or re.search(r"""[\s'"`\\|<>$;]""", u):
            raise RuntimeError(f"健康检查 URL 非法: {u!r}（仅允许 http/https 且无特殊字符）")
    return urls


def remote_health_check(cfg: SshConfig, urls, retries=20, interval=3):
    """在远端 curl 各健康端点（带重试，等服务重启后预热）。全部通过返回 True。

    不抛异常：失败由调用方决定是否回滚，便于在 CI 中保留清晰的失败语义。
    """
    if not urls:
        return True
    write_step("【健康检查】服务就绪校验")
    quoted = " ".join(shlex.quote(u) for u in urls)
    script = "\n".join([
        "set -uo pipefail",
        f"for i in $(seq 1 {int(retries)}); do",
        "  ok=1",
        f"  for u in {quoted}; do",
        '    if ! curl -fsS --max-time 5 "$u" >/dev/null 2>&1; then ok=0; fi',
        "  done",
        '  if [ "$ok" = "1" ]; then echo "HEALTH_OK"; exit 0; fi',
        '  echo "HEALTH_RETRY $i"',
        f"  sleep {int(interval)}",
        "done",
        'echo "HEALTH_FAIL"',
    ])
    out = run_remote_script(cfg, script, label="健康检查")
    if cfg.dry_run:
        return True
    for line in out.splitlines():
        if line.startswith("HEALTH_OK"):
            write_ok(f"健康检查通过（{', '.join(urls)}）")
            return True
    write_err(f"健康检查未通过（已重试 {retries} 次，每次间隔 {interval}s）: {', '.join(urls)}")
    return False


def _print_backups_json(items, environment):
    """用固定标记包裹单行 JSON，便于 CI 提取（其余日志走 stderr/彩色输出不影响）。"""
    payload = {"environment": environment, "count": len(items), "backups": items}
    print("BACKUPS_JSON_BEGIN")
    print(json.dumps(payload, ensure_ascii=False))
    print("BACKUPS_JSON_END")


def _print_backups_md(items, environment):
    """输出 Markdown 表格（CI 写入 Step Summary，供人工在 Actions 页面挑选备份）。"""
    print(f"### {environment} 环境可用备份（{len(items)} 份）")
    print("")
    if not items:
        print("暂无可用备份：每次部署前会自动生成一份。")
        return
    print("| 备份 id | 创建时间 | 提交 | 组件 | 大小 |")
    print("| --- | --- | --- | --- | --- |")
    for it in items:
        files = it.get("files") or {}
        size_kb = sum(int((f or {}).get("size") or 0) for f in files.values()) // 1024
        commit = (it.get("git_commit") or "")[:8]
        comps = ", ".join(it.get("components") or [])
        print(f"| `{it.get('backup_id', '')}` | {it.get('created_at', '')} "
              f"| `{commit}` | {comps} | {size_kb} KB |")
    print("")
    print("回滚方式：重新运行本工作流并选择 action=rollback，将上表 id 填入 backup_id。")


# ====================== 命令行入口 ======================
def build_ssh_config(args) -> SshConfig:
    # 合并优先级：命令行参数 > 本地配置文件 > DEFAULTS
    defaults = effective_defaults()
    host = args.ssh_host or defaults.get("ssh_host", "")
    user = args.ssh_user or defaults.get("ssh_user", "")
    port = args.ssh_port if args.ssh_port and args.ssh_port > 0 else defaults.get("ssh_port", 22)
    identity = args.ssh_identity or defaults.get("ssh_identity", "")
    sudo_password = args.sudo_password or defaults.get("sudo_password", "")
    return SshConfig(host=host, user=user, port=port,
                     identity=identity, sudo_password=sudo_password,
                     dry_run=args.dry_run,
                     no_sudo=getattr(args, "no_sudo", False),
                     remote_tmp=getattr(args, "remote_tmp", None) or "~/tmp")


def main_cli(args):
    try:
        cfg = build_ssh_config(args)

        # 项目根：命令行参数 > 本地配置文件 > 脚本上一级目录
        if args.project_path:
            repo_root = Path(args.project_path)
        else:
            proj = effective_defaults().get("project_path")
            repo_root = Path(proj) if proj else default_project_path()
        if not repo_root.is_dir():
            raise RuntimeError(f"项目路径不存在: {repo_root}")

        # ---------- 前置检查 ----------
        write_step("前置检查")

        if not cfg.host:
            cfg.host = input("请输入远程服务器地址 (IP/域名): ").strip()
            if not cfg.host:
                raise RuntimeError("必须提供远程服务器地址")

        # 仅查询备份 / 执行回滚时不构建前端，无需 npm
        query_only = bool(args.list_backups or args.rollback)
        for tool in (["ssh"] if query_only else ["tar", "scp", "ssh", "npm"]):
            if not test_command(tool):
                raise RuntimeError(
                    f"未找到依赖工具: {tool}。请确保其已安装并在 PATH 中。")

        env = get_env_config(args.environment)

        # ---------- 只读查询：列出可用备份（供 CI 摘要与人工选择） ----------
        if args.list_backups:
            write_step(f"列出 {args.environment} 环境可用备份")
            items = list_backups(cfg, args.environment)
            if args.backups_format == "md":
                _print_backups_md(items, args.environment)
            else:
                _print_backups_json(items, args.environment)
            return 0

        # ---------- 回滚到指定备份 ----------
        if args.rollback:
            target = args.rollback.strip()
            if target == "latest":
                items = list_backups(cfg, args.environment)
                if not items:
                    raise RuntimeError(f"{args.environment} 环境没有任何可用备份")
                target = items[0]["backup_id"]
            rollback_to_backup(cfg, args.environment, env, target)
            # 回滚后同样校验服务就绪；失败只报警并以 1 退出（不再递归回滚）
            health_urls = _validate_health_urls(args.health_urls)
            if health_urls and not remote_health_check(cfg, health_urls,
                                                       args.health_retries,
                                                       args.health_interval):
                write_err("回滚后健康检查未通过，请立即人工介入")
                return 1
            write_step("全部完成")
            return 0

        components = parse_components(args.components)

        if args.environment == "prod" and not args.dry_run and not args.yes:
            confirm = input(f"即将部署到【生产环境】服务器 {cfg.host}，确认继续？输入 yes 继续: ").strip()
            if confirm != "yes":
                print("已取消。")
                return 0

        write_ok(f"环境: {args.environment} | 服务器: {cfg.user}@{cfg.host}:{cfg.port}")
        write_ok(f"组件: {', '.join(components)}")
        write_ok(f"远端基目录: {env['RemoteBase']}")

        backup_id = None
        if args.no_backup:
            write_info("已按要求跳过部署前备份（--no-backup）")
        else:
            backup_id = create_backup(cfg, args.environment, env, components,
                                      git_commit=args.git_commit or "",
                                      keep=args.backup_keep)
            # 固定格式输出，供 CI 抓取：部署失败时据此自动回滚到「本次部署前」
            print(f"DEPLOY_BACKUP_ID={backup_id}")

        if "frontend" in components:
            deploy_frontend(cfg, repo_root, env, args.skip_build)
        if "backend" in components:
            deploy_backend(cfg, repo_root, env, args.clean_remote)
        if "ai" in components:
            deploy_ai(cfg, repo_root, env)

        # ---------- 部署后健康检查；未通过则自动回滚到本次部署前 ----------
        health_urls = _validate_health_urls(args.health_urls)
        if health_urls and not remote_health_check(cfg, health_urls,
                                                   args.health_retries,
                                                   args.health_interval):
            if backup_id:
                write_err("健康检查未通过，自动回滚到本次部署前的备份")
                try:
                    rollback_to_backup(cfg, args.environment, env, backup_id)
                    write_ok("已自动回滚，请人工确认服务状态")
                except RuntimeError as e:
                    write_err(f"自动回滚失败，请立即人工处理: {e}")
            else:
                write_err("健康检查未通过，且本次未生成备份，无法自动回滚")
            return 1

        write_step("全部完成")
        write_ok(f"{args.environment} 环境部署结束: {', '.join(components)}")
        return 0

    except RuntimeError as e:
        write_err(str(e))
        return 1
    except KeyboardInterrupt:
        write_err("用户中断")
        return 130


# ====================== 图形界面入口 ======================
COMPONENT_LABELS = ["全部", "前端", "后端", "算法"]
COMPONENT_MAP = {
    "全部": ["all"],
    "前端": ["frontend"],
    "后端": ["backend"],
    "算法": ["ai"],
}


class DeployApp:
    def __init__(self, root):
        self.root = root
        self._running = False
        self._build_ui()
        self._load_config_into_form()

    # ---- 界面构建 ----
    def _build_ui(self):
        self.root.title("OpenRobotService 部署工具")
        self.root.geometry("780x700")
        self.root.minsize(640, 560)

        # ---- 部署配置区 ----
        cfg_frame = ttk.LabelFrame(self.root, text="部署配置（保存到本地，不上云端）")
        cfg_frame.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Label(cfg_frame, text="服务器地址:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.host_var = tk.StringVar()
        ttk.Entry(cfg_frame, textvariable=self.host_var).grid(
            row=0, column=1, columnspan=3, sticky="we", padx=5)

        ttk.Label(cfg_frame, text="用户名:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.user_var = tk.StringVar()
        ttk.Entry(cfg_frame, textvariable=self.user_var).grid(
            row=1, column=1, sticky="we", padx=5)
        ttk.Label(cfg_frame, text="端口:").grid(row=1, column=2, sticky="e", padx=5)
        self.port_var = tk.StringVar()
        ttk.Entry(cfg_frame, textvariable=self.port_var, width=8).grid(
            row=1, column=3, sticky="w", padx=5)

        ttk.Label(cfg_frame, text="私钥路径:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.identity_var = tk.StringVar()
        ttk.Entry(cfg_frame, textvariable=self.identity_var).grid(
            row=2, column=1, columnspan=2, sticky="we", padx=5)
        ttk.Button(cfg_frame, text="浏览...", command=self._browse_identity).grid(
            row=2, column=3, padx=5, pady=2)

        ttk.Label(cfg_frame, text="sudo 密码:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.pwd_var = tk.StringVar()
        self.pwd_entry = ttk.Entry(cfg_frame, textvariable=self.pwd_var, show="*")
        self.pwd_entry.grid(row=3, column=1, columnspan=2, sticky="we", padx=5)
        self.show_pwd_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg_frame, text="显示", variable=self.show_pwd_var,
                        command=self._toggle_pwd).grid(row=3, column=3, padx=5)

        ttk.Label(cfg_frame, text="项目路径:").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        self.project_path_var = tk.StringVar()
        ttk.Entry(cfg_frame, textvariable=self.project_path_var).grid(
            row=4, column=1, columnspan=2, sticky="we", padx=5)
        ttk.Button(cfg_frame, text="浏览...", command=self._browse_project_path).grid(
            row=4, column=3, padx=5, pady=2)

        cfg_frame.columnconfigure(1, weight=1)

        # ---- 部署选项区 ----
        opt_frame = ttk.LabelFrame(self.root, text="部署选项")
        opt_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(opt_frame, text="部署组件:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.component_var = tk.StringVar(value="全部")
        ttk.Combobox(opt_frame, textvariable=self.component_var,
                     values=COMPONENT_LABELS, state="readonly", width=10).grid(
            row=0, column=1, sticky="w", padx=5)

        self.skip_build_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="跳过前端构建", variable=self.skip_build_var).grid(
            row=0, column=2, padx=15)

        self.clean_remote_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="部署前清空远程 backend/app",
                        variable=self.clean_remote_var).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=5)

        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="试运行 (dry-run，只打印不执行)",
                        variable=self.dry_run_var).grid(row=1, column=2, padx=15)

        # ---- 按钮区 ----
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=5)
        self.btn_test = ttk.Button(btn_frame, text="部署到测试环境",
                                   command=lambda: self._start_deploy("test"))
        self.btn_test.pack(side="left", padx=(0, 10))
        self.btn_prod = ttk.Button(btn_frame, text="部署到生产环境",
                                   command=lambda: self._start_deploy("prod"))
        self.btn_prod.pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="保存配置",
                   command=self._save_config_from_form).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side="right")

        # ---- 日志区 ----
        log_frame = ttk.LabelFrame(self.root, text="日志输出")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=18, wrap="word",
                                                  state="disabled", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_text.tag_configure("step", foreground="#0096c7")
        self.log_text.tag_configure("ok", foreground="#2a9d3f")
        self.log_text.tag_configure("err", foreground="#d62828")
        self.log_text.tag_configure("info", foreground="#6c757d")
        self.log_text.tag_configure("cmd", foreground="#495057")

    # ---- 表单与配置 ----
    def _toggle_pwd(self):
        self.pwd_entry.config(show="" if self.show_pwd_var.get() else "*")

    def _browse_identity(self):
        path = filedialog.askopenfilename(title="选择 SSH 私钥",
                                          filetypes=[("所有文件", "*.*")])
        if path:
            self.identity_var.set(path)

    def _browse_project_path(self):
        path = filedialog.askdirectory(title="选择项目根目录（包含 frontend/backend/ai）")
        if path:
            self.project_path_var.set(path)

    def _load_config_into_form(self):
        cfg = effective_defaults()
        self.host_var.set(cfg.get("ssh_host", ""))
        self.user_var.set(cfg.get("ssh_user", ""))
        self.port_var.set(str(cfg.get("ssh_port", "")))
        self.identity_var.set(cfg.get("ssh_identity", ""))
        self.pwd_var.set(cfg.get("sudo_password", ""))
        self.project_path_var.set(cfg.get("project_path") or str(default_project_path()))

    def _collect_form_config(self):
        port_str = self.port_var.get().strip()
        try:
            port = int(port_str) if port_str else 0
        except ValueError:
            port = 0
        return {
            "ssh_host": self.host_var.get().strip(),
            "ssh_user": self.user_var.get().strip(),
            "ssh_port": port,
            "ssh_identity": self.identity_var.get().strip(),
            "sudo_password": self.pwd_var.get(),
            "project_path": self.project_path_var.get().strip(),
        }

    def _save_config_from_form(self):
        try:
            save_user_config(self._collect_form_config())
            write_ok(f"配置已保存到本地: {CONFIG_FILE}")
        except Exception as e:
            write_err(f"保存配置失败: {e}")

    def _build_ssh_config(self, environment):
        c = self._collect_form_config()
        host = c["ssh_host"]
        user = c["ssh_user"]
        port = c["ssh_port"] or 22
        identity = c["ssh_identity"]
        sudo_password = c["sudo_password"]
        dry_run = self.dry_run_var.get()
        if not host:
            raise RuntimeError("请填写服务器地址")
        if not user:
            raise RuntimeError("请填写 SSH 用户名")
        return SshConfig(host=host, user=user, port=port,
                        identity=identity, sudo_password=sudo_password,
                        dry_run=dry_run)

    def _get_repo_root(self):
        p = self.project_path_var.get().strip()
        path = Path(p) if p else default_project_path()
        if not path.is_dir():
            raise RuntimeError(f"项目路径不存在: {path}")
        return path

    # ---- 日志区操作（线程安全：通过 after 调度到主线程） ----
    def _log_handler(self, text, tag):
        self.root.after(0, self._append_log, text, tag)

    def _append_log(self, text, tag):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_buttons_state(self, state):
        self.btn_test.config(state=state)
        self.btn_prod.config(state=state)

    # ---- 部署流程 ----
    def _start_deploy(self, environment):
        if self._running:
            return
        # 主线程内做配置校验与生产确认（避免在工作线程里弹消息框）
        try:
            cfg = self._build_ssh_config(environment)
            repo_root = self._get_repo_root()
        except RuntimeError as e:
            messagebox.showerror("配置错误", str(e))
            return

        comp_label = self.component_var.get()
        comps = parse_components(COMPONENT_MAP.get(comp_label, ["all"]))

        if environment == "prod" and not cfg.dry_run:
            if not messagebox.askyesno(
                    "生产部署确认",
                    f"即将部署到【生产环境】服务器 {cfg.host}，确认继续？"):
                return

        # backend/ai 重启 supervisor 需要 sudo 密码
        needs_sudo = ("backend" in comps) or ("ai" in comps)
        if needs_sudo and not cfg.sudo_password and not cfg.dry_run:
            messagebox.showerror("缺少 sudo 密码",
                                 "重启 supervisor 需要 sudo 权限，请先在配置中填写 sudo 密码。")
            return

        # 保存配置 + 清空日志 + 禁用按钮，随后后台线程执行
        self._save_config_from_form()
        self._clear_log()
        self._set_buttons_state("disabled")
        self._running = True
        write_step(f"开始部署（{environment} 环境）")
        write_info(f"项目路径: {repo_root}")
        t = threading.Thread(target=self._deploy_thread,
                             args=(environment, comp_label, repo_root), daemon=True)
        t.start()

    def _deploy_thread(self, environment, comp_label, repo_root):
        try:
            cfg = self._build_ssh_config(environment)
            env = get_env_config(environment)
            comps = parse_components(COMPONENT_MAP.get(comp_label, ["all"]))

            write_ok(f"环境: {environment} | 服务器: {cfg.user}@{cfg.host}:{cfg.port}")
            write_ok(f"组件: {', '.join(comps)}")
            write_ok(f"远端基目录: {env['RemoteBase']}")

            for tool in ["tar", "scp", "ssh", "npm"]:
                if not test_command(tool):
                    raise RuntimeError(
                        f"未找到依赖工具: {tool}。请确保其已安装并在 PATH 中。")

            if "frontend" in comps:
                deploy_frontend(cfg, repo_root, env, self.skip_build_var.get())
            if "backend" in comps:
                deploy_backend(cfg, repo_root, env, self.clean_remote_var.get())
            if "ai" in comps:
                deploy_ai(cfg, repo_root, env)

            write_step("全部完成")
            write_ok(f"{environment} 环境部署结束: {', '.join(comps)}")
        except RuntimeError as e:
            write_err(str(e))
        except Exception as e:  # noqa: BLE001
            write_err(f"未知错误: {e}")
        finally:
            self.root.after(0, self._finish_deploy)

    def _finish_deploy(self):
        self._running = False
        self._set_buttons_state("normal")


def run_gui():
    global _output_handler
    if not _HAS_TK:
        print("错误：当前环境未安装 tkinter，无法启动图形界面。请使用命令行模式。", file=sys.stderr)
        print("提示：Windows 官方 Python 安装时勾选 'tcl/tk and IDLE' 即可。", file=sys.stderr)
        return 1
    root = tk.Tk()
    app = DeployApp(root)
    _output_handler = app._log_handler
    try:
        root.mainloop()
    finally:
        _output_handler = None
    return 0


# ====================== 入口 ======================
def build_parser():
    parser = argparse.ArgumentParser(
        description="OpenRobotService 一键部署脚本（前端 + 后端 + 算法）。"
    )
    parser.add_argument("--gui", action="store_true",
                        help="启动图形界面（默认无参数即启动 GUI，此参数可显式指定）。")
    parser.add_argument("-e", "--environment", choices=["test", "prod"],
                        default="test", help="环境类型：test 或 prod。默认 test。")
    parser.add_argument("-c", "--components", nargs="+", default=["all"],
                        help="要部署的组件：frontend, backend, ai, all。默认 all。")
    parser.add_argument("--ssh-host", dest="ssh_host", help="远程服务器地址（IP 或域名）。")
    parser.add_argument("--ssh-user", dest="ssh_user", help="SSH 用户名。")
    parser.add_argument("--ssh-port", dest="ssh_port", type=int, default=0,
                        help="SSH 端口。")
    parser.add_argument("--ssh-identity", dest="ssh_identity",
                        help="SSH 私钥文件路径。")
    parser.add_argument("--sudo-password", dest="sudo_password",
                        help="远端 sudo 密码（supervisorctl 需要 sudo）。")
    parser.add_argument("--skip-build", action="store_true",
                        help="跳过前端构建（使用已存在的 frontend/dist）。")
    parser.add_argument("--no-clean-remote", dest="clean_remote", action="store_false",
                        help="部署前不清空远程 backend/app 目录。")
    parser.set_defaults(clean_remote=True)
    parser.add_argument("--project-path", dest="project_path",
                        help="项目根目录（包含 frontend/backend/ai）。默认为脚本上一级目录。")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要执行的命令，不真正执行。")
    # ---------- 自动化（CI / 无人值守）相关参数 ----------
    parser.add_argument("--yes", action="store_true",
                        help="跳过交互式确认（CI 非交互执行时必须指定）。")
    parser.add_argument("--no-sudo", dest="no_sudo", action="store_true",
                        help="supervisorctl 不加 sudo（服务器已给 supervisor 组 socket 权限时使用）。")
    parser.add_argument("--remote-tmp", dest="remote_tmp", default="~/tmp",
                        help="远端暂存目录，默认 ~/tmp（scp 自动展开 ~，bash 侧自动转 $HOME）；"
                             "家目录亦不可写时可用绝对路径覆盖。")
    parser.add_argument("--no-backup", dest="no_backup", action="store_true",
                        help="跳过部署前自动备份（不推荐：将失去本次回滚能力）。")
    parser.add_argument("--backup-keep", dest="backup_keep", type=int,
                        default=BACKUP_KEEP_DEFAULT,
                        help=f"保留最近 N 份备份，0 表示不清理。默认 {BACKUP_KEEP_DEFAULT}。")
    parser.add_argument("--git-commit", dest="git_commit", default="",
                        help="写入备份清单的提交号（CI 传入，便于追溯）。")
    parser.add_argument("--list-backups", dest="list_backups", action="store_true",
                        help="列出远端可用备份后退出，不执行部署。")
    parser.add_argument("--backups-format", dest="backups_format", default="json",
                        choices=["json", "md"],
                        help="--list-backups 输出格式：json（默认，供 CI 解析）"
                             "或 md（Markdown 表格，供 Actions 摘要）。")
    parser.add_argument("--rollback", dest="rollback", default="",
                        help="回滚到指定备份 id（或 latest）后退出，不执行部署。")
    parser.add_argument("--health-urls", dest="health_urls", default="",
                        help="部署后健康检查地址（逗号分隔，服务器本机可访问的 HTTP 端点）。"
                             "检查失败会自动回滚到本次部署前的备份。")
    parser.add_argument("--health-retries", dest="health_retries", type=int, default=20,
                        help="健康检查重试次数（默认 20，配合 --health-interval 共等待 60 秒）。")
    parser.add_argument("--health-interval", dest="health_interval", type=int, default=3,
                        help="健康检查重试间隔秒数（默认 3）。")
    return parser


def main():
    # 无任何参数时默认启动图形界面；带参数则按 CLI 处理
    if len(sys.argv) == 1:
        return run_gui()
    args = build_parser().parse_args()
    if args.gui:
        return run_gui()
    return main_cli(args)


if __name__ == "__main__":
    sys.exit(main())
