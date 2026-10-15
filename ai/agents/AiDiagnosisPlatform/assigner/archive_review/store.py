"""归档审核人名单的读写（叶子模块）。

只依赖 pathlib + PyYAML + domains；供后端开发者模式 / submit 派审直接 import，
不经过 assigner.settings / dual_tree / pipeline。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ai.agents.AiDiagnosisPlatform.assigner.archive_review.domains import DOMAINS

try:
    import yaml

    def _load_yaml(path: Path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PyYAML 是必要依赖，请安装: pip install pyyaml") from exc


# assigner/config/{config.yaml, runtime_overrides.yaml}
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_CONFIG_YAML = _CONFIG_DIR / "config.yaml"
_OVERRIDE_FILE = _CONFIG_DIR / "runtime_overrides.yaml"

_ARCHIVE_REVIEWERS_CACHE: Dict[str, Any] | None = None
_ARCHIVE_REVIEWERS_MTIMES: tuple[float | None, float | None] | None = None


def _file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime if path.exists() else None
    except OSError:
        return None


def _archive_reviewers_mtimes() -> tuple[float | None, float | None]:
    return _file_mtime(_CONFIG_YAML), _file_mtime(_OVERRIDE_FILE)


def invalidate_archive_reviewers_cache() -> None:
    """开发者模式保存后或测试里强制清缓存。"""
    global _ARCHIVE_REVIEWERS_CACHE, _ARCHIVE_REVIEWERS_MTIMES
    _ARCHIVE_REVIEWERS_CACHE = None
    _ARCHIVE_REVIEWERS_MTIMES = None


def _load_runtime_overrides() -> dict:
    if not _OVERRIDE_FILE.exists():
        return {}
    try:
        data = _load_yaml(_OVERRIDE_FILE) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_archive_reviewers(
    domains: Any = None,
    *,
    on_empty: str | None = "fail",
) -> dict:
    """开发者模式写入前校验：领域 → users.id 列表。"""
    if not isinstance(domains, dict):
        raise ValueError("domains 必须是对象：问题领域 → 审核人 id 列表")
    out_domains: Dict[str, list] = {}
    known = set(DOMAINS)
    for name, ids in domains.items():
        key = str(name or "").strip()
        if not key:
            continue
        if known and key not in known:
            raise ValueError(f"未知问题领域：{key}")
        if ids is None:
            ids = []
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, (list, tuple)):
            raise ValueError(f"领域「{key}」的审核人必须是列表")
        seen = set()
        clean: list = []
        for raw in ids:
            uid = str(raw or "").strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            clean.append(uid)
        out_domains[key] = clean
    for d in DOMAINS:
        out_domains.setdefault(d, [])
    empty = (on_empty or "fail").strip().lower() or "fail"
    if empty != "fail":
        raise ValueError("on_empty 目前只支持 fail")
    return {"domains": out_domains, "on_empty": empty}


def save_archive_reviewers_overrides(
    domains: Any = None,
    *,
    on_empty: str | None = "fail",
) -> dict:
    """把归档审核人名单写进 runtime_overrides.yaml（整块覆盖，不改 config.yaml）。"""
    patch = normalize_archive_reviewers(domains, on_empty=on_empty)
    data = _load_runtime_overrides()
    data["archive_reviewers"] = patch
    _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_OVERRIDE_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    invalidate_archive_reviewers_cache()
    return patch


def effective_archive_reviewers() -> dict:
    """生效配置：runtime_overrides 整块覆盖 config.yaml 默认。

    按两份 yaml 的 mtime 缓存；文件变更或 save 后自动失效。
    """
    global _ARCHIVE_REVIEWERS_CACHE, _ARCHIVE_REVIEWERS_MTIMES

    mtimes = _archive_reviewers_mtimes()
    if (
        _ARCHIVE_REVIEWERS_CACHE is not None
        and _ARCHIVE_REVIEWERS_MTIMES == mtimes
    ):
        return _ARCHIVE_REVIEWERS_CACHE

    base = {}
    try:
        raw = _load_yaml(_CONFIG_YAML) or {}
        if isinstance(raw.get("archive_reviewers"), dict):
            base = dict(raw["archive_reviewers"])
    except Exception:
        base = {}
    extra = _load_runtime_overrides()
    ov = extra.get("archive_reviewers") if isinstance(extra, dict) else None
    src = ov if isinstance(ov, dict) and ov else base
    domains_raw = src.get("domains") if isinstance(src, dict) else {}
    domains: Dict[str, list] = {}
    if isinstance(domains_raw, dict):
        for name, ids in domains_raw.items():
            key = str(name or "").strip()
            if not key:
                continue
            if isinstance(ids, str):
                ids = [ids]
            if not isinstance(ids, (list, tuple)):
                continue
            domains[key] = [str(x).strip() for x in ids if str(x).strip()]
    for d in DOMAINS:
        domains.setdefault(d, [])
    result = {
        "domains": domains,
        "on_empty": str((src or {}).get("on_empty") or "fail"),
        "domain_order": list(DOMAINS),
        "source": "runtime_overrides" if isinstance(ov, dict) and ov else "config.yaml",
    }
    _ARCHIVE_REVIEWERS_CACHE = result
    _ARCHIVE_REVIEWERS_MTIMES = mtimes
    return result
