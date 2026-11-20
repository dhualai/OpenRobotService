# -*- coding: utf-8 -*-
"""spec_doc_parser 单元测试：文档解析 + 内嵌图片外置/降级链路。

纯函数测试，不依赖 DB/MinIO：image_handler 用回调桩模拟外置成功/失败。
docx 用 zipfile 手工构造（含 1 张内嵌图），覆盖 mammoth convert_image 链路。
"""
import base64
import io
import zipfile

import pytest

from app.utils.spec_doc_parser import (
    MAX_DOC_SIZE,
    SpecDocParseError,
    externalize_inline_images,
    parse_spec_document,
)

# 1x1 红色 PNG（魔数完整，仅供 mammoth 读取字节，无解码要求）
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>
"""

# 正文：一段文字 + 一张内嵌图（wp:inline 标准 OOXML drawing）
_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
  <w:body>
    <w:p><w:r><w:t>问题说明文字</w:t></w:r></w:p>
    <w:p>
      <w:r>
        <w:drawing>
          <wp:inline distT="0" distB="0" distL="0" distR="0">
            <wp:extent cx="100" cy="100"/>
            <wp:docPr id="1" name="Picture 1"/>
            <a:graphic>
              <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
                <pic:pic>
                  <pic:nvPicPr>
                    <pic:cNvPr id="1" name="image1.png"/>
                    <pic:cNvPicPr/>
                  </pic:nvPicPr>
                  <pic:blipFill>
                    <a:blip r:embed="rId5"/>
                    <a:stretch><a:fillRect/></a:stretch>
                  </pic:blipFill>
                  <pic:spPr>
                    <a:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></a:xfrm>
                    <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
                  </pic:spPr>
                </pic:pic>
              </a:graphicData>
            </a:graphic>
          </wp:inline>
        </w:drawing>
      </w:r>
    </w:p>
  </w:body>
</w:document>
"""


def _make_docx() -> bytes:
    """构造含 1 张内嵌 PNG 的最小 .docx 字节流。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("word/document.xml", _DOCUMENT)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/media/image1.png", _PNG_1PX)
    return buf.getvalue()


# ── 文本类直读 ──


def test_parse_markdown_direct():
    md = parse_spec_document("说明.md", "# 标题\n正文".encode("utf-8"))
    assert "# 标题" in md


def test_parse_txt_gb18030_fallback():
    raw = "中文内容".encode("gb18030")
    md = parse_spec_document("note.txt", raw)
    assert "中文内容" in md


# ── 参数校验 ──


def test_reject_unsupported_ext():
    with pytest.raises(SpecDocParseError):
        parse_spec_document("a.exe", b"MZ...")


def test_reject_empty():
    with pytest.raises(SpecDocParseError):
        parse_spec_document("a.md", b"")


def test_reject_oversize():
    with pytest.raises(SpecDocParseError):
        parse_spec_document("a.md", b"x" * (MAX_DOC_SIZE + 1))


def test_reject_bad_docx_magic():
    # PK 魔数但非 zip 结构 → mammoth 报错转 SpecDocParseError
    with pytest.raises(SpecDocParseError):
        parse_spec_document("a.docx", b"PK\x03\x04" + b"garbage" * 8)


# ── docx 内嵌图片：外置 / 降级链路 ──


def test_docx_image_externalized_via_handler():
    """image_handler 返回 URL → markdown 引用 URL，且不残留 base64 内联。"""
    docx = _make_docx()
    calls = []

    def handler(data: bytes, content_type: str) -> str:
        calls.append((data, content_type))
        return "/api/tasks/files/helpdesk-comment/spec-doc/images/abc.png"

    md = parse_spec_document("spec.docx", docx, handler)

    assert len(calls) == 1
    assert calls[0][0] == _PNG_1PX
    assert calls[0][1] == "image/png"
    assert "/api/tasks/files/helpdesk-comment/spec-doc/images/abc.png" in md
    assert "data:image" not in md  # 不再内联 base64
    assert "问题说明文字" in md


def test_docx_image_handler_failure_fallback_data_uri():
    """image_handler 抛异常 → 降级内联 base64（图片永不丢）。"""
    docx = _make_docx()

    def boom(data: bytes, content_type: str) -> str:
        raise RuntimeError("minio down")

    md = parse_spec_document("spec.docx", docx, boom)
    assert "data:image/png;base64," in md
    assert "问题说明文字" in md


# ── 存量正文治理：externalize_inline_images（超长 base64 单行外置） ──


def _data_uri_of(size_bytes: int) -> str:
    """构造指定原始字节数的可解码 base64 data URI（PNG 头 + 零填充）。"""
    payload = _PNG_1PX + b"\x00" * max(0, size_bytes - len(_PNG_1PX))
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def test_externalize_replaces_large_inline_image():
    """超过阈值的内联图 → 替换为 handler 返回的 URL，正文不再含 data:image。"""
    big = _data_uri_of(20 * 1024)
    md = f"说明\n\n![图]({big})\n结尾"
    calls = []

    def handler(data: bytes, content_type: str) -> str:
        calls.append((len(data), content_type))
        return "/api/tasks/files/bucket/spec-doc/images/x.png"

    new_md, replaced = externalize_inline_images(md, handler)

    assert replaced == 1
    assert "/api/tasks/files/bucket/spec-doc/images/x.png" in new_md
    assert "data:image" not in new_md
    assert "说明" in new_md and "结尾" in new_md
    assert calls[0][1] == "image/png"
    assert calls[0][0] >= 20 * 1024  # 解码后的原始字节传给了 handler


def test_externalize_keeps_small_inline_image():
    """小于阈值的内联图（图标类）保持原样，避免无谓请求。"""
    small = _data_uri_of(1024)
    md = f"![icon]({small})"
    new_md, replaced = externalize_inline_images(md, lambda d, ct: "/never")
    assert replaced == 0
    assert new_md == md


def test_externalize_keeps_inline_on_handler_failure():
    """外置失败（返回空串/抛异常）→ 保持内联，图片永不丢。"""
    big = _data_uri_of(20 * 1024)
    md = f"![图]({big})"
    assert externalize_inline_images(md, lambda d, ct: "") == (md, 0)

    def boom(data: bytes, content_type: str) -> str:
        raise RuntimeError("minio down")

    assert externalize_inline_images(md, boom) == (md, 0)


def test_externalize_idempotent_and_noop():
    """已治理正文（无 data:image）/ 无图正文 → 原样返回，可安全重跑。"""
    md_url = "说明\n\n![图](/api/tasks/files/bucket/spec-doc/images/x.png)"
    assert externalize_inline_images(md_url, lambda d, ct: "/never") == (md_url, 0)

    md_plain = "普通正文，无内联图片"
    assert externalize_inline_images(md_plain, lambda d, ct: "/never") == (md_plain, 0)


def test_externalize_multiple_images_in_one_line():
    """同一行内多张超长内联图（836 工单形态）逐张外置，正文显著缩短。"""
    a, b = _data_uri_of(10 * 1024), _data_uri_of(12 * 1024)
    md = f"![a]({a})    ![b]({b})"
    urls = iter(["/api/tasks/files/x1.png", "/api/tasks/files/x2.png"])
    new_md, replaced = externalize_inline_images(md, lambda d, ct: next(urls))

    assert replaced == 2
    assert "data:image" not in new_md
    assert "/api/tasks/files/x1.png" in new_md
    assert "/api/tasks/files/x2.png" in new_md
    assert len(new_md) < 200


def test_docx_image_handler_empty_fallback_data_uri():
    """image_handler 返回空串（上传失败）→ 降级内联 base64。"""
    docx = _make_docx()
    md = parse_spec_document("spec.docx", docx, lambda d, c: "")
    assert "data:image/png;base64," in md


def test_docx_no_handler_keeps_legacy_base64():
    """不传 handler → 旧行为：全部内联 base64（存量兼容）。"""
    docx = _make_docx()
    md = parse_spec_document("spec.docx", docx)
    assert "data:image/png;base64," in md


def test_docx_text_only():
    """无图 docx：纯文本正常转 markdown。"""
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>问题说明文字</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>第二段</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("word/document.xml", document)
    md = parse_spec_document("plain.docx", buf.getvalue())
    assert "问题说明文字" in md
    assert "data:image" not in md
