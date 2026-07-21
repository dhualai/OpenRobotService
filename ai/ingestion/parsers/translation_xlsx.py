"""
USP 国际化翻译表 .xlsx → translation 集合

来源：translation_doc/多语言国际化管理.xlsx
格式：namespace | identifier | description | cn | en
产出：按 namespace 分组，每组一个 chunk → translation 集合（独立 collection）
"""
import hashlib
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any

from ai.config import get_docs_dir
from ai.ingestion.base import BaseIngester, Chunk
from ai.ingestion.registry import register


class TranslationXLSXIngester(BaseIngester[Dict[str, str]]):
    """翻译表 XLSX → translation 集合（独立 collection）"""

    source_paths = [get_docs_dir() / "translation_doc" / "多语言国际化管理.xlsx"]
    collection_prefix = "translation"
    collection_type = "translation"
    rebuild = False  # 追加到现有集合（保留 docx UI 翻译数据）

    @staticmethod
    def _pointer_reader() -> str:
        from ai.config import get_active_translation_collection
        return get_active_translation_collection()

    @staticmethod
    def _pointer_writer(name: str) -> None:
        from ai.config import _write_active_translation_collection
        _write_active_translation_collection(name)

    pointer_reader = staticmethod(_pointer_reader)
    pointer_writer = staticmethod(_pointer_writer)

    def parse(self) -> List[Dict[str, str]]:
        """按 namespace 分组后返回 chunk-ready dict（含 id/text/payload）"""
        rows = _load_xlsx(self.source_paths[0])
        return _build_chunks(rows)

    def to_chunk(self, entry: Dict[str, str]) -> Chunk:
        return Chunk(
            id=entry["id"],
            text=entry["text"],
            payload=entry["payload"],
        )


# ── XLSX 解析（zipfile + xml，无依赖） ─────────────────────────

def _read_shared_strings(z: zipfile.ZipFile) -> List[str]:
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    strings = []
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


def _load_xlsx(xlsx_path: Path) -> List[Dict[str, str]]:
    rows = []
    with zipfile.ZipFile(str(xlsx_path), 'r') as z:
        shared_strings = _read_shared_strings(z)
        sheet_data = _read_sheet_data(z, 'xl/worksheets/sheet1.xml', shared_strings)

        if not sheet_data:
            return rows
        header = [h.lower().strip() for h in sheet_data[0]]
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


def _build_chunks(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """按 namespace 分组，每组一个 chunk"""
    groups: Dict[str, List[Dict]] = {}
    for r in rows:
        ns = r.get("namespace", "default") or "default"
        groups.setdefault(ns, []).append(r)

    chunks = []
    for ns, items in groups.items():
        lines = [f"【翻译表】namespace: {ns}", f"共 {len(items)} 条"]
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
            "text": text,
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


def register_all():
    register(TranslationXLSXIngester, description="翻译表 XLSX → translation 集合")


register_all()
