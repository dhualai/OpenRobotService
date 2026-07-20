"""
车端错误码 PDF → cheduan 集合

来源：cheduan_doc/3.0车载错误文档.pdf
格式：PDF 表格，48 页，4-5 位错误码

解析策略 v3（基于实际表格结构）：
  - 4 列表格：[Level, Code, 中文描述, English]
  - Level 为空时继承上一行
  - 表头可有可无，直接按"某列含 4-5 位数字"定位 code 列
  - 合并单元格：一个 cell 里塞了多个码的行，用正则拆
"""
import re
import logging
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass

logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

from ai.config import get_docs_dir
from ai.ingestion.base import BaseIngester, Chunk
from ai.ingestion.registry import register


@dataclass
class ErrorCodeEntry:
    category: str
    level: str
    code: str
    description_cn: str
    description_en: str
    solution_cn: str = ""
    solution_en: str = ""


class CheduanPDFIngester(BaseIngester[ErrorCodeEntry]):
    """车端错误码 PDF（4-5 位码）→ cheduan 集合"""

    source_paths = [get_docs_dir() / "cheduan_doc" / "3.0车载错误文档.pdf"]
    collection_prefix = "cheduan"
    collection_type = "cheduan"
    rebuild = True

    @staticmethod
    def _pointer_reader() -> str:
        from ai.config import get_active_cheduan_collection
        return get_active_cheduan_collection()

    @staticmethod
    def _pointer_writer(name: str) -> None:
        from ai.config import _write_active_cheduan_collection
        _write_active_cheduan_collection(name)

    pointer_reader = staticmethod(_pointer_reader)
    pointer_writer = staticmethod(_pointer_writer)

    def parse(self) -> List[ErrorCodeEntry]:
        return _extract_all_v3(self.source_paths[0])

    def to_chunk(self, e: ErrorCodeEntry) -> Chunk:
        parts = [
            f"【车端错误码】{e.code}",
            f"类别：{e.category}",
            f"等级：{e.level}",
        ]
        if e.description_cn:
            parts.append(f"描述：{e.description_cn}")
        if e.description_en:
            parts.append(f"Description: {e.description_en}")
        if e.solution_cn:
            parts.append(f"方案：{e.solution_cn}")
        if e.solution_en:
            parts.append(f"Solution: {e.solution_en}")

        text = "\n".join(parts)
        return Chunk(
            id=self.stable_id("cheduan", e.code),
            text=text,
            payload={
                "error_code": e.code,
                "category": e.category,
                "level": e.level,
                "description_cn": e.description_cn,
                "description_en": e.description_en,
                "solution_cn": e.solution_cn,
                "solution_en": e.solution_en,
                "source": "3.0车载错误文档.pdf",
            },
        )


# ══════════════════════════════════════════════════════════════════
# v3 核心：无表头依赖的表格解析
# ══════════════════════════════════════════════════════════════════

# 4-5 位数字（前后不能紧跟数字，排除更长数字的部分匹配）
_CODE_RE = re.compile(r'(?<!\d)(\d{4,5})(?!\d)')

# 合并单元格：在一个 cell 文本中找嵌入的错误码（未使用，保留备用）
_EMBEDDED_RE = re.compile(
    r'(?:Warn|Error)?\s*(?P<code>\d{4,5})\s+'
    r'(?P<desc>.+?)'
    r'(?=\s*(?:Warn|Error)?\s*\d{4,5}\s+|$)',
    re.DOTALL,
)


def _extract_all_v3(pdf_path: Path) -> List[ErrorCodeEntry]:
    """v3 解析：遍历所有表格行，自动识别 code 列"""
    import pdfplumber

    all_entries: List[ErrorCodeEntry] = []
    seen_codes: Set[str] = set()
    current_category = ""

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_text = (page.extract_text() or "").strip()

            # 类别检测
            cat = _detect_category_v3(page_text)
            if cat:
                current_category = cat

            tables = page.extract_tables()
            # 标准格式（≥3列）优先处理，合并格式（≤2列）后处理
            # 这样标准表的干净数据不会因 seen_codes 被合并表的脏数据挡住
            tables.sort(key=lambda t: max(len(r or []) for r in t), reverse=True)
            for table in tables:
                if not table or len(table) < 2:
                    continue

                # 先扫一遍找类别标题
                for row in table:
                    if not row:
                        continue
                    non_none = [c for c in row if c is not None and str(c).strip()]
                    if len(non_none) == 1:
                        txt = str(non_none[0]).strip()
                        if _looks_like_category(txt):
                            current_category = txt.split('\n')[0].strip().rstrip('：:')

                entries = _parse_table_v3(table, current_category)
                for e in entries:
                    if e.code not in seen_codes:
                        seen_codes.add(e.code)
                        all_entries.append(e)

    return all_entries


def _parse_table_v3(table: List[List], category: str) -> List[ErrorCodeEntry]:
    """
    解析一个表格。

    先判断表类型：
      - 合并格式（≤2 列，大段文本塞在一个 cell）→ _extract_merged_cells
      - 标准格式（≥3 列，每列独立）→ 列解析
    """
    if not table:
        return []

    num_cols = max(len(row or []) for row in table)

    # ── 判断表类型 ──
    # 合并格式特征：≤2 列，或者 code 列中每个 cell 包含多个码
    code_col = _find_code_column(table)
    if code_col is None:
        return _extract_merged_cells(table, category)

    # 检查 code 列中多码 cell 的比例
    multi_code_ratio = _multi_code_ratio(table, code_col)
    if num_cols <= 2 or multi_code_ratio > 0.3:
        return _extract_merged_cells(table, category)

    # ── 标准格式：推断其他列 ──
    level_col = _find_level_column(table, code_col)
    desc_col, qa_col = _find_desc_columns(table, code_col, num_cols)

    # ── 3. 逐行解析 ──
    entries: List[ErrorCodeEntry] = []
    inherited_level = ""

    for row in table:
        if not row or all(c is None or str(c).strip() == '' for c in row):
            continue

        cells = [str(c or '').strip() for c in row]

        # 取 code
        if code_col >= len(cells):
            continue
        code_raw = cells[code_col]

        # 跳过表头行（含"错误码"文字）
        if '错误码' in code_raw:
            continue

        # 如果 code 列不是纯数字，检查是否是合并单元格
        codes_in_cell = _CODE_RE.findall(code_raw)
        if not codes_in_cell:
            # 可能 code 和 desc 混在一起了，尝试从整行提取
            row_text = ' '.join(cells)
            codes_in_cell = _CODE_RE.findall(row_text)
            if not codes_in_cell:
                continue
            # 整行提取到的码
            for code in codes_in_cell:
                e = _make_entry_from_row_text(row_text, code, category, inherited_level)
                if e:
                    entries.append(e)
            continue

        # 该 cell 只有一个码：正常解析
        if len(codes_in_cell) == 1:
            code = codes_in_cell[0]
        else:
            # 多个码挤在一个 cell → 用嵌入正则拆
            for code in codes_in_cell:
                e = _make_entry_from_row_text(
                    ' '.join(cells), code, category, inherited_level
                )
                if e:
                    entries.append(e)
            continue

        # 取 level
        level_raw = cells[level_col] if level_col is not None and level_col < len(cells) else ""
        if level_raw.lower() in ('warn', 'error'):
            inherited_level = level_raw
        cur_level = level_raw if level_raw.lower() in ('warn', 'error') else inherited_level

        # 取描述
        if isinstance(desc_col, int) and desc_col < len(cells):
            desc_cn_raw = cells[desc_col]
        else:
            desc_cn_raw = ""
        if isinstance(qa_col, int) and qa_col < len(cells):
            desc_en_raw = cells[qa_col]
        else:
            desc_en_raw = ""

        # 清理换行符
        desc_cn = desc_cn_raw.replace('\n', '').strip()
        desc_en = desc_en_raw.replace('\n', ' ').strip()

        # 排除非描述内容
        if desc_cn in ('', 'Warn', 'Error') or _CODE_RE.fullmatch(desc_cn):
            # 描述列可能是空的（code 被放在了描述里）
            # 从 code cell 中提取描述
            _, cn, en = _split_code_and_desc(code_raw, code)
            if cn:
                desc_cn = cn
            if en:
                desc_en = en

        if not desc_cn and not desc_en:
            continue

        # 标准化 level
        cur_level = _normalize_level(cur_level)

        entries.append(ErrorCodeEntry(
            category=category,
            level=cur_level,
            code=code,
            description_cn=desc_cn,
            description_en=desc_en,
        ))

    return entries


def _find_code_column(table: List[List]) -> Optional[int]:
    """找到 code 列：扫描各列，找 4-5 位数字命中率最高的列"""
    num_rows = len(table)
    col_hits = {}
    col_counts = {}

    for row in table:
        if not row:
            continue
        for ci, cell in enumerate(row):
            cell_str = str(cell or '').strip()
            col_counts[ci] = col_counts.get(ci, 0) + 1
            if _CODE_RE.search(cell_str):
                col_hits[ci] = col_hits.get(ci, 0) + 1

    if not col_hits:
        return None

    # 选命中率最高的列（至少命中 2 行或命中率 > 20%）
    best_col = max(col_hits, key=lambda c: col_hits[c])
    hit_rate = col_hits[best_col] / max(col_counts.get(best_col, 1), 1)
    if col_hits[best_col] >= 2 or hit_rate > 0.2:
        return best_col
    return None


def _multi_code_ratio(table: List[List], code_col: int) -> float:
    """返回 code 列中包含多个码的 cell 的比例。>0.3 说明是合并格式。"""
    total = 0
    multi = 0
    for row in table:
        if not row or code_col >= len(row):
            continue
        cell = str(row[code_col] or '').strip()
        if not cell or '错误码' in cell:
            continue
        total += 1
        codes = _CODE_RE.findall(cell)
        if len(codes) > 1:
            multi += 1
    if total == 0:
        return 0.0
    return multi / total


def _find_level_column(table: List[List], code_col: int) -> Optional[int]:
    """找 level 列：通常在 code 列左边"""
    for ci in range(code_col - 1, -1, -1):
        for row in table:
            if not row or ci >= len(row):
                continue
            cell = str(row[ci] or '').strip().lower()
            if cell in ('warn', 'error'):
                return ci
    # Level 可能和 code 同列（如 "Error 6601" 在同一 cell）
    return None


def _find_desc_columns(table: List[List], code_col: int, num_cols: int) -> Tuple[Optional[int], Optional[int]]:
    """
    找描述列：在 code 列右边。
    返回 (desc_cn_col, qa_col)。
    """
    # 找 code 右边含中文最长的列作为 desc_cn
    # 找最右边含英文的列作为 qa
    desc_col = None
    qa_col = None

    for ci in range(code_col + 1, num_cols):
        has_cn = False
        has_en = False
        for row in table:
            if not row or ci >= len(row):
                continue
            cell = str(row[ci] or '')
            if any('一' <= c <= '鿿' for c in cell):
                has_cn = True
            if any('A' <= c <= 'Z' for c in cell if len(cell) > 3):
                has_en = True
        if has_cn and desc_col is None:
            desc_col = ci
        # 注意：不是 elif！同一列可能中英文都有
        if has_en and qa_col is None and ci != desc_col:
            qa_col = ci

    return desc_col, qa_col


def _extract_merged_cells(table: List[List], category: str) -> List[ErrorCodeEntry]:
    """兜底：整张表当文本扫，以 code 为锚点分割描述"""
    entries: List[ErrorCodeEntry] = []
    for row in table:
        if not row:
            continue
        row_text = ' '.join(str(c or '') for c in row)

        # 找到所有 code 的位置
        code_positions = [(m.group(0), m.start(), m.end()) for m in _CODE_RE.finditer(row_text)]
        if not code_positions:
            continue

        for i, (code, start, end) in enumerate(code_positions):
            # 这个 code 的描述：从 code 后面到下一个 code 之间
            if i + 1 < len(code_positions):
                desc = row_text[end:code_positions[i + 1][1]].strip()
            else:
                desc = row_text[end:].strip()

            # 去掉描述中残留的 code 数字和坐标类数字
            desc = re.sub(r'\b\d{1,3}\b', '', desc)  # 去掉 1-3 位杂数
            desc = desc.strip()

            desc_cn, desc_en = _split_cn_en(desc)
            if desc_cn or desc_en:
                entries.append(ErrorCodeEntry(
                    category=category,
                    level='',
                    code=code,
                    description_cn=desc_cn,
                    description_en=desc_en,
                ))
    return entries


def _make_entry_from_row_text(
    row_text: str, code: str, category: str, inherited_level: str
) -> Optional[ErrorCodeEntry]:
    """从整行文本中提取一个错误码的描述"""
    # 在文本中定位 code
    idx = row_text.find(code)
    if idx < 0:
        return None
    # 取 code 之后的文本
    after = row_text[idx + len(code):].strip()
    # 去掉 level 前缀
    after = re.sub(r'^(Warn|Error)\s+', '', after, flags=re.IGNORECASE)
    desc_cn, desc_en = _split_cn_en(after)
    if not desc_cn and not desc_en:
        return None
    return ErrorCodeEntry(
        category=category,
        level=inherited_level,
        code=code,
        description_cn=desc_cn,
        description_en=desc_en,
    )


def _split_code_and_desc(cell_text: str, code: str) -> Tuple[str, str, str]:
    """从含code的cell文本中分离 code/中文/英文"""
    idx = cell_text.find(code)
    if idx < 0:
        return (code, "", "")
    after = cell_text[idx + len(code):].strip()
    after = re.sub(r'^(Warn|Error)\s+', '', after, flags=re.IGNORECASE)
    cn, en = _split_cn_en(after)
    return (code, cn, en)


# ══════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════

def _detect_category_v3(text: str) -> Optional[str]:
    """检测页面文本中的类别标题"""
    for line in text.split('\n')[:5]:
        line = line.strip()
        if _looks_like_category(line):
            return line.split('\n')[0].strip().rstrip('：:')
    return None


def _looks_like_category(text: str) -> bool:
    if not text or len(text) > 30:
        return False
    if '错误' not in text:
        return False
    if '错误等级' in text or '错误码' in text or '解决方案' in text:
        return False
    return True


def _split_cn_en(text: str) -> Tuple[str, str]:
    """分离中英文：找中→英边界"""
    text = text.strip()
    if not text:
        return ("", "")

    boundary = None
    for i, ch in enumerate(text):
        if i > 0 and '一' <= text[i - 1] <= '鿿':
            if 'A' <= ch <= 'Z' or 'a' <= ch <= 'z':
                boundary = i
                break

    if boundary is None:
        has_cn = any('一' <= c <= '鿿' for c in text)
        if has_cn:
            return (text, "")
        else:
            return ("", text)

    return (text[:boundary].strip(), text[boundary:].strip())


def _normalize_level(level: str) -> str:
    level = level.strip().lower()
    if level == 'warn':
        return 'Warn'
    if level == 'error':
        return 'Error'
    return level


# ── 注册 ─────────────────────────────────────────────────────────

def register_all():
    register(CheduanPDFIngester, description="车端错误码 PDF（4-5位）→ cheduan 集合")


register_all()
