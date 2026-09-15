"""工单「问题文档」上传解析：.md/.txt 直读，.docx → mammoth，.doc → LibreOffice → mammoth。

安全红线：
- 扩展名白名单（md/markdown/txt/doc/docx）
- 魔数校验（.doc = D0CF11E0，.docx = PK）
- 大小上限
- soffice 以 argv 列表调用（禁 shell 拼接），输入内容落在文件里而非命令行参数
- 临时文件固定在 mkdtemp 目录，解析后清理；并发用独立 UserInstallation 隔离

内嵌图片处理：
- 可传 image_handler(bytes, content_type) -> str 把 Word 内嵌图片外置到对象存储，
  markdown 里只存引用 URL（治本：避免 base64 内联把正文撑到 MB 级、渲染卡顿）。
- image_handler 缺省 / 抛异常 / 返回空串时，自动降级为内联 base64 data URI（图片永不丢）。

注意：.doc 解析依赖系统 LibreOffice（soffice）；未安装时抛 SpecDocParseError。
"""
import base64
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 内嵌图片外置回调：(图片字节, content_type) -> 引用 URL；返回空串表示外置失败
ImageHandler = Callable[[bytes, str], str]

# 允许上传的文档扩展名
ALLOWED_EXTS = {".md", ".markdown", ".txt", ".doc", ".docx"}
# 单文档大小上限（5MB）
MAX_DOC_SIZE = 5 * 1024 * 1024
# soffice 转换超时（秒）
_SOFFICE_TIMEOUT = 60

# mammoth 输出的 `<a id="..."></a>` 锚点（word 书签），markdown 渲染无意义，清理掉
_MAMMOTH_ANCHOR_RE = re.compile(r'<a\s+id="[^"]*"\s*>\s*</a>')


class SpecDocParseError(ValueError):
    """文档解析失败（面向用户可读的错误信息）。"""


def ext_of(filename: str) -> str:
    """取小写扩展名（含点）。"""
    return os.path.splitext(filename or "")[1].lower()


def parse_spec_document(
    filename: str,
    raw: bytes,
    image_handler: Optional[ImageHandler] = None,
) -> str:
    """把上传的文档字节解析为 markdown 文本。

    :param image_handler: 可选回调，Word 内嵌图片经它外置（返回引用 URL）；
        缺省/失败时降级为内联 base64 data URI（保持旧行为）。
    :raises SpecDocParseError: 扩展名不支持 / 超大 / 空文件 / 魔数不符 / 解析失败
    """
    ext = ext_of(filename)
    if ext not in ALLOWED_EXTS:
        raise SpecDocParseError(
            f"不支持的文件类型 {ext or '(无扩展名)'}，仅支持 .md / .markdown / .txt / .doc / .docx"
        )
    if not raw:
        raise SpecDocParseError("文件内容为空")
    if len(raw) > MAX_DOC_SIZE:
        raise SpecDocParseError(f"文件过大（{len(raw) // 1024 // 1024}MB），上限 5MB")
    _check_magic(ext, raw)

    if ext in (".md", ".markdown", ".txt"):
        return _decode_text(raw)
    if ext == ".docx":
        return _clean_markdown(_mammoth_markdown(raw, image_handler))
    if ext == ".doc":
        return _clean_markdown(_parse_doc(raw, image_handler))
    raise SpecDocParseError(f"暂不支持的文档类型 {ext}")


def _check_magic(ext: str, raw: bytes) -> None:
    """按扩展名校验文件魔数，拦截改名的伪装文件。"""
    if ext == ".doc" and not raw.startswith(b"\xd0\xcf\x11\xe0"):
        raise SpecDocParseError("文件不是有效的 .doc（老 Word 二进制格式）")
    if ext == ".docx" and not raw.startswith(b"PK\x03\x04"):
        raise SpecDocParseError("文件不是有效的 .docx（OOXML 格式）")


def _decode_text(raw: bytes) -> str:
    """文本文件解码：UTF-8(BOM) → UTF-8 → GB18030 兜底。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _data_uri(data: bytes, content_type: str) -> str:
    """内联 base64 data URI（图片外置失败时的降级，保证图片永不丢）。"""
    ct = (content_type or "").strip() or "image/png"
    return f"data:{ct};base64," + base64.b64encode(data).decode("ascii")


def _mammoth_image_converter(image_handler: Optional[ImageHandler]):
    """构造 mammoth 的 convert_image 转换器：内嵌图片 → image_handler URL；
    handler 缺省/抛异常/返回空 → 降级内联 base64。"""
    import mammoth

    def handle_image(image):
        with image.open() as f:
            data = f.read()
        src = ""
        if image_handler is not None:
            try:
                src = (image_handler(data, image.content_type or "") or "").strip()
            except Exception as e:  # noqa: BLE001 - 外置失败降级内联，不中断解析
                logger.warning("[spec_doc] 内嵌图片外置失败，降级 base64 内联: %s", e)
                src = ""
        if not src:
            src = _data_uri(data, image.content_type or "")
        return {"src": src}

    return mammoth.images.img_element(handle_image)


def _mammoth_markdown(raw: bytes, image_handler: Optional[ImageHandler] = None) -> str:
    """mammoth 将 .docx 字节转为 markdown（内嵌图片经 image_handler 外置）。"""
    try:
        import mammoth
    except ImportError as e:  # pragma: no cover - 环境未装依赖
        raise SpecDocParseError("服务器缺少 mammoth 依赖，无法解析 Word 文档") from e
    try:
        result = mammoth.convert_to_markdown(
            io.BytesIO(raw),
            convert_image=_mammoth_image_converter(image_handler),
        )
    except Exception as e:  # noqa: BLE001 - 统一转用户可读错误
        logger.warning("[spec_doc] mammoth 解析失败: %s", e)
        raise SpecDocParseError("Word 文档解析失败，请确认文件未损坏") from e
    return result.value or ""


def _parse_doc(raw: bytes, image_handler: Optional[ImageHandler] = None) -> str:
    """老 .doc 二进制格式：LibreOffice headless 转 .docx，再 mammoth 转 markdown。"""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise SpecDocParseError("服务器未安装 LibreOffice，暂无法解析 .doc，请另存为 .docx 后重试")

    tmp_dir = tempfile.mkdtemp(prefix="specdoc_")
    try:
        src_path = os.path.join(tmp_dir, "input.doc")
        with open(src_path, "wb") as f:
            f.write(raw)
        # argv 列表调用，禁 shell；输入内容在文件内，命令行只出现固定文件名
        proc = subprocess.run(
            [
                soffice,
                f"-env:UserInstallation=file://{tmp_dir}/lo_profile",
                "--headless",
                "--norestore",
                "--convert-to", "docx",
                "--outdir", tmp_dir,
                src_path,
            ],
            capture_output=True,
            timeout=_SOFFICE_TIMEOUT,
            check=False,
        )
        out_path = os.path.join(tmp_dir, "input.docx")
        if not os.path.exists(out_path):
            logger.warning(
                "[spec_doc] soffice 转换未产出 docx: rc=%s stderr=%s",
                proc.returncode, (proc.stderr or b"")[:300],
            )
            raise SpecDocParseError("Word 文档（.doc）转换失败，请另存为 .docx 后重试")
        with open(out_path, "rb") as f:
            docx_raw = f.read()
        return _mammoth_markdown(docx_raw, image_handler)
    except subprocess.TimeoutExpired as e:
        raise SpecDocParseError("Word 文档转换超时，请尝试拆分或另存为 .docx") from e
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _clean_markdown(md: str) -> str:
    """清理 mammoth 输出中的无意义锚点标签。"""
    return _MAMMOTH_ANCHOR_RE.sub("", md or "").strip()
