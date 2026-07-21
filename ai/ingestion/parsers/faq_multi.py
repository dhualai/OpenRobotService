"""
FAQ 三源合并 → faq_docs 集合

来源：
  1. faq_doc/faq_index_with_clarification.jsonl — 结构化 FAQ 175 条
  2. faq_doc/USP FAQ.xlsx — 真实客户问题 64 条
  3. faq_doc/USP FAQ手册.docx — Docx Q&A 带图片

流程：加载三源 → 去重合并 → 向量化写入 faq_docs_* collection
"""
import re
import json
import hashlib
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import dataclass, field

from ai.config import get_docs_dir, get_ai_config
from ai.ingestion.base import BaseIngester, Chunk
from ai.ingestion.registry import register


# ── 数据模型 ────────────────────────────────────────────────────

@dataclass
class FaqEntry:
    id: str
    question: str
    answer: str
    answer_mode: str           # procedure / troubleshoot / explain / fact
    source_type: str           # manual / direct_faq / chat_faq / docx_faq
    keywords: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    source_ids: List[str] = field(default_factory=list)
    business_domain: str = ""
    images: List[str] = field(default_factory=list)
    notes: str = ""


# ── Ingester ─────────────────────────────────────────────────────

class FAQMultiIngester(BaseIngester[FaqEntry]):
    """FAQ 三源合并 → faq_docs 集合"""

    source_paths = [
        get_docs_dir() / "faq_doc" / "faq_index_with_clarification.jsonl",
        get_docs_dir() / "faq_doc" / "USP FAQ.xlsx",
        get_docs_dir() / "faq_doc" / "USP FAQ手册.docx",
    ]
    collection_prefix = "faq_docs"
    collection_type = "faq"
    rebuild = True

    @staticmethod
    def _pointer_reader() -> str:
        from ai.config import get_active_faq_collection
        return get_active_faq_collection()

    @staticmethod
    def _pointer_writer(name: str) -> None:
        from ai.config import _write_active_faq_collection
        _write_active_faq_collection(name)

    pointer_reader = staticmethod(_pointer_reader)
    pointer_writer = staticmethod(_pointer_writer)

    def get_source_label(self) -> str:
        return "FAQ三源合并"

    def validate_source_files(self) -> bool:
        # 至少需要一个源文件
        if any(p.exists() for p in self.source_paths):
            return True
        self._log("[WARN] FAQ 三源均不存在，跳过")
        return False

    def parse(self) -> List[FaqEntry]:
        # 1. 加载三源
        jsonl = _load_jsonl(self.source_paths[0]) if self.source_paths[0].exists() else []
        xlsx = _load_xlsx(self.source_paths[1]) if self.source_paths[1].exists() else []
        docx = _load_docx_faq(self.source_paths[2]) if self.source_paths[2].exists() else []

        # 2. 三源合并（去重）
        merged = _merge_entries(jsonl, xlsx)
        if docx:
            merged = _merge_entries(merged, docx)

        self._log(f"  source_type 分布: {_count_by(merged, lambda e: e.source_type)}")
        self._log(f"  answer_mode 分布: {_count_by(merged, lambda e: e.answer_mode)}")
        self._log(f"  有答案: {sum(1 for e in merged if e.answer)}/{len(merged)}")

        return merged

    def to_chunk(self, entry: FaqEntry) -> Chunk:
        parts = [entry.question]
        if entry.answer:
            parts.append(entry.answer)
        if entry.images:
            config = get_ai_config()
            faq_media_url = f"{config.media_url_prefix}/faq_doc"
            img_refs = ' '.join(f'![]({faq_media_url}/{img})' for img in entry.images)
            parts.append(img_refs)

        text = '\n'.join(parts)
        return Chunk(
            id=hashlib.md5(entry.id.encode()).hexdigest(),
            text=text,
            payload={
                "faq_id": entry.id,
                "question": entry.question,
                "answer": entry.answer,
                "source_ids": entry.source_ids,
                "images": entry.images,
                "content": text,
            },
        )


# ── JSONL 加载 ──────────────────────────────────────────────────

def _load_jsonl(jsonl_path: Path) -> List[FaqEntry]:
    entries = []
    with open(jsonl_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            j = json.loads(line)
            mode = j.get('answer_mode', 'procedure')
            mode_map = {
                'troubleshooting': 'troubleshoot', 'general': 'chat',
                'definition': 'explain', 'concept': 'explain',
                'short_fact': 'fact', 'clarification': 'clarify',
            }
            mode = mode_map.get(mode, mode)
            entries.append(FaqEntry(
                id=j.get('faq_id', ''),
                question=j.get('question', ''),
                answer=j.get('direct_answer', ''),
                answer_mode=mode,
                source_type=j.get('source_type', 'manual'),
                keywords=j.get('keywords', []),
                aliases=j.get('aliases', []),
                source_ids=j.get('source_ids', []),
                business_domain=j.get('business_domain', ''),
                notes=j.get('review_note', ''),
            ))
    print(f"  [JSONL] {len(entries)} entries")
    return entries


# ── XLSX 加载 ───────────────────────────────────────────────────

def _parse_xlsx_cells(filepath: str) -> List[Dict[str, str]]:
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with zipfile.ZipFile(filepath) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            ss = ET.parse(z.open('xl/sharedStrings.xml'))
            for si in ss.findall('.//{' + ns + '}si'):
                texts = [t.text or '' for t in si.iter('{' + ns + '}t')]
                strings.append(''.join(texts))

        sheet = ET.parse(z.open('xl/worksheets/sheet1.xml'))
        rows = list(sheet.findall('.//{' + ns + '}row'))
        rows_data = []
        for row in rows:
            cells = {}
            for c in row.findall('{' + ns + '}c'):
                ref = c.get('r', '')
                col_letter = ref.rstrip('0123456789')
                ct = c.get('t')
                v = c.find('{' + ns + '}v')
                val = ''
                if v is not None and v.text:
                    val = strings[int(v.text)] if ct == 's' else v.text
                cells[col_letter] = val.strip() if val else ''
            rows_data.append(cells)
    return rows_data


def _clean_chat_answer(text: str) -> str:
    text = re.sub(r'\*\*[\d:]+ [^*]+\*\*:\s*', '', text)
    text = re.sub(r'^> .+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _load_xlsx(xlsx_path: Path) -> List[FaqEntry]:
    rows = _parse_xlsx_cells(str(xlsx_path))
    if not rows:
        return []

    header = rows[0]
    col_map = {}
    for col_letter, val in header.items():
        if '问题' in val:
            col_map['q'] = col_letter
        elif '回答' in val:
            col_map['a'] = col_letter
        elif '备注' in val:
            col_map['note'] = col_letter
        elif '项目' in val:
            col_map['project'] = col_letter

    entries = []
    for i, row in enumerate(rows[1:], 1):
        q = row.get(col_map.get('q', 'A'), '').strip()
        if not q:
            continue
        raw_a = row.get(col_map.get('a', 'B'), '').strip()
        note = row.get(col_map.get('note', 'C'), '').strip()
        project = row.get(col_map.get('project', 'D'), '').strip()

        entries.append(FaqEntry(
            id=f"xlsx.{i:03d}",
            question=q,
            answer=_clean_chat_answer(raw_a) if raw_a else '',
            answer_mode='troubleshoot' if raw_a else 'procedure',
            source_type='chat_faq',
            business_domain=project if project else 'general',
            notes=note,
        ))

    has_a = sum(1 for e in entries if e.answer)
    print(f"  [XLSX] {len(entries)} questions ({has_a} with answers)")
    return entries


# ── Docx FAQ 加载 ───────────────────────────────────────────────

def _load_docx_faq(docx_path: Path) -> List[FaqEntry]:
    faq_dir = docx_path.parent
    media_dir = faq_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    # 提取图片
    extracted = 0
    with zipfile.ZipFile(str(docx_path)) as z:
        for name in z.namelist():
            if not name.startswith('word/media/') or name.endswith('/'):
                continue
            fname = name.split('/')[-1]
            dest = media_dir / fname
            if not dest.exists():
                dest.write_bytes(z.read(name))
                extracted += 1
    print(f"  [DOCX] {extracted} images extracted")

    # pandoc 转 markdown
    text = subprocess.run(
        ["pandoc", str(docx_path), "-f", "docx", "-t", "markdown", "--wrap=none"],
        capture_output=True, encoding="utf-8", check=True,
    ).stdout

    # 去掉目录和修订记录
    text = re.sub(r'^# \*\*目 录\*\*.*?(?=^# 1\s)', '', text, flags=re.MULTILINE | re.DOTALL)

    # 按 **Q 边界切分
    qa_blocks = re.split(r'\n(?=\*\*Q[:：])', text)

    entries = []
    for i, block in enumerate(qa_blocks):
        if not block.strip():
            continue
        if not re.search(r'\*\*Q[:：]', block):
            continue

        q_match = re.search(r'\*\*Q[:：]\s*(.+?)\*\*\s*$', block, re.MULTILINE)
        if not q_match:
            continue
        question = q_match.group(1).strip()

        a_match = re.search(r'(?:\*\*)?A[:：]\s*\*?\*?(.+?)$', block, re.MULTILINE | re.DOTALL)
        answer = ''
        if a_match:
            answer = re.sub(r'\n!\[descript\]\(media/[^)]+\)\{[^}]*\}', '', a_match.group(1)).strip()

        # 提取图片（过滤分隔线）
        images = []
        for img_m in re.finditer(
            r'!\[descript\]\(media/([^)]+)\)\{[^}]*height="([^"]+)"[^}]*\}', block
        ):
            fname, height_str = img_m.group(1), img_m.group(2)
            try:
                if float(height_str.replace('in', '').strip()) < 0.1:
                    continue
            except ValueError:
                pass
            if fname not in images:
                images.append(fname)

        entries.append(FaqEntry(
            id=f"docx.{i:03d}",
            question=question,
            answer=answer,
            answer_mode='troubleshoot',
            source_type='docx_faq',
            business_domain='',
            images=images,
        ))

    has_a = sum(1 for e in entries if e.answer)
    has_img = sum(1 for e in entries if e.images)
    print(f"  [DOCX] {len(entries)} Q&A pairs ({has_a} with answers, {has_img} with images)")
    return entries


# ── 去重合并 ────────────────────────────────────────────────────

def _simple_tokenize(text: str) -> set:
    clean = re.sub(r'[^\w一-鿿]', '', text)
    tokens = set()
    for n in [2, 3, 4]:
        for i in range(len(clean) - n + 1):
            tokens.add(clean[i:i + n])
    return tokens


def _merge_entries(base: List[FaqEntry], new: List[FaqEntry]) -> List[FaqEntry]:
    merged = list(base)
    new_count = 0
    merged_count = 0

    for ne in new:
        n_tokens = _simple_tokenize(ne.question)
        if len(n_tokens) < 3:
            merged.append(ne)
            new_count += 1
            continue

        best_overlap = 0
        best_entry = None
        for be in merged:
            b_tokens = _simple_tokenize(be.question)
            overlap = len(n_tokens & b_tokens)
            denom = min(len(n_tokens), len(b_tokens))
            ratio = overlap / denom if denom > 0 else 0
            if ratio > best_overlap and ratio > 0.35:
                best_overlap = ratio
                best_entry = be

        if best_entry and best_overlap > 0.35:
            if ne.question not in best_entry.aliases:
                best_entry.aliases.append(ne.question)
            if ne.answer and not best_entry.answer:
                best_entry.answer = ne.answer
                best_entry.answer_mode = ne.answer_mode
            if ne.images and not best_entry.images:
                best_entry.images = ne.images
            merged_count += 1
        else:
            merged.append(ne)
            new_count += 1

    print(f"  [MERGE] base={len(base)}, new={len(new)} → merged={merged_count}, new_added={new_count}, total={len(merged)}")
    return merged


def _count_by(items: list, key_fn) -> dict:
    counts = {}
    for item in items:
        k = key_fn(item)
        counts[k] = counts.get(k, 0) + 1
    return counts


def register_all():
    register(FAQMultiIngester, description="FAQ 三源合并（JSONL + XLSX + Docx）→ faq_docs 集合")


register_all()
