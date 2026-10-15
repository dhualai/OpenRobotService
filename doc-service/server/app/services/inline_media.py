from __future__ import annotations

import base64
import hashlib
import re
import threading
from collections import OrderedDict

_LOCK = threading.Lock()
_CACHE: OrderedDict[str, tuple[str, bytes]] = OrderedDict()
_TOTAL = 0
_MAX_ITEMS = 64
_MAX_BYTES = 80 * 1024 * 1024

_DATA_ATTR = re.compile(
    r"(?P<attr>src|href|xlink:href)\s*=\s*(?P<q>['\"])"
    r"data:(?P<mime>image/(?:png|jpe?g|gif|webp|svg\+xml));base64,"
    r"(?P<b64>[A-Za-z0-9+/=\s]+)(?P=q)",
    re.I,
)

SHA_RE = re.compile(r"^[a-f0-9]{64}$")


def put(mime: str, data: bytes) -> str:
    global _TOTAL
    h = hashlib.sha256(data).hexdigest()
    with _LOCK:
        if h in _CACHE:
            _CACHE.move_to_end(h)
            return h
        while _CACHE and (_TOTAL + len(data) > _MAX_BYTES or len(_CACHE) >= _MAX_ITEMS):
            _, old = _CACHE.popitem(last=False)
            _TOTAL -= len(old[1])
        _CACHE[h] = (mime, data)
        _TOTAL += len(data)
    return h


def get(sha: str) -> tuple[str, bytes] | None:
    if not SHA_RE.match(sha or ""):
        return None
    with _LOCK:
        item = _CACHE.get(sha)
        if item:
            _CACHE.move_to_end(sha)
        return item


def rewrite_html(html: str) -> str:
    if not html or "data:image" not in html:
        return html or ""

    def repl(m: re.Match[str]) -> str:
        mime = m.group("mime").lower()
        if mime == "image/jpg":
            mime = "image/jpeg"
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", m.group("b64")), validate=False)
        except Exception:
            return m.group(0)
        if not raw:
            return m.group(0)
        h = put(mime, raw)
        return f"{m.group('attr')}={m.group('q')}/api/v1/inline-media/{h}{m.group('q')}"

    return _DATA_ATTR.sub(repl, html or "")
