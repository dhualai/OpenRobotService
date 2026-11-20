# -*- coding: utf-8 -*-
"""KB md → html 转换器（dar_studio 知识库预览抽屉用，零依赖）。

覆盖 KB 文档实际用到的语法：标题/段落/围栏代码块/管道表格/列表/引用/分割线/
图片（media/ 相对路径改写为 /api/kb_media 代理）/外链/行内粗体·斜体·行内码。
HTML 特殊字符先转义再叠加行内标记，源文档里的尖括号不会被浏览器解析。
"""
import re
from html import escape
from urllib.parse import quote


def _inline(s: str) -> str:
    s = escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\s][^*]*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)",
               lambda m: '<img loading="lazy" src="' + _img_url(m.group(2)) + '" alt="' + m.group(1) + '">',
               s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               lambda m: '<a href="' + m.group(2) + '" target="_blank">' + m.group(1) + "</a>"
               if m.group(2).startswith(("http://", "https://")) else m.group(1),
               s)
    return s


def _img_url(src: str) -> str:
    if src.startswith(("http://", "https://", "data:")):
        return escape(src)
    return escape(src.replace("\\", "/").lstrip("./"))


def md_to_html(md: str, base_dir: str = "") -> str:
    out = []
    lines = md.split("\n")
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            buf = []
            i += 1
            while i < n and not lines[i].lstrip().startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append("<pre><code>" + escape("\n".join(buf)) + "</code></pre>")
            i += 1
            continue
        h = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if h:
            lvl = len(h.group(1))
            out.append("<h" + str(lvl) + ">" + _inline(h.group(2)) + "</h" + str(lvl) + ">")
            i += 1
            continue
        if ln.lstrip().startswith("|"):
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(lines[i])
                i += 1
            sep = next((k for k, r in enumerate(rows) if re.fullmatch(r"[\s|:\-]+", r)), None)
            head = rows[:1]
            body = rows[1:] if sep is None else rows[sep + 1:]
            out.append("<table>")
            if head:
                out.append("<tr>" + "".join("<th>" + _inline(c.strip()) + "</th>"
                                            for c in _split_row(head[0])) + "</tr>")
            for r in body:
                out.append("<tr>" + "".join("<td>" + _inline(c.strip()) + "</td>"
                                            for c in _split_row(r)) + "</tr>")
            out.append("</table>")
            continue
        if re.match(r"^\s*([-*]|\d+[.)])\s+", ln):
            items = []
            while i < n and re.match(r"^\s*([-*]|\d+[.)])\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+[.)])\s+", "", lines[i]))
                i += 1
            ordered = bool(re.match(r"^\s*\d", ln))
            tag = "ol" if ordered else "ul"
            out.append("<" + tag + ">" + "".join("<li>" + _inline(x) + "</li>" for x in items) + "</" + tag + ">")
            continue
        if ln.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + _inline(" ".join(buf)) + "</blockquote>")
            continue
        if re.fullmatch(r"\s*[-*_]{3,}\s*", ln):
            out.append("<hr>")
            i += 1
            continue
        if not ln.strip():
            i += 1
            continue
        out.append("<p>" + _inline(ln) + "</p>")
        i += 1
    return "\n".join(out)


def _split_row(line: str) -> list:
    cells = line.strip().strip("|").split("|")
    out = []
    for c in cells:
        c = c.strip()
        out.append(c)
    return out
