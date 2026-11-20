from __future__ import annotations

import re

import markdown as md

from app.services.inline_media import rewrite_html
from app.services.legacy_links import convert_legacy_href

_PARK_MATH = (
    re.compile(r"\$\$.+?\$\$", re.DOTALL),
    re.compile(r"\\\[.+?\\\]", re.DOTALL),
    re.compile(r"\\\(.+?\\\)"),
    re.compile(r"(?<!\$)\$(?!\$)(?:\\.|[^$\n])+?(?<!\$)\$(?!\$)"),
)
_BLOCK_TAGS = ("div", "svg", "figure")
_BLOCK_OPEN = re.compile(r"</?(div|svg|figure)\b[^>]*>", re.I)


def _depth_delta(line: str) -> int:
    d = 0
    for m in _BLOCK_OPEN.finditer(line):
        tag = m.group(0)
        if tag.startswith("</"):
            d -= 1
        elif tag.endswith("/>"):
            continue
        else:
            d += 1
    return d


def _park_html_blocks(text: str, slots: list[str]) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        tag = next(
            (
                t
                for t in _BLOCK_TAGS
                if stripped.startswith(f"<{t} ") or stripped.startswith(f"<{t}>")
            ),
            None,
        )
        if not tag:
            out.append(lines[i])
            i += 1
            continue
        buf = [lines[i]]
        depth = _depth_delta(lines[i])
        i += 1
        while i < len(lines) and depth > 0:
            buf.append(lines[i])
            depth += _depth_delta(lines[i])
            i += 1
        slots.append("\n".join(buf))
        out.append(f"@@BLK{len(slots) - 1}@@")
    return "\n".join(out)


_HREF_RE = re.compile(r"""href\s*=\s*(['"])([^'"]+)\1""", re.I)


def rewrite_doc_hrefs(
    html: str,
    product: str,
    *,
    module_id: str | None = None,
    versions: list[str] | None = None,
    current_version: str | None = None,
    known_ids: set[str] | None = None,
    ids_by_product: dict[str, set[str]] | None = None,
) -> str:
    """Map legacy static module files to SPA routes: id-v1.0.html → /{product}/m/id?v=v1.0."""

    def repl(m: re.Match[str]) -> str:
        quote, raw = m.group(1), (m.group(2) or "").strip()
        dest = convert_legacy_href(
            raw,
            product=product,
            module_id=module_id,
            versions=versions,
            current_version=current_version,
            ids_by_product=ids_by_product,
            known_ids=known_ids,
        )
        return f"href={quote}{dest}{quote}"

    return _HREF_RE.sub(repl, html or "")


def md_to_html(
    text: str | None,
    *,
    product: str | None = None,
    module_id: str | None = None,
    versions: list[str] | None = None,
    current_version: str | None = None,
    known_ids: set[str] | None = None,
    ids_by_product: dict[str, set[str]] | None = None,
) -> str:
    """Markdown to HTML; park SVG/HTML blocks and TeX so they survive conversion."""
    slots: list[str] = []
    s = _park_html_blocks(text or "", slots)

    def park_math(m: re.Match[str]) -> str:
        slots.append(m.group(0))
        return f"@@BLK{len(slots) - 1}@@"

    for pat in _PARK_MATH:
        s = pat.sub(park_math, s)
    html = md.markdown(s, extensions=["tables", "fenced_code"])
    for i in range(len(slots) - 1, -1, -1):
        token = f"@@BLK{i}@@"
        raw = slots[i]
        html = html.replace(f"<p>{token}</p>", raw)
        html = html.replace(token, raw)
    html = rewrite_html(html)
    if product:
        html = rewrite_doc_hrefs(
            html,
            product,
            module_id=module_id,
            versions=versions,
            current_version=current_version,
            known_ids=known_ids,
            ids_by_product=ids_by_product,
        )
    return html
