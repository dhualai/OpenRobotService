from app.services.inline_media import get as media_get
from app.services.markdown_html import md_to_html, rewrite_doc_hrefs

_PNG1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_md_keeps_tex_delimiters():
    html = md_to_html(
        "给定 \\(\\mathcal{T}\\) 与 \\(\\mathcal{R}\\)\n\n"
        "\\[\n"
        "\\mu:\\mathcal{T}'\\rightarrow\\mathcal{R}\n"
        "\\]\n\n"
        "| 项 | 含义 |\n|----|------|\n"
        "| \\(d(r,\\mathrm{start}(\\tau))\\) | 距 |\n"
    )
    assert "\\(\\mathcal{T}\\)" in html
    assert "\\[" in html and "\\]" in html
    assert "\\mathrm{start}" in html
    assert "<em>" not in html


def test_md_keeps_svg_blank_lines():
    html = md_to_html(
        "## 图\n\n"
        '<svg viewBox="0 0 100 100">\n'
        '  <rect x="0" y="0" width="10" height="10"></rect>\n'
        '  <line x1="0" y1="0" x2="10" y2="10"></line>\n'
        "\n"
        '  <rect x="20" y="20" width="10" height="10"></rect>\n'
        '  <text x="0" y="40">Layer 1 · 部门 DeptRouter</text>\n'
        "</svg>\n"
    )
    inner = html[html.find("<svg") : html.find("</svg>")]
    assert html.count("<svg") == 1
    assert "</p>" not in inner
    assert inner.count("<rect") == 2
    assert "Layer 1 · 部门 DeptRouter" in inner


def test_md_rewrites_static_module_and_version_hrefs():
    html = rewrite_doc_hrefs(
        '<a href="ors_e49e8488-v1.0.html">v1.0</a>'
        '<a href="./biz-v1.0.html">上版本</a>'
        '<a href="ors_3a7f10c2.html">意图识别</a>'
        '<a href="./um_6e3357f6.html">v1.0 文件</a>'
        '<a href="https://example.com/x.html">外</a>'
        '<a href="account.html">账号</a>',
        "ors",
    )
    assert 'href="/ors/m/ors_e49e8488?v=v1.0"' in html
    assert 'href="/ors/m/biz?v=v1.0"' in html
    assert 'href="/ors/m/ors_3a7f10c2"' in html
    assert 'href="/ors/m/um_6e3357f6"' in html
    assert 'href="https://example.com/x.html"' in html
    assert 'href="/ors/account"' in html
    rendered = md_to_html("> 仍以 [v1.0](ors_e49e8488-v1.0.html) 为准。", product="ors")
    assert "/ors/m/ors_e49e8488?v=v1.0" in rendered
    assert "ors_e49e8488-v1.0.html" not in rendered


def test_md_rewrites_version_file_alias_to_same_module():
    html = rewrite_doc_hrefs(
        '<a href="./um_6e3357f6.html">上版本-v1.0</a>'
        '<a href="biz.html">业务</a>',
        "usp",
        module_id="um_5a6603dd",
        versions=["v1.0", "v1.1"],
        current_version="v1.1",
        known_ids={"um_5a6603dd", "biz"},
    )
    assert 'href="/usp/m/um_5a6603dd?v=v1.0"' in html
    assert 'href="/usp/m/biz"' in html
    assert "/usp/m/um_6e3357f6" not in html


def test_md_extracts_inline_png():
    html = md_to_html(
        f'<svg viewBox="0 0 10 10">\n'
        f'  <image href="data:image/png;base64,{_PNG1}" width="10" height="10"></image>\n'
        f"</svg>\n"
    )
    assert "data:image/png;base64," not in html
    assert "/api/v1/inline-media/" in html
    sha = html.split("/api/v1/inline-media/")[1].split('"')[0]
    item = media_get(sha)
    assert item is not None
    assert item[0] == "image/png"
    assert item[1][:8] == b"\x89PNG\r\n\x1a\n"
