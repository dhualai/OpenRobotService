"""
车端错误码导入 → Qdrant

读取 cheduan_doc/ 下的 3.0车载错误文档.pdf，解析错误码表，
每个错误码作为一个 chunk 写入 Qdrant。

使用方法：
    python -m ai.ingestion.ingest_cheduan --rebuild
    python -m ai.ingestion.ingest_cheduan --dry-run
    python -m ai.ingestion.ingest_cheduan --cleanup
"""
import sys
import hashlib
import asyncio
from pathlib import Path
from typing import List, Dict, Any
import re
from dataclasses import dataclass

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

from ai.config import get_docs_dir

DOCS = get_docs_dir()
PDF_PATH = DOCS / "cheduan_doc" / "3.0车载错误文档.pdf"
COLLECTION_PREFIX = "cheduan"


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ErrorCodeEntry:
    category: str          # 错误类别（通讯/任务/定位/...）
    level: str             # Warn / Error
    code: str              # 错误码（1301, 2301, ...）
    description_cn: str    # 中文描述
    description_en: str    # 英文描述
    solution_cn: str       # 中文解决方案
    solution_en: str       # 英文解决方案


# ============================================================
# PDF 文本提取
# ============================================================

def extract_pdf_tables(pdf_path: Path) -> List[Dict]:
    """
    提取 PDF 中所有表格和嵌入文本中的错误码行。
    混合策略：先用 extract_tables() 取结构化表格，
    再用 extract_text() 做正则兜底补充解析。
    每个数据行: {category, level, code, desc_cn, desc_en}
    """
    import pdfplumber
    all_rows = []
    seen_codes = set()
    current_category = ""

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()

            for table in tables:
                if not table or len(table) < 2:
                    continue

                # ── Step 1: 扫描全表找类别标题 ──
                for prow in table:
                    if not prow or all(c is None for c in prow):
                        continue
                    # 类别行特征：有且仅有一个非 None 单元格，包含 "错误" 但不含 "错误等级"/"错误码"
                    non_none = [c for c in prow if c is not None and str(c).strip()]
                    if len(non_none) == 1:
                        txt = str(non_none[0]).strip()
                        if '错误' in txt and '错误等级' not in txt and '错误码' not in txt:
                            # 取第一行（去除换行后的第一段）
                            current_category = txt.split('\n')[0].strip().rstrip('：:')
                            break

                # ── Step 2: 找表头行索引 ──
                header_row_idx = None
                header_cols = {}
                for ri, prow in enumerate(table):
                    cells = [str(c or '') for c in prow]
                    if any('错误码' in c for c in cells) and any(('解决' in c or 'Q&A' in c or 'QA' in c) for c in cells):
                        header_row_idx = ri
                        for ci, h in enumerate(cells):
                            if '错误等级' in h:
                                header_cols['level'] = ci
                            elif h.strip() == '错误码':
                                header_cols['code'] = ci
                            elif '解决' in h:
                                header_cols['desc'] = ci
                            elif 'Q&A' in h or 'QA' in h:
                                header_cols['qa'] = ci
                        break

                if header_row_idx is None or 'code' not in header_cols or 'desc' not in header_cols:
                    continue

                # ── Step 3: 确定表的默认 level（处理合并单元格导致的 level 缺失）──
                # 扫描全表所有单元格中的嵌入文本，找 level 信息
                table_all_text = ' '.join(
                    str(c or '') for row in table for c in row
                )
                # 逐 code 提取 level 映射（宽松匹配：level 和 code 可以在嵌入文本的不同位置）
                level_map: Dict[str, str] = {}
                for m in re.finditer(r'(Warn|Error)\s+(\d{4,5})', table_all_text):
                    level_map[m.group(2)] = m.group(1)
                # 如果表中某类 code 都没有显式 level，从文本中推断默认
                table_default_level = ""
                if 'Warn' in table_all_text:
                    table_default_level = 'Warn'
                if 'Error' in table_all_text and not table_default_level:
                    table_default_level = 'Error'

                # ── Step 4: 解析数据行 ──
                inherited_level = table_default_level  # 用表默认值起步
                for row in table[header_row_idx + 1:]:
                    if not row or all(c is None or str(c).strip() == '' for c in row):
                        continue

                    def _cell(col_key):
                        ci = header_cols.get(col_key)
                        if ci is None or ci >= len(row):
                            return ''
                        return str(row[ci] or '').strip()

                    level_raw = _cell('level')
                    code = _cell('code')
                    desc = _cell('desc')
                    qa = _cell('qa')

                    # 更新继承的 level
                    if level_raw in ('Warn', 'Error'):
                        inherited_level = level_raw

                    # 有些行在 desc/qa 里嵌入了错误码文本（合并单元格后遗症）
                    # 尝试从第一列中提取被挤在一起的错误码
                    if (not code or not code.isdigit()) and row[0] and '\n' in str(row[0]):
                        # 合并单元格导致多行挤在第一列，用正则拆
                        extra = _parse_embedded_codes(str(row[0]), current_category, inherited_level)
                        for er in extra:
                            if er['code'] not in seen_codes:
                                seen_codes.add(er['code'])
                                all_rows.append(er)
                        continue

                    if not code or not code.isdigit():
                        continue

                    cur_level = level_raw if level_raw in ('Warn', 'Error') else inherited_level
                    # 如果仍然没有 level，尝试从嵌入文本的 level_map 获取
                    if not cur_level or cur_level not in ('Warn', 'Error'):
                        cur_level = level_map.get(code, cur_level)

                    if code not in seen_codes:
                        seen_codes.add(code)
                        all_rows.append({
                            'category': current_category,
                            'level': cur_level,
                            'code': code,
                            'desc_cn': desc,
                            'desc_en': qa,
                        })

            # ── Step 4: 从文本中兜底提取（处理非表格形式的错误码文本）──
            page_text = page.extract_text()
            if page_text:
                extra = _parse_text_error_codes(page_text, current_category)
                for er in extra:
                    if er['code'] not in seen_codes:
                        seen_codes.add(er['code'])
                        all_rows.append(er)

    return all_rows


def _parse_embedded_codes(text: str, category: str, level: str) -> List[Dict]:
    """解析合并单元格中挤在一起的多个错误码行"""
    import re
    results = []
    # 匹配: Warn/Error? 数字编码 中文描述 英文描述
    pattern = re.compile(
        r'(?:Warn|Error)?\s*(\d{4})\s+(.+?)\s+([A-Z][A-Za-z\s.,;!?\-\'"]+(?:\s+[A-Z][A-Za-z\s.,;!?\-\'"]+)*?)(?=\s*(?:Warn|Error)?\s*\d{4}\s+|$)',
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        code = m.group(1)
        desc_cn = m.group(2).strip()
        desc_en = m.group(3).strip() if m.group(3) else ''
        results.append({
            'category': category,
            'level': level,
            'code': code,
            'desc_cn': desc_cn,
            'desc_en': desc_en,
        })
    return results


def _parse_text_error_codes(text: str, current_category: str) -> List[Dict]:
    """从页面纯文本中正则提取错误码行"""
    import re
    results = []
    # 匹配: Warn/Error + 4位数字码 + 中文 + 英文
    pattern = re.compile(
        r'(Warn|Error)\s+(\d{4})\s+(.+?)\s*\n\s*([A-Z][A-Za-z\s.,;!?\-]+(?:\s+[A-Z][A-Za-z\s.,;!?\-]+)*)',
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        level = m.group(1)
        code = m.group(2)
        desc_cn = m.group(3).strip()
        desc_en = m.group(4).strip() if m.group(4) else ''
        results.append({
            'category': current_category,
            'level': level,
            'code': code,
            'desc_cn': desc_cn,
            'desc_en': desc_en,
        })
    return results


# ============================================================
# 表格行 → ErrorCodeEntry
# ============================================================

def table_rows_to_entries(rows: List[Dict]) -> List[ErrorCodeEntry]:
    """将 extract_pdf_tables 返回的 dict 列表转为 ErrorCodeEntry 列表"""
    entries = []
    for r in rows:
        code = r.get("code", "").strip()
        # 过滤无效：非数字、少于4位（页码残留如"9"/"12"）、描述为空
        if not code or not code.isdigit() or len(code) < 4:
            continue
        desc_cn = r.get("desc_cn", "").strip()
        desc_en = r.get("desc_en", "").strip()
        if not desc_cn and not desc_en:
            continue
        entries.append(ErrorCodeEntry(
            category=r.get("category", ""),
            level=r.get("level", ""),
            code=code,
            description_cn=desc_cn,
            description_en=desc_en,
            solution_cn="",
            solution_en="",
        ))
    return entries


# ============================================================
# Chunk 构建
# ============================================================

def build_chunks(entries: List[ErrorCodeEntry]) -> List[Dict[str, Any]]:
    """将错误码条目转为可入库的 chunk"""
    chunks = []
    for e in entries:
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
        chunks.append({
            "id": hashlib.md5(f"cheduan_{e.code}".encode()).hexdigest(),
            "text": text,
            "payload": {
                "error_code": e.code,
                "category": e.category,
                "level": e.level,
                "description_cn": e.description_cn,
                "description_en": e.description_en,
                "solution_cn": e.solution_cn,
                "solution_en": e.solution_en,
                "source": "3.0车载错误文档.pdf",
            },
        })
    return chunks


# ============================================================
# 入库
# ============================================================

async def ingest_cheduan(chunks: List[Dict], collection_name: str, rebuild: bool = False) -> Dict:
    from ai.core.embed import get_embed_client
    from ai.config import get_ai_config
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct, Distance, VectorParams

    config = get_ai_config()
    embed_client = await get_embed_client()

    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _project_root / local
        print(f"\n[INGEST] {len(chunks)} cheduan chunks -> Qdrant (local: {local})")
    else:
        print(f"\n[INGEST] {len(chunks)} cheduan chunks -> Qdrant ({config.qdrant_host}:{config.qdrant_port})")
    print(f"   Collection: {collection_name}")

    texts = [c["text"] for c in chunks]
    vectors = await embed_client.embed_batch(texts)
    dim = vectors[0].shape[-1]

    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _project_root / local
        client = QdrantClient(path=str(local))
    else:
        client = QdrantClient(
            host=config.qdrant_host, port=config.qdrant_port,
            timeout=config.qdrant_timeout, check_compatibility=False,
        )

    if rebuild:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass

    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    points = [
        PointStruct(id=c["id"], vector=v.tolist(), payload=c["payload"])
        for c, v in zip(chunks, vectors)
    ]
    for i in range(0, len(points), 50):
        client.upsert(collection_name=collection_name, points=points[i:i+50])

    return {"status": "ok", "entries": len(chunks), "dimension": dim, "collection": collection_name}


async def _cleanup_old(collections_prefix: str, keep: int = 1):
    from ai.config import get_ai_config
    from qdrant_client import QdrantClient

    config = get_ai_config()
    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _project_root / local
        client = QdrantClient(path=str(local))
    else:
        client = QdrantClient(
            host=config.qdrant_host, port=config.qdrant_port,
            timeout=config.qdrant_timeout, check_compatibility=False,
        )

    try:
        all_cols = [c.name for c in client.get_collections().collections]
    except Exception as e:
        print(f"[ERR] 获取集合列表失败: {e}")
        return

    ours = sorted([c for c in all_cols if c.startswith(collections_prefix)], reverse=True)
    for c in ours[keep:]:
        try:
            client.delete_collection(c)
            print(f"  [DEL] {c}")
        except Exception:
            pass


async def auto_ingest() -> bool:
    from datetime import datetime
    from ai.config import _write_active_cheduan_collection

    rows = extract_pdf_tables(PDF_PATH)
    print(f"[CHE DUAN] PDF 表格解析到 {len(rows)} 行原始数据")

    entries = table_rows_to_entries(rows)
    print(f"[CHE DUAN] 去重后 {len(entries)} 条错误码")

    if not entries:
        print("[ERR] 未解析到任何错误码")
        return False

    chunks = build_chunks(entries)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"{COLLECTION_PREFIX}_{ts}"
    result = await ingest_cheduan(chunks, collection_name=collection_name, rebuild=True)
    if result["status"] != "ok":
        return False

    _write_active_cheduan_collection(collection_name)
    await _cleanup_old(COLLECTION_PREFIX, keep=2)
    return True


async def main():
    import argparse
    from datetime import datetime
    from ai.config import get_active_cheduan_collection, _write_active_cheduan_collection

    parser = argparse.ArgumentParser(description="车端错误码 -> Qdrant")
    parser.add_argument("--rebuild", "-r", action="store_true", help="入库")
    parser.add_argument("--dry-run", "-n", action="store_true", help="预览")
    parser.add_argument("--cleanup", action="store_true", help="清理旧集合")
    args = parser.parse_args()

    if args.cleanup:
        await _cleanup_old(COLLECTION_PREFIX)
        return

    rows = extract_pdf_tables(PDF_PATH)
    print(f"[CHE DUAN] PDF 表格解析到 {len(rows)} 行原始数据")

    entries = table_rows_to_entries(rows)
    print(f"[CHE DUAN] 去重后 {len(entries)} 条错误码")

    if args.dry_run:
        print("\n预览（前 10 条）:\n")
        for e in entries[:10]:
            print(f"  [{e.level}] {e.code} | {e.category} | {e.description_cn[:80]}")
        return

    chunks = build_chunks(entries)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"{COLLECTION_PREFIX}_{ts}"
    result = await ingest_cheduan(chunks, collection_name=collection_name, rebuild=args.rebuild)

    if result["status"] != "ok":
        print(f"\n[ERR] {result}")
        return

    old = get_active_cheduan_collection()
    _write_active_cheduan_collection(collection_name)
    print(f"\n[SWITCH] {old or '(new)'} -> {collection_name}")
    await _cleanup_old(COLLECTION_PREFIX, keep=2)


if __name__ == "__main__":
    asyncio.run(main())
