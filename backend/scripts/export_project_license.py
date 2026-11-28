"""项目授权完整流程脚本（独立运行版）。

参考文档：docs/apply_project_license_api.md
涉及两个独立服务，各自一个 base host：

  ┌─────────────────────── 服务划分 & base host ───────────────────────┐
  │                                                                    │
  │  BACKEND_HOST  (本服务 OpenRobotService admin API)                 │
  │    STEP 3  POST /api/admin/export/apply_project_license           │
  │    STEP 4  POST /api/admin/export/project/{project_code}          │
  │                  + GET  /api/admin/export/project/{code}/meta     │
  │                                                                    │
  │  MONITOR_HOST (另一服务 monitorPlatform)                          │
  │    STEP 1  GET  /monitorPlatform/license/machineCode              │
  │    STEP 2  GET  /monitorPlatform/license/status                   │
  │    STEP 5  POST /monitorPlatform/auth/import-users   (multipart)  │
  │                                                                    │
  └────────────────────────────────────────────────────────────────────┘

配置文件（优先级：CLI > config > 硬编码默认值）：
  默认同目录 export_project_license.config.json，也可 --config 指定
  一键生成示例：python export_project_license.py --init-config

简化命令示例（配置文件已写好）：
  # 全流程
  python export_project_license.py run

  # 只导出（配置里有 backend_host / export_key / project_code / type）
  python export_project_license.py export --pretty

依赖：pip install requests
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------
DEFAULT_BACKEND_HOST = "http://127.0.0.1:8100"   # 本服务 admin API
DEFAULT_MONITOR_HOST = "http://127.0.0.1"        # monitorPlatform 另一服务
DEFAULT_CONFIG_NAME = "export_project_license.config.json"  # 同目录自动发现


# ---------------------------------------------------------------------------
# 路径定义
# ---------------------------------------------------------------------------
class _Path:
    # BACKEND_HOST 下
    APPLY_LICENSE = "/api/admin/export/apply_project_license"
    EXPORT        = "/api/admin/export/project/{project_code}"
    EXPORT_META   = "/api/admin/export/project/{project_code}/meta"
    # MONITOR_HOST 下
    MACHINE_CODE  = "/monitorPlatform/license/machineCode"
    LICENSE_STATUS= "/monitorPlatform/license/status"
    IMPORT_USERS  = "/monitorPlatform/auth/import-users"


# ===========================================================================
# 配置文件加载 & 合并
# ===========================================================================

def _script_dir() -> Path:
    """脚本所在目录（兼容 .py 直跑和 PyInstaller 打包）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_config_path(explicit: Optional[str] = None) -> Optional[Path]:
    """定位配置文件：显式指定 > 脚本同目录 > None（不存在则返回 None）。"""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    auto = _script_dir() / DEFAULT_CONFIG_NAME
    return auto if auto.exists() else None


def _load_config(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RuntimeError("配置文件顶层必须是 JSON object")
        return raw
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"配置文件解析失败: {exc}")


# 配置文件合法字段清单（扁平化，避免嵌套造成困惑）
# key 同时作为 argparse 的 dest 名
_CONFIG_KEYS: Tuple[str, ...] = (
    # 通用
    "backend_host", "monitor_host",
    "output_dir", "timeout",
    # run 子命令
    "apply_key", "apply_token",
    "start_date", "end_date", "max_vehicles",
    "export_key", "export_token", "export_type",
    "check_meta", "monitor_token", "skip_step5", "pretty",
    # export 子命令
    "project_code", "type",   # export 用 --type / apply 用 --project-code
    # apply 子命令
    "mac",
)


def _merge_config_into_args(args: argparse.Namespace, cfg: Dict[str, Any]) -> None:
    """把配置文件中的值覆盖到 args 上（仅覆盖 CLI 未显式设置的 None 字段）。

    优先级：CLI 显式值（非 None）> 配置文件值 > argparse 硬编码默认
    """
    # export 子命令里 type 映射到 args.type，run 子命令里是 args.export_type
    # 配置文件里只放一个 type，统一落到 args.type 和 args.export_type 两个字段
    cfg_type = cfg.pop("type", None)
    if cfg_type is not None:
        if getattr(args, "type", None) is None:
            args.type = cfg_type
        if getattr(args, "export_type", None) is None:
            args.export_type = cfg_type

    for key in _CONFIG_KEYS:
        if key in cfg and cfg[key] is not None:
            current = getattr(args, key, None)
            if current is None:   # CLI 没显式传，用配置值兜底
                setattr(args, key, cfg[key])


def _generate_example_config(target: Path) -> None:
    """写出示例配置文件。"""
    example: Dict[str, Any] = {
        "_comment": (
            "优先级：命令行参数 > 本配置文件 > 脚本内硬编码默认值。"
            "字段与命令行参数同名，按需填写，留空或不写都视为未设置。"
        ),
        "backend_host": DEFAULT_BACKEND_HOST,
        "monitor_host": DEFAULT_MONITOR_HOST,
        "output_dir": "./exports",
        "timeout": 30,
        "apply_key": "",   # 匹配后端 LICENSE_APPLY_API_KEY，STEP3 通道 A
        "apply_token": "",  # 或用 JWT 通道 B（STEP3）
        "export_key": "",   # 匹配后端 USP_PROJECT_SYNC_KEY，STEP4 通道 A
        "export_token": "", # 或用 JWT 通道 B（STEP4）
        "project_code": "", # run 子命令会自动从 STEP2 拿；export/apply 必须显式或配置
        "type": "all",      # export_type 简写：license / users / all
        "start_date": "",   # YYYY-MM-DD；空则自动填今日 ~ +2 年
        "end_date": "",
        "max_vehicles": None,  # null = 不限制
        "check_meta": False,
        "skip_step5": False,
        "monitor_token": "",   # STEP5 导入用的 Bearer Token
        "pretty": False,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(example, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dump_effective_config(args: argparse.Namespace, cfg_loaded: bool, cfg_path: Optional[Path]) -> None:
    """打印当前生效的关键配置，让用户一眼看清。"""
    print(f"  配置文件 : {cfg_path or '(未找到，使用默认)'}")
    if cfg_loaded:
        print(f"  合并规则 : CLI > 配置文件 > 硬编码默认")
    print("-" * 42)


# ===========================================================================
# HTTP 工具
# ===========================================================================

def _join(host: str, path: str) -> str:
    return host.rstrip("/") + path


def _headers_bearer(token: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h: Dict[str, str] = {"Authorization": f"Bearer {token}"} if token else {}
    if extra:
        h.update(extra)
    return h


def _expect_json(resp: requests.Response, label: str = "") -> Any:
    """统一处理 JSON 响应，出错时抛带上下文的 RuntimeError。"""
    if resp.status_code >= 400:
        detail: Any = resp.text[:500]
        try:
            detail = resp.json()
        except Exception:
            pass
        raise RuntimeError(f"[{label}] HTTP {resp.status_code}: {detail}")
    try:
        return resp.json()
    except Exception as exc:
        raise RuntimeError(f"[{label}] 响应不是合法 JSON: {exc}; 前 200 字节: {resp.content[:200]!r}")


# ===========================================================================
# MONITOR_HOST 接口（STEP 1 / STEP 2 / STEP 5）
# ===========================================================================

def step1_get_machine_code(monitor_host: str, timeout: int = 15) -> str:
    url = _join(monitor_host, _Path.MACHINE_CODE)
    resp = requests.get(url, timeout=timeout)
    body = _expect_json(resp, "STEP1 机器码")
    code = body.get("data")
    if not code:
        raise RuntimeError(f"STEP1 机器码为空，完整响应: {body}")
    return code


def step2_get_project_code(monitor_host: str, timeout: int = 15) -> Tuple[str, Dict[str, Any]]:
    url = _join(monitor_host, _Path.LICENSE_STATUS)
    resp = requests.get(url, timeout=timeout)
    body = _expect_json(resp, "STEP2 项目编码")
    data = body.get("data", {}) or {}
    project_code = data.get("project_code")
    if not project_code:
        raise RuntimeError(f"STEP2 project_code 为空，完整响应: {body}")
    return project_code, data


def step5_import_users(
    monitor_host: str,
    gzip_path: Path,
    bearer_token: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """POST /monitorPlatform/auth/import-users 上传 gzip 文件。

    文档显示为 multipart/form-data + binary payload。
    由于文档未明确字段名，这里按常见约定用 file=gzip 上传；
    若服务端字段不同，可调整 files 参数 key。
    """
    url = _join(monitor_host, _Path.IMPORT_USERS)
    headers = _headers_bearer(bearer_token)
    with gzip_path.open("rb") as fh:
        files = {"file": (gzip_path.name, fh, "application/gzip")}
        resp = requests.post(url, headers=headers, files=files, timeout=timeout)
    body = _expect_json(resp, "STEP5 导入授权")
    return body


# ===========================================================================
# BACKEND_HOST 接口（STEP 3 / STEP 4）
# ===========================================================================

def _normalize_date(date_str: str, is_start: bool) -> str:
    """补齐时间后缀。已带时间（含空格/T/毫秒）直接返回。"""
    if not date_str:
        return date_str
    # 已包含时间（YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DDTHH:MM:SS 等）
    if " " in date_str or "T" in date_str:
        return date_str
    return date_str + (" 00:00:00" if is_start else " 23:59:59")


def step3_apply_project_license(
    backend_host: str,
    project_code: str,
    mac: str,
    start_date: str,
    end_date: str,
    max_vehicles: int,
    apply_key: str = "",
    jwt_token: str = "",
    timeout: int = 65,
) -> Dict[str, Any]:
    # 统一补齐时间后缀：YYYY-MM-DD → YYYY-MM-DD 00:00:00 / 23:59:59
    start_date = _normalize_date(start_date, is_start=True)
    end_date = _normalize_date(end_date, is_start=False)

    url = _join(backend_host, _Path.APPLY_LICENSE)
    params = {"key": apply_key} if apply_key else None
    headers = {"Content-Type": "application/json"}
    if not apply_key and jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"

    payload: Dict[str, Any] = {
        "project_code": project_code,
        "mac": mac,
        "start_date": start_date,
        "end_date": end_date,
        "max_vehicles": max_vehicles,
    }

    resp = requests.post(url, headers=headers, params=params, json=payload, timeout=timeout)
    body = _expect_json(resp, "STEP3 申请授权")
    return body


def step4_export_project(
    backend_host: str,
    project_code: str,
    export_type: str = "all",
    api_key: str = "",
    jwt_token: str = "",
    timeout: int = 30,
) -> bytes:
    if export_type not in ("license", "users", "all"):
        raise RuntimeError(f"type 必须是 license/users/all，收到 {export_type!r}")

    url = _join(backend_host, _Path.EXPORT.format(project_code=project_code))
    headers: Dict[str, str] = {"Accept": "*/*"}
    if api_key:
        headers["X-API-Key"] = api_key
    elif jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"

    resp = requests.post(url, headers=headers, params={"type": export_type}, timeout=timeout)
    if resp.status_code >= 400:
        detail: Any = resp.text[:500]
        try:
            detail = resp.json()
        except Exception:
            pass
        raise RuntimeError(f"[STEP4 导出] HTTP {resp.status_code}: {detail}")
    return resp.content


def step4_meta(
    backend_host: str,
    project_code: str,
    api_key: str = "",
    jwt_token: str = "",
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    url = _join(backend_host, _Path.EXPORT_META.format(project_code=project_code))
    headers: Dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key
    elif jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException as exc:
        print(f"[WARN] STEP4 meta 预检异常: {exc}", file=sys.stderr)
    return None


# ===========================================================================
# 文件 I/O 辅助
# ===========================================================================

def decompress_gzip(raw: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as gz:
        return gz.read()


def save_bytes(data: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_summary(out_dir: Path, summary: Dict[str, Any]) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = out_dir / f"summary_{ts}.json"
    save_bytes(json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"), p)
    return p


# ===========================================================================
# 顶层：构造主解析器（所有字段用 default=None，让配置文件能介入）
# ===========================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "项目授权完整流程脚本 —— 双 host 分离 + JSON 配置文件\n"
            "  python export_project_license.py --init-config    # 一键生成示例配置\n"
            "  python export_project_license.py run               # 全流程（配置驱动）"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
优先级：CLI 参数 > 同目录 config.json > 硬编码默认值

常用简化命令（配置文件已写好）：
  python export_project_license.py run              # 全流程 1→5
  python export_project_license.py export --pretty  # 只导出并预览
  python export_project_license.py export -p xxx    # 临时换项目编码
""",
    )
    # 顶层配置相关
    parser.add_argument(
        "-c", "--config", default=None,
        help=f"指定配置文件路径；默认自动找脚本同目录的 {DEFAULT_CONFIG_NAME}",
    )
    parser.add_argument(
        "--init-config", action="store_true",
        help="在脚本同目录生成示例配置并退出（不会覆盖已存在文件）",
    )

    # 子命令（所有字段 default=None，让 merge 逻辑生效）
    sub = parser.add_subparsers(dest="cmd", required=False)

    def _common_args(p: argparse.ArgumentParser) -> None:
        """子命令通用参数。"""
        p.add_argument("--backend-host", default=None,
                       help=f"本服务 admin API（默认 {DEFAULT_BACKEND_HOST}）")
        p.add_argument("--monitor-host", default=None,
                       help=f"monitorPlatform（默认 {DEFAULT_MONITOR_HOST}）")
        p.add_argument("-o", "--output-dir", default=None,
                       help="输出目录（默认 ./exports）")
        p.add_argument("--timeout", type=int, default=None,
                       help="HTTP 超时秒数（默认 30）")

    # ---- run ----
    run_p = sub.add_parser("run", help="完整 STEP 1→5 流程")
    _common_args(run_p)
    run_p.add_argument("--apply-key", default=None)
    run_p.add_argument("--apply-token", default=None)
    run_p.add_argument("--start-date", default=None)
    run_p.add_argument("--end-date", default=None)
    run_p.add_argument("--max-vehicles", type=int, default=None)
    run_p.add_argument("--export-key", default=None)
    run_p.add_argument("--export-token", default=None)
    run_p.add_argument("-t", "--export-type", choices=["license", "users", "all"], default=None)
    run_p.add_argument("--check-meta", action="store_true", default=None)
    run_p.add_argument("--monitor-token", default=None)
    run_p.add_argument("--skip-step5", action="store_true", default=None)
    run_p.add_argument("--pretty", action="store_true", default=None)

    # ---- export ----
    ex_p = sub.add_parser("export", help="只跑 STEP 4 导出")
    _common_args(ex_p)
    ex_p.add_argument("-p", "--project-code", default=None)
    ex_p.add_argument("--export-key", default=None)
    ex_p.add_argument("--export-token", default=None)
    ex_p.add_argument("-t", "--type", choices=["license", "users", "all"], default=None)
    ex_p.add_argument("--check-meta", action="store_true", default=None)
    ex_p.add_argument("--pretty", action="store_true", default=None)

    # ---- apply ----
    ap_p = sub.add_parser("apply", help="只跑 STEP 3 申请")
    _common_args(ap_p)
    ap_p.add_argument("-p", "--project-code", default=None)
    ap_p.add_argument("--mac", default=None)
    ap_p.add_argument("--start-date", default=None)
    ap_p.add_argument("--end-date", default=None)
    ap_p.add_argument("--max-vehicles", type=int, default=None)
    ap_p.add_argument("--apply-key", default=None)
    ap_p.add_argument("--apply-token", default=None)

    return parser


def _fill_defaults(args: argparse.Namespace) -> None:
    """合并后再兜底硬编码默认值（只填仍为 None 的字段）。"""
    mapping = {
        "backend_host": DEFAULT_BACKEND_HOST,
        "monitor_host": DEFAULT_MONITOR_HOST,
        "output_dir": "./exports",
        "timeout": 30,
        # 布尔开关：None 统一当 False
        "check_meta": False,
        "skip_step5": False,
        "pretty": False,
    }
    for k, v in mapping.items():
        if getattr(args, k, None) is None:
            setattr(args, k, v)
    # export_type 兜底
    if getattr(args, "export_type", None) is None and getattr(args, "cmd", None) == "run":
        args.export_type = "all"
    if getattr(args, "type", None) is None and getattr(args, "cmd", None) == "export":
        args.type = "all"


def _validate_args(args: argparse.Namespace) -> int:
    """校验必填项，返回 0=OK，非 0=错误码。"""
    cmd = getattr(args, "cmd", None)
    errors: List[str] = []

    # 鉴权通道至少一个
    if cmd in ("run", "apply") and not (args.apply_key or args.apply_token):
        errors.append("STEP3 鉴权缺失：需 --apply-key 或 --apply-token（或写进配置文件）")
    if cmd in ("run", "export") and not (args.export_key or args.export_token):
        errors.append("STEP4 鉴权缺失：需 --export-key 或 --export-token（或写进配置文件）")

    # 项目编码
    if cmd == "export" and not args.project_code:
        errors.append("export 缺少 --project-code（或写进配置文件 project_code）")
    if cmd == "apply" and not args.project_code:
        errors.append("apply 缺少 --project-code")
    if cmd == "apply" and not args.mac:
        errors.append("apply 缺少 --mac")
    if cmd == "apply" and (not args.start_date or not args.end_date):
        errors.append("apply 缺少 --start-date / --end-date")
    # STEP3 必填：max_vehicles
    if cmd in ("run", "apply") and args.max_vehicles in (None, ""):
        errors.append("STEP3 缺少 --max-vehicles（后端已改为必填，0 也必须显式传）")

    if errors:
        print("参数错误：", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print(file=sys.stderr)
        print("提示：要么命令行补参数，要么把这些值写进同目录的 config.json", file=sys.stderr)
        print(f"  一键生成示例：python {Path(__file__).name} --init-config", file=sys.stderr)
        return 2
    return 0


# ===========================================================================
# 子命令实现
# ===========================================================================

def cmd_run(args: argparse.Namespace) -> int:
    summary: Dict[str, Any] = {"started_at": datetime.now().isoformat()}
    out_dir = Path(args.output_dir)

    print("=" * 60)
    print("项目授权完整流程  STEP 1 → 5")
    print(f"  BACKEND_HOST : {args.backend_host}")
    print(f"  MONITOR_HOST : {args.monitor_host}")
    print(f"  export_type  : {args.export_type}")
    print(f"  timeout      : {args.timeout}s")
    print("=" * 60)
    print()

    # ---- STEP 1 ----
    print("[STEP 1] 获取机器码 —— monitorPlatform")
    print(f"  URL: {_join(args.monitor_host, _Path.MACHINE_CODE)}")
    try:
        mac = step1_get_machine_code(args.monitor_host, timeout=args.timeout)
    except Exception as exc:
        print(f"  ✗ 失败: {exc}", file=sys.stderr)
        return 1
    print(f"  ✓ mac: {mac[:60]}{'...' if len(mac) > 60 else ''}")
    summary["step1_mac"] = mac
    print()

    # ---- STEP 2 ----
    print("[STEP 2] 获取项目编码 —— monitorPlatform")
    try:
        project_code, status_data = step2_get_project_code(args.monitor_host, timeout=args.timeout)
    except Exception as exc:
        print(f"  ✗ 失败: {exc}", file=sys.stderr)
        return 1
    print(f"  ✓ project_code : {project_code}")
    print(f"    valid        : {status_data.get('valid')}")
    print(f"    start_date   : {status_data.get('start_date')}")
    print(f"    end_date     : {status_data.get('end_date')}")
    summary["step2_project_code"] = project_code
    summary["step2_status"] = status_data
    print()

    # ---- STEP 3 ----
    print("[STEP 3] 申请项目授权 —— 本服务 admin API")
    print(f"  项目 : {project_code}")
    # 自动填充日期
    if not args.start_date or not args.end_date:
        today = datetime.now()
        start = args.start_date or today.strftime("%Y-%m-%d")
        end = args.end_date or today.replace(year=today.year + 2).strftime("%Y-%m-%d")
        print(f"  日期 (自动填充): {start} ~ {end}")
    else:
        start = args.start_date
        end = args.end_date

    try:
        apply_resp = step3_apply_project_license(
            backend_host=args.backend_host,
            project_code=project_code,
            mac=mac,
            start_date=start,
            end_date=end,
            max_vehicles=args.max_vehicles,
            apply_key=args.apply_key,
            jwt_token=args.apply_token,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"  ✗ 失败: {exc}", file=sys.stderr)
        return 1

    status = apply_resp.get("status", "(无 status 字段)")
    lc = apply_resp.get("license_content", "")
    print(f"  ✓ status          : {status}")
    print(f"    license_content : {lc[:50]}{'...' if len(lc) > 50 else ''}" if lc else "    license_content : (空)")
    print(f"    project_code    : {apply_resp.get('project_code', '-')}")
    summary["step3"] = apply_resp
    if status != "approved":
        print(f"  [WARN] 申请未通过（status={status}），继续导出现有数据 ...")
    print()

    # ---- STEP 4 ----
    print("[STEP 4] 导出项目授权包 —— 本服务 admin API")
    print(f"  类型 : {args.export_type}")
    if args.check_meta:
        meta = step4_meta(args.backend_host, project_code, api_key=args.export_key, jwt_token=args.export_token)
        if meta:
            print(f"  [meta 预检] version={meta.get('version')} updated_at={meta.get('updated_at', '-')}")

    try:
        gz_bytes = step4_export_project(
            backend_host=args.backend_host,
            project_code=project_code,
            export_type=args.export_type,
            api_key=args.export_key,
            jwt_token=args.export_token,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"  ✗ 失败: {exc}", file=sys.stderr)
        return 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    gz_path = out_dir / f"project_{project_code}_{args.export_type}_{ts}.gz"
    json_path = out_dir / f"project_{project_code}_{args.export_type}_{ts}.json"

    save_bytes(gz_bytes, gz_path)
    print(f"  ✓ gzip 已保存 ({len(gz_bytes)/1024:.2f} KB): {gz_path}")

    try:
        json_bytes = decompress_gzip(gz_bytes)
        parsed = json.loads(json_bytes.decode("utf-8"))
        save_bytes(json_bytes, json_path)
        print(f"  ✓ json 已保存: {json_path}")
        summary["step4_exported"] = {
            "gz_path": str(gz_path),
            "json_path": str(json_path),
            "version": parsed.get("version"),
            "license_code": (parsed.get("license_code", "") or "")[:40],
            "user_count": len(parsed.get("user_list", [])) if isinstance(parsed.get("user_list"), list) else None,
        }
        if args.pretty:
            print()
            print("  ----- 导出内容预览 -----")
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
            print("  -----------------------")
    except Exception as exc:
        print(f"  [WARN] gzip 解压/解析异常: {exc}，原始 .gz 已保留")
        summary["step4_exported"] = {"gz_path": str(gz_path), "error": str(exc)}
    print()

    # ---- STEP 5 ----
    if args.skip_step5:
        print("[STEP 5] 已按配置/参数跳过")
        summary["step5_skipped"] = True
    else:
        print("[STEP 5] 导入授权验证 —— monitorPlatform")
        print(f"  文件 : {gz_path.name}")
        try:
            import_resp = step5_import_users(
                monitor_host=args.monitor_host,
                gzip_path=gz_path,
                bearer_token=args.monitor_token or "",
                timeout=args.timeout,
            )
        except Exception as exc:
            print(f"  ✗ 失败: {exc}", file=sys.stderr)
            summary["step5"] = {"error": str(exc)}
        else:
            data = import_resp.get("data") or {}
            code = import_resp.get("code")
            msg = import_resp.get("msg", "")
            print(f"  ✓ 响应 code        : {code}")
            print(f"    msg              : {msg}")
            print(f"    licenseActivated : {data.get('licenseActivated')}")
            print(f"    projectCode      : {data.get('projectCode')}")
            if code not in (0, None):
                print(f"  [WARN] monitorPlatform 返回失败，完整响应: {json.dumps(import_resp, ensure_ascii=False)}")
            summary["step5"] = import_resp

    summary["finished_at"] = datetime.now().isoformat()
    sum_path = write_summary(out_dir, summary)
    print()
    print(f"✓ 摘要已写入: {sum_path}")
    print()
    print("全部完成 ✓")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    print(f"=== STEP 4 导出项目授权包 ===")
    print(f"  Host      : {args.backend_host}")
    print(f"  项目编码  : {args.project_code}")
    print(f"  导出类型  : {args.type}")
    print(f"  鉴权      : {'X-API-Key' if args.export_key else 'JWT'}")
    print()

    if args.check_meta:
        print("[meta 预检] ...")
        meta = step4_meta(args.backend_host, args.project_code, api_key=args.export_key, jwt_token=args.export_token)
        if meta:
            print(f"    version={meta.get('version')} updated_at={meta.get('updated_at', '-')}")
        else:
            print("    (跳过或失败)")
        print()

    print("[导出] ...")
    try:
        gz_bytes = step4_export_project(
            backend_host=args.backend_host,
            project_code=args.project_code,
            export_type=args.type,
            api_key=args.export_key,
            jwt_token=args.export_token,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"  ✗ 失败: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    gz_path = out_dir / f"project_{args.project_code}_{args.type}_{ts}.gz"
    json_path = out_dir / f"project_{args.project_code}_{args.type}_{ts}.json"

    save_bytes(gz_bytes, gz_path)
    print(f"  ✓ gzip ({len(gz_bytes)/1024:.2f} KB) → {gz_path}")

    try:
        json_bytes = decompress_gzip(gz_bytes)
        parsed = json.loads(json_bytes.decode("utf-8"))
        save_bytes(json_bytes, json_path)
        print(f"  ✓ json                               → {json_path}")

        if args.pretty:
            print()
            print("----- 导出内容预览 -----")
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
            print("-----------------------")

        if isinstance(parsed, dict):
            lc = parsed.get("license_code", "") or ""
            ul = parsed.get("user_list", [])
            print()
            print("=== 摘要 ===")
            print(f"  version      : {parsed.get('version', '-')}")
            print(f"  license_code : {lc[:40]}{'...' if len(lc) > 40 else ''}" if lc else "  license_code : (空)")
            print(f"  user_list    : {len(ul) if isinstance(ul, list) else 0} 条")
    except Exception as exc:
        print(f"  [WARN] 解压/解析异常: {exc}，原始 .gz 已保留")

    print()
    print("完成 ✓")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    print("=== STEP 3 申请项目授权码 ===")
    print(f"  Host        : {args.backend_host}")
    print(f"  project_code: {args.project_code}")
    print(f"  mac         : {(args.mac or '')[:60]}...")
    print(f"  日期        : {args.start_date} ~ {args.end_date}")
    print(f"  鉴权        : {'URL key' if args.apply_key else 'JWT'}")
    print()

    try:
        resp = step3_apply_project_license(
            backend_host=args.backend_host,
            project_code=args.project_code,
            mac=args.mac,
            start_date=args.start_date,
            end_date=args.end_date,
            max_vehicles=args.max_vehicles,
            apply_key=args.apply_key,
            jwt_token=args.apply_token,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"✗ 失败: {exc}", file=sys.stderr)
        return 1

    print("✓ 响应:")
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    return 0


# ===========================================================================
# 顶层入口
# ===========================================================================

def main() -> int:
    parser = _build_parser()
    raw = parser.parse_args()

    # ---- --init-config 单独处理 ----
    if raw.init_config:
        target = Path(raw.config) if raw.config else (_script_dir() / DEFAULT_CONFIG_NAME)
        if target.exists():
            print(f"已存在：{target}（不覆盖，已退出）")
            return 0
        _generate_example_config(target)
        print(f"✓ 示例配置已生成：{target}")
        print("  用编辑器打开填好 host/key，然后就可以：")
        print(f"    python {Path(__file__).name} run       # 全流程")
        print(f"    python {Path(__file__).name} export    # 只导出")
        return 0

    # ---- 无子命令：提示帮助 ----
    if not raw.cmd:
        parser.print_help()
        print()
        print("  提示：先执行 --init-config 生成配置文件")
        return 1

    # ---- 加载配置 ----
    cfg_path = _resolve_config_path(raw.config)
    try:
        cfg = _load_config(cfg_path)
    except Exception as exc:
        print(f"✗ 配置加载失败：{exc}", file=sys.stderr)
        return 1

    _merge_config_into_args(raw, cfg)
    _fill_defaults(raw)

    # ---- 打印生效的配置摘要 ----
    _dump_effective_config(raw, cfg_loaded=bool(cfg), cfg_path=cfg_path)

    # ---- 校验 ----
    rc = _validate_args(raw)
    if rc:
        return rc

    # ---- 分发 ----
    dispatch = {"run": cmd_run, "export": cmd_export, "apply": cmd_apply}
    return dispatch[raw.cmd](raw)


if __name__ == "__main__":
    sys.exit(main())
