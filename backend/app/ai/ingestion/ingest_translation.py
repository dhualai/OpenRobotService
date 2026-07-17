"""
USP 国际化翻译表 → Qdrant

读取 translation_doc/多语言国际化管理.xlsx，按 namespace 分组，
每组作为一个 chunk 写入 Qdrant。支持错误码、UI标签、操作名等查询。

使用方法：
    python -m app.ai.ingestion.ingest_translation --rebuild
    python -m app.ai.ingestion.ingest_translation --dry-run
    python -m app.ai.ingestion.ingest_translation --cleanup
"""
import sys
import hashlib
import asyncio
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

_backend_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from dotenv import load_dotenv
load_dotenv(_backend_dir / ".env")

from app.ai.config import get_docs_dir

DOCS = get_docs_dir()
XLSX_PATH = DOCS / "translation_doc" / "多语言国际化管理.xlsx"
COLLECTION_PREFIX = "translation"


# ============================================================
# XLSX 解析（zipfile + xml，无依赖）
# ============================================================

def _read_shared_strings(z: zipfile.ZipFile) -> List[str]:
    """读取 xl/sharedStrings.xml，返回字符串列表"""
    strings = []
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    try:
        with z.open('xl/sharedStrings.xml') as f:
            tree = ET.parse(f)
            for si in tree.findall(f'.//{{{ns}}}si'):
                parts = []
                for t in si.iter(f'{{{ns}}}t'):
                    if t.text:
                        parts.append(t.text)
                strings.append(''.join(parts))
    except KeyError:
        pass
    return strings


def _read_sheet_data(z: zipfile.ZipFile, sheet_path: str, shared_strings: List[str]) -> List[List[str]]:
    """读取 sheet XML，返回二维数组 [[col1, col2, ...], ...]"""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rows_data = []
    with z.open(sheet_path) as f:
        tree = ET.parse(f)
        for row in tree.findall(f'.//{{{ns}}}row'):
            cells = []
            for c in row.findall(f'{{{ns}}}c'):
                v = c.find(f'{{{ns}}}v')
                if v is not None and v.text:
                    try:
                        idx = int(v.text)
                        cells.append(shared_strings[idx] if idx < len(shared_strings) else v.text)
                    except ValueError:
                        cells.append(v.text)
                else:
                    cells.append("")
            if any(c.strip() for c in cells):
                rows_data.append(cells)
    return rows_data


def load_xlsx() -> List[Dict[str, str]]:
    """加载 XLSX，返回 [{namespace, identifier, description, cn, en}, ...]"""
    rows = []
    with zipfile.ZipFile(str(XLSX_PATH), 'r') as z:
        shared_strings = _read_shared_strings(z)
        sheet_data = _read_sheet_data(z, 'xl/worksheets/sheet1.xml', shared_strings)

        # 第一行是表头：namespace | identifier | description | cn | en
        if not sheet_data:
            return rows
        header = [h.lower().strip() for h in sheet_data[0]]
        # 找列索引
        col_map = {}
        for i, h in enumerate(header):
            if h in ('namespace', 'identifier', 'description', 'cn', 'en'):
                col_map[h] = i

        for row in sheet_data[1:]:
            if len(row) <= max(col_map.values(), default=0):
                continue
            entry = {}
            for key, idx in col_map.items():
                entry[key] = row[idx].strip() if idx < len(row) else ""
            if entry.get("identifier") or entry.get("cn"):
                rows.append(entry)
    return rows


# ============================================================
# Chunk 构建：按 namespace 分组
# ============================================================

def build_chunks(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """按 namespace 分组，每组一个 chunk"""
    groups: Dict[str, List[Dict]] = {}
    for r in rows:
        ns = r.get("namespace", "default") or "default"
        groups.setdefault(ns, []).append(r)

    chunks = []
    for ns, items in groups.items():
        # 构建可读文本
        lines = [f"【翻译表】namespace: {ns}", f"共 {len(items)} 条"]
        # 取前 200 条拼接（避免 chunk 过大）
        for item in items[:200]:
            identifier = item.get("identifier", "")
            cn = item.get("cn", "")
            en = item.get("en", "")
            desc = item.get("description", "")
            line = f"{cn} | {en}"
            if desc:
                line += f"  ({desc})"
            if identifier:
                line = f"[{identifier}] {line}"
            lines.append(line)

        text = "\n".join(lines)
        chunks.append({
            "id": hashlib.md5(f"trans_{ns}".encode()).hexdigest(),
            "text": f"namespace:{ns}\n" + "\n".join(
                f"{it.get('cn','')} | {it.get('en','')}"
                for it in items[:200]
            ),
            "payload": {
                "namespace": ns,
                "entry_count": len(items),
                "sample_entries": [
                    {"cn": it["cn"], "en": it["en"], "identifier": it["identifier"]}
                    for it in items[:10]
                ],
                "source": "多语言国际化管理.xlsx",
            },
        })
    return chunks


# ============================================================
# 入库
# ============================================================

async def ingest_translation(chunks: List[Dict], collection_name: str, rebuild: bool = False) -> Dict:
    from app.ai.core.embed import get_embed_client
    from app.ai.config import get_ai_config
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct, Distance, VectorParams

    config = get_ai_config()
    embed_client = await get_embed_client()

    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _backend_dir / local
        print(f"\n[INGEST] {len(chunks)} translation chunks -> Qdrant (local: {local})")
    else:
        print(f"\n[INGEST] {len(chunks)} translation chunks -> Qdrant ({config.qdrant_host}:{config.qdrant_port})")
    print(f"   Collection: {collection_name}")

    texts = [c["text"] for c in chunks]
    vectors = await embed_client.embed_batch(texts)
    dim = vectors[0].shape[-1]

    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _backend_dir / local
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
    from app.ai.config import get_ai_config
    from qdrant_client import QdrantClient

    config = get_ai_config()
    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _backend_dir / local
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
    from app.ai.config import _write_active_translation_collection

    rows = load_xlsx()
    print(f"[TRANSLATION] 加载 {len(rows)} 条翻译记录")

    if not rows:
        print("[ERR] 未解析到翻译数据")
        return False

    chunks = build_chunks(rows)
    print(f"[TRANSLATION] 分组为 {len(chunks)} 个 namespace chunk")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"{COLLECTION_PREFIX}_{ts}"
    result = await ingest_translation(chunks, collection_name=collection_name, rebuild=True)
    if result["status"] != "ok":
        return False

    _write_active_translation_collection(collection_name)
    await _cleanup_old(COLLECTION_PREFIX, keep=2)
    return True


async def main():
    import argparse
    from datetime import datetime
    from app.ai.config import get_active_translation_collection, _write_active_translation_collection

    parser = argparse.ArgumentParser(description="USP 翻译表 -> Qdrant")
    parser.add_argument("--rebuild", "-r", action="store_true", help="入库")
    parser.add_argument("--dry-run", "-n", action="store_true", help="预览")
    parser.add_argument("--cleanup", action="store_true", help="清理旧集合")
    args = parser.parse_args()

    if args.cleanup:
        await _cleanup_old(COLLECTION_PREFIX)
        return

    rows = load_xlsx()
    print(f"[TRANSLATION] 加载 {len(rows)} 条翻译记录")

    if args.dry_run:
        ns_counts: Dict[str, int] = {}
        for r in rows:
            ns = r.get("namespace", "default") or "default"
            ns_counts[ns] = ns_counts.get(ns, 0) + 1
        print("\nnamespace 分布:")
        for ns, cnt in sorted(ns_counts.items(), key=lambda x: -x[1]):
            print(f"  {ns}: {cnt} 条")
        return

    chunks = build_chunks(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"{COLLECTION_PREFIX}_{ts}"
    result = await ingest_translation(chunks, collection_name=collection_name, rebuild=args.rebuild)

    if result["status"] != "ok":
        print(f"\n[ERR] {result}")
        return

    old = get_active_translation_collection()
    _write_active_translation_collection(collection_name)
    print(f"\n[SWITCH] {old or '(new)'} -> {collection_name}")
    await _cleanup_old(COLLECTION_PREFIX, keep=2)


if __name__ == "__main__":
    asyncio.run(main())
