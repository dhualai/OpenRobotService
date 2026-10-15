"""Optional freeze for a site that has moved.

When a migrated URL is set, document writes are refused. Login, reading,
and the USP test-system jump stay available.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.settings import get_settings

_DEFAULT_FILE = "/home/topo/migrated.url"

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/api/v1/products/[^/]+/packs/[^/]+$")),
    ("POST", re.compile(r"^/api/v1/products/[^/]+/requirements/[^/]+/pack$")),
    ("POST", re.compile(r"^/api/v1/drafts/[^/]+/pack$")),
    ("PUT", re.compile(r"^/api/v1/products/[^/]+/modules/[^/]+/document$")),
    ("POST", re.compile(r"^/api/v1/products/[^/]+/modules/[^/]+/revisions/restore$")),
    ("POST", re.compile(r"^/api/v1/products/[^/]+/requirements/[^/]+/revisions/restore$")),
    ("PUT", re.compile(r"^/api/v1/products/[^/]+/boards/.+")),
)


def migrated_url() -> str:
    url = (get_settings().topo_migrated_url or "").strip()
    if not url:
        path = os.environ.get("TOPO_MIGRATED_URL_FILE", _DEFAULT_FILE)
        try:
            url = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            url = ""
    return url.rstrip("/")


def migrated_notice() -> str:
    url = migrated_url()
    if not url:
        return ""
    return f"本站已迁移至 {url}，请登录新地址更新。"


def doc_update_closed(method: str, path: str) -> bool:
    if not migrated_url():
        return False
    method = method.upper()
    return any(rule_method == method and rx.match(path) for rule_method, rx in _RULES)
