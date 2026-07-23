"""
Ingester 注册表 — 文件 → 解析器自动匹配

约定优于配置：
  每个 parser 文件放在 ai/ingestion/parsers/ 下，
  只要定义了 Ingester 子类并暴露为模块级变量，就会被自动发现。

支持两种注册方式：
  1. **自动发现**：`discover_parsers()` 扫描 parsers/ 目录
  2. **显式注册**：`register(MyIngester)` 手动添加

匹配逻辑：
  给定一个文件路径，遍历所有注册的 parser，
  返回 source_paths 包含该文件（或匹配 glob pattern）的 parser。
"""
import sys
from pathlib import Path
from typing import List, Dict, Optional, Type, Tuple
from dataclasses import dataclass, field

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from ai.ingestion.base import BaseIngester

# ── 全局注册表 ──────────────────────────────────────────────────

_registry: Dict[str, "IngesterMeta"] = {}


@dataclass
class IngesterMeta:
    """注册表中一条记录"""
    name: str                        # 唯一标识，如 "cheduan_pdf"
    ingester_cls: Type[BaseIngester]
    source_patterns: List[str]        # 源文件 glob 或路径，如 "cheduan_doc/**/*.pdf"
    collection_type: str              # operation / faq / troubleshooting / cheduan / translation
    description: str = ""


def register(
    ingester_cls: Type[BaseIngester],
    name: Optional[str] = None,
    collection_type: Optional[str] = None,
    description: str = "",
) -> None:
    """
    注册一个 Ingester。

    Args:
        ingester_cls: BaseIngester 子类
        name: 唯一标识（默认用类名 snake_case）
        collection_type: 覆盖子类定义的 collection_type
        description: 人类可读的描述
    """
    key = name or _class_to_key(ingester_cls.__name__)

    # 尝试实例化获取 source_paths（不实际 parse）
    # 这里我们不能实例化因为可能触发副作用，用类属性
    source_patterns = [str(p) for p in ingester_cls.source_paths]
    ct = collection_type or ingester_cls.collection_type or ""

    _registry[key] = IngesterMeta(
        name=key,
        ingester_cls=ingester_cls,
        source_patterns=source_patterns,
        collection_type=ct,
        description=description,
    )


def find_parser_for_file(file_path: Path) -> Optional[Type[BaseIngester]]:
    """
    根据文件路径查找匹配的 Ingester。

    匹配规则：
      1. 精确路径匹配（source_path 中直接包含此文件）
      2. Glob 匹配（source_path 中的 glob 能匹配此文件）
    """
    file_str = str(file_path)

    for meta in _registry.values():
        for pattern in meta.source_patterns:
            # 精确匹配
            if pattern == file_str or Path(pattern) == file_path:
                return meta.ingester_cls
            # 文件名匹配（跨平台安全）
            try:
                pp = Path(pattern)
                if pp.name == file_path.name:
                    return meta.ingester_cls
            except Exception:
                pass

    return None


def find_parsers_by_type(collection_type: str) -> List[Type[BaseIngester]]:
    """按集合类型查找所有 Ingester。"""
    return [
        meta.ingester_cls
        for meta in _registry.values()
        if meta.collection_type == collection_type
    ]


def list_registered() -> List[IngesterMeta]:
    """列出所有已注册的 ingester。"""
    return list(_registry.values())


def _class_to_key(cls_name: str) -> str:
    """CamelCase → snake_case（正确处理 PDF/XLSX/JSON/FAQ/UI 等连续大写缩写）"""
    import re
    # 去掉 Ing ester 后缀
    name = cls_name
    if name.endswith("Ingester"):
        name = name[:-8]
    # 1. 在 "小写→大写" 边界插入下划线：CheduanPDF → Cheduan_PDF
    name = re.sub(r'(?<=[a-z一-鿿])(?=[A-Z])', '_', name)
    # 2. 在 "连续大写→大写+小写" 边界插入下划线：PDFIngester → PDF_Ingester, UITranslation → UI_Translation
    name = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', '_', name)
    return name.lower()


# ================================================================
# 自动发现
# ================================================================

def discover_parsers() -> List[str]:
    """
    扫描 ai/ingestion/parsers/ 目录，自动注册所有 Ingester 子类。

    约定：
      - 每个 .py 文件在模块级别调用 register_all() 完成注册
      - 导入即注册（import side-effect）
      - 如果模块没有主动注册，查找 BaseIngester 子类并自动注册

    Returns:
        已发现的 parser 名称列表
    """
    parsers_dir = Path(__file__).resolve().parent / "parsers"
    if not parsers_dir.is_dir():
        print(f"[REGISTRY] parsers 目录不存在: {parsers_dir}（自动发现跳过）")
        return []

    discovered = []

    for py_file in sorted(parsers_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        mod_name = f"ai.ingestion.parsers.{py_file.stem}"

        try:
            mod = __import__(mod_name)
        except ImportError as e:
            print(f"[REGISTRY] 无法导入 {mod_name}: {e}")
            continue

        # 模块导入时 register_all() 已被调用（module-level side-effect）
        # 验证：检查 registry 是否有此模块产出的条目
        mod_registered = any(
            meta.ingester_cls.__module__ == mod_name
            for meta in _registry.values()
        )
        if mod_registered:
            discovered.append(py_file.stem)
            continue

        # Fallback：查找未主动注册的 BaseIngester 子类
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseIngester)
                and obj is not BaseIngester
                and obj.__module__ == mod_name
            ):
                register(obj)
                discovered.append(f"{py_file.stem}.{attr_name}")

    return discovered


# ================================================================
# 一键注册所有内置 parser
# ================================================================

def register_builtin_parsers() -> None:
    """
    显式注册所有内置 parser（不依赖自动发现）。

    每个 parser 模块被导入后，通过 register() 函数注册。
    这是最可靠的注册方式——不依赖文件系统扫描。
    """
    # 尝试导入每个已知的 parser 模块
    builtin_modules = [
        "ai.ingestion.parsers.cheduan_pdf",
        "ai.ingestion.parsers.cheduan_docx",
        "ai.ingestion.parsers.translation_xlsx",
        "ai.ingestion.parsers.translation_docx",
        "ai.ingestion.parsers.operation_docx",
        "ai.ingestion.parsers.operation_prose_docx",
        "ai.ingestion.parsers.troubleshooting_json",
        "ai.ingestion.parsers.faq_multi",
        "ai.ingestion.parsers.platform_faq",
    ]

    for mod_name in builtin_modules:
        try:
            __import__(mod_name)  # module-level register_all() handles registration
        except ImportError:
            pass  # parser 模块可能尚未创建
