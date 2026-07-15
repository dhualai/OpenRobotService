"""
FAQ 知识库导入 — 三源合并（JSONL + XLSX + Docx 图片）→ Qdrant

流程：
  1. 加载 JSONL（结构化 FAQ，175 条）
  2. 加载 XLSX（真实客户问题，64 条）→ 去重合并
  3. 提取 Docx 图片 → faq_doc/media/
  4. 统一向量化写入 Qdrant（FAQ 专用 collection）

使用方法：
    python -m app.ai.ingestion.ingest_faq --rebuild   # 三源合并入库
    python -m app.ai.ingestion.ingest_faq --dry-run   # 预览
    python -m app.ai.ingestion.ingest_faq --cleanup   # 清理旧集合
"""
import os
import re
import sys
import json
import hashlib
import asyncio
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

# 确保 backend 在 path 中
_backend_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from dotenv import load_dotenv
load_dotenv(_backend_dir / ".env")
load_dotenv(_backend_dir / "app" / "ai" / ".env")

# 路径常量（通过 get_docs_dir() 动态获取）
def _faq_dir() -> Path:
    from app.ai.config import get_docs_dir
    return get_docs_dir() / "faq_doc"

JSONL_PATH = _faq_dir() / "faq_index_with_clarification.jsonl"
XLSX_PATH = _faq_dir() / "USP FAQ.xlsx"
DOCX_PATH = _faq_dir() / "USP FAQ手册.docx"
MEDIA_DIR = _faq_dir() / "media"

# Qdrant FAQ 集合前缀
FAQ_COLLECTION_PREFIX = "faq_docs"


# ============================================================
# 数据模型
# ============================================================

@dataclass
class FaqEntry:
    """统一 FAQ 条目"""
    id: str
    question: str
    answer: str               # 直接答案（可为空，需从手册检索补全）
    answer_mode: str           # procedure / troubleshoot / explain / fact
    source_type: str           # manual / direct_faq / chat_faq
    keywords: List[str]
    aliases: List[str]
    source_ids: List[str]      # 引用的手册节号
    business_domain: str
    images: List[str]          # faq 专属图片路径
    notes: str = ""


# ============================================================
# 步骤 1：加载 JSONL
# ============================================================

def load_jsonl() -> List[FaqEntry]:
    entries = []
    with open(JSONL_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            j = json.loads(line)

            # 规范化 answer_mode
            mode = j.get('answer_mode', 'procedure')
            mode_map = {
                'troubleshooting': 'troubleshoot',
                'general': 'chat',
                'definition': 'explain',
                'concept': 'explain',
                'short_fact': 'fact',
                'clarification': 'clarify',
            }
            mode = mode_map.get(mode, mode)

            entry = FaqEntry(
                id=j.get('faq_id', ''),
                question=j.get('question', ''),
                answer=j.get('direct_answer', ''),
                answer_mode=mode,
                source_type=j.get('source_type', 'manual'),
                keywords=j.get('keywords', []),
                aliases=j.get('aliases', []),
                source_ids=j.get('source_ids', []),
                business_domain=j.get('business_domain', ''),
                images=[],
                notes=j.get('review_note', ''),
            )
            entries.append(entry)

    print(f"[JSONL] {len(entries)} entries")
    return entries


# ============================================================
# 步骤 2：加载 XLSX
# ============================================================

def _parse_xlsx_cells(filepath: str) -> List[Dict[str, str]]:
    """解析 xlsx 为 [{col_letter: value}] 列表（用内置 xml 解析，不依赖 openpyxl）"""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

    with zipfile.ZipFile(filepath) as z:
        # Shared strings
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            ss = ET.parse(z.open('xl/sharedStrings.xml'))
            for si in ss.findall('.//{' + ns + '}si'):
                texts = [t.text or '' for t in si.iter('{' + ns + '}t')]
                strings.append(''.join(texts))

        # Sheet data
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
    """清理聊天记录风格的答案：去时间戳、去人名标记"""
    # 去掉 **时间 人名**: 前缀
    text = re.sub(r'\*\*[\d:]+ [^*]+\*\*:\s*', '', text)
    text = re.sub(r'^> .+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def load_xlsx() -> List[FaqEntry]:
    rows = _parse_xlsx_cells(str(XLSX_PATH))
    if not rows:
        return []

    # 第一行是表头
    header = rows[0]
    # '问题' -> 'A', '回答' -> 'B', '备注' -> 'C', '属于项目' -> 'D'
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

        entry = FaqEntry(
            id=f"xlsx.{i:03d}",
            question=q,
            answer=_clean_chat_answer(raw_a) if raw_a else '',
            answer_mode='troubleshoot' if raw_a else 'procedure',
            source_type='chat_faq',
            keywords=[],
            aliases=[],
            source_ids=[],
            business_domain=project if project else 'general',
            images=[],
            notes=note,
        )
        entries.append(entry)

    has_a = sum(1 for e in entries if e.answer)
    print(f"[XLSX] {len(entries)} questions ({has_a} with answers)")
    return entries


# ============================================================
# 步骤 3：解析 FAQ Docx（按 Q&A 切分，图片自然归属）
# ============================================================

def load_docx_faq() -> List[FaqEntry]:
    """
    从 FAQ docx 提取 Q&A 对，图片自然归属到各自 Q&A 下。

    Docx 结构：
        # 1 USP部署与启动         ← 章节标题（仅分组，不用于切块）
        **Q：客户在前期不开放...**  ← 问题
        A：1. 临时方案...          ← 答案（可多段落）
        ![](media/image2.png)       ← 截图（属于这条 QA）
        ![](media/image1.png)       ← 分隔线（height≈0.035in，过滤掉）
        **Q：下一个问题...**        ← 下一条 QA
    """
    # 1. 提取 docx 内嵌图片
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with zipfile.ZipFile(str(DOCX_PATH)) as z:
        for name in z.namelist():
            if not name.startswith('word/media/') or name.endswith('/'):
                continue
            fname = name.split('/')[-1]
            dest = MEDIA_DIR / fname
            if not dest.exists():
                dest.write_bytes(z.read(name))
                extracted += 1
    print(f"[DOCX] {extracted} images extracted to {MEDIA_DIR}")

    # 2. pandoc 转 markdown
    text = subprocess.run(
        ["pandoc", str(DOCX_PATH), "-f", "docx", "-t", "markdown", "--wrap=none"],
        capture_output=True, encoding="utf-8", check=True,
    ).stdout

    # 3. 去掉目录和修订记录（# 目 录 → # 1 之前的内容）
    text = re.sub(r'^# \*\*目 录\*\*.*?(?=^# 1\s)', '', text, flags=re.MULTILINE | re.DOTALL)

    # 4. 按 **Q 边界切分每个 Q&A block
    qa_blocks = re.split(r'\n(?=\*\*Q[:：])', text)

    entries: List[FaqEntry] = []
    for i, block in enumerate(qa_blocks):
        if not block.strip():
            continue
        # 跳过纯章节标题（没有 Q&A）
        if not re.search(r'\*\*Q[:：]', block):
            continue

        # 提取问题：**Q：...** 或 **Q: ...**
        q_match = re.search(r'\*\*Q[:：]\s*(.+?)\*\*\s*$', block, re.MULTILINE)
        if not q_match:
            continue
        question = q_match.group(1).strip()

        # 提取答案：A：... 或 **A:** ...（截到图片标记之前）
        a_match = re.search(r'(?:\*\*)?A[:：]\s*\*?\*?(.+?)$', block, re.MULTILINE | re.DOTALL)
        answer = ''
        if a_match:
            # 去掉末尾的图片标记（单独提取）
            answer = re.sub(r'\n!\[descript\]\(media/[^)]+\)\{[^}]*\}', '', a_match.group(1)).strip()

        # 提取图片：过滤分隔线（height < 0.1in 视为分隔线，如 image1.png height=0.035in）
        images: List[str] = []
        for img_m in re.finditer(
            r'!\[descript\]\(media/([^)]+)\)\{[^}]*height="([^"]+)"[^}]*\}', block
        ):
            fname, height_str = img_m.group(1), img_m.group(2)
            try:
                if float(height_str.replace('in', '').strip()) < 0.1:
                    continue  # 分隔线，跳过
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
            keywords=[],
            aliases=[],
            source_ids=[],
            business_domain='',
            images=images,
        ))

    has_a = sum(1 for e in entries if e.answer)
    has_img = sum(1 for e in entries if e.images)
    print(f"[DOCX] {len(entries)} Q&A pairs ({has_a} with answers, {has_img} with images)")
    return entries


# ============================================================
# 步骤 4：去重合并
# ============================================================

def _simple_tokenize(text: str) -> set:
    """简单分词：2-4 字片段"""
    tokens = set()
    # 去掉标点
    clean = re.sub(r'[^\w一-鿿]', '', text)
    for n in [2, 3, 4]:
        for i in range(len(clean) - n + 1):
            tokens.add(clean[i:i + n])
    return tokens


def merge_entries(jsonl_entries: List[FaqEntry],
                  xlsx_entries: List[FaqEntry]) -> List[FaqEntry]:
    """合并 JSONL 和 XLSX，去重后统一输出"""
    merged = list(jsonl_entries)
    new_count = 0
    merged_count = 0

    for xe in xlsx_entries:
        x_tokens = _simple_tokenize(xe.question)
        if len(x_tokens) < 3:
            merged.append(xe)
            new_count += 1
            continue

        # 找最佳匹配
        best_overlap = 0
        best_entry = None
        for je in jsonl_entries:
            j_tokens = _simple_tokenize(je.question)
            overlap = len(x_tokens & j_tokens)
            denom = min(len(x_tokens), len(j_tokens))
            ratio = overlap / denom if denom > 0 else 0
            if ratio > best_overlap and ratio > 0.35:
                best_overlap = ratio
                best_entry = je

        if best_entry and best_overlap > 0.35:
            # 合并：补充 aliases、answer、images
            if xe.question not in best_entry.aliases:
                best_entry.aliases.append(xe.question)
            if xe.answer and not best_entry.answer:
                best_entry.answer = xe.answer
                best_entry.answer_mode = xe.answer_mode
            if xe.images and not best_entry.images:
                best_entry.images = xe.images
            merged_count += 1
        else:
            merged.append(xe)
            new_count += 1

    print(f"[MERGE] jsonl={len(jsonl_entries)}, xlsx={len(xlsx_entries)}")
    print(f"   merged={merged_count} (xlsx -> jsonl alias), new={new_count}")
    print(f"   total={len(merged)}")

    return merged


# ============================================================
# 步骤 5：入库
# ============================================================

def faq_to_chunk(entry: FaqEntry) -> str:
    """将 FAQ 条目转为可检索的文本块"""
    parts = [entry.question]
    if entry.answer:
        parts.append(entry.answer)
    if entry.images:
        img_refs = ' '.join(f'![](/api/media/faq_doc/{img})' for img in entry.images)
        parts.append(img_refs)
    return '\n'.join(parts)


async def ingest_faq(entries: List[FaqEntry], collection_name: str,
                     rebuild: bool = False) -> Dict[str, Any]:
    """将 FAQ 条目向量化写入 Qdrant"""
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
        print(f"\n[INGEST] {len(entries)} FAQ entries -> Qdrant (local: {local})")
    else:
        print(f"\n[INGEST] {len(entries)} FAQ entries -> Qdrant ({config.qdrant_host}:{config.qdrant_port})")
    print(f"   Collection: {collection_name}")

    # 1. 向量化（用 question 做向量，answer 做 payload）
    print(f"\n[EMBED] generating vectors ({len(entries)} texts)...")
    texts = [faq_to_chunk(e) for e in entries]
    vectors = await embed_client.embed_batch(texts)
    dim = vectors[0].shape[-1]
    print(f"   dim={dim}")

    # 2. 连接 Qdrant
    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _backend_dir / local
        client = QdrantClient(path=str(local))
    else:
        client = QdrantClient(
            host=config.qdrant_host,
            port=config.qdrant_port,
            timeout=config.qdrant_timeout,
        )

    # 3. 重建
    if rebuild:
        print(f"\n[DROP] deleting old collection '{collection_name}'...")
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass

    # 4. 确保集合存在
    if not client.collection_exists(collection_name):
        print(f"\n[CREATE] creating collection '{collection_name}'...")
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    # 5. 写入
    print(f"\n[WRITE] upserting {len(entries)} points...")
    points = [
        PointStruct(
            id=hashlib.md5(entry.id.encode()).hexdigest(),
            vector=v.tolist(),
            payload={
                "faq_id": entry.id,
                "question": entry.question,
                "answer": entry.answer,
                "source_ids": entry.source_ids,
                "images": entry.images,
                "content": faq_to_chunk(entry),
            },
        )
        for entry, v in zip(entries, vectors)
    ]

    batch_size = 50
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        client.upsert(collection_name=collection_name, points=batch)
        print(f"   [{i + len(batch)}/{len(points)}]")

    return {
        "status": "ok",
        "entries": len(entries),
        "dimension": dim,
        "collection": collection_name,
    }


# ============================================================
# 辅助
# ============================================================

def print_summary(entries: List[FaqEntry]):
    print(f"\n[SUMMARY] {len(entries)} FAQ entries:")
    src = {}
    mode = {}
    has_a = 0
    for e in entries:
        src[e.source_type] = src.get(e.source_type, 0) + 1
        mode[e.answer_mode] = mode.get(e.answer_mode, 0) + 1
        if e.answer:
            has_a += 1
    print(f"  source_type: {src}")
    print(f"  answer_mode: {mode}")
    print(f"  has_answer: {has_a}/{len(entries)}")


# ============================================================
# 主入口
# ============================================================

async def auto_ingest() -> bool:
    """首次启动自动入库（无 argparse，可被 main.py 调用）"""
    from datetime import datetime
    from app.ai.config import get_active_faq_collection, _write_active_faq_collection

    jsonl_entries = load_jsonl()
    xlsx_entries = load_xlsx()
    docx_entries = load_docx_faq() if DOCX_PATH.exists() else []

    merged = merge_entries(jsonl_entries, xlsx_entries)
    if docx_entries:
        merged = merge_entries(merged, docx_entries)

    print_summary(merged)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"{FAQ_COLLECTION_PREFIX}_{ts}"
    result = await ingest_faq(merged, collection_name=collection_name, rebuild=True)
    if result["status"] != "ok":
        print(f"[ERR] auto_ingest FAQ 入库失败: {result}")
        return False

    _write_active_faq_collection(collection_name)
    print(f"[AUTO-INGEST] FAQ 入库完成: {collection_name}")

    await _cleanup_old_faq_collections(keep=2)
    return True


async def main():
    import argparse
    from datetime import datetime
    from app.ai.config import _write_active_collection, get_active_collection

    parser = argparse.ArgumentParser(
        description="FAQ 三源合并 -> Qdrant 知识库导入",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m app.ai.ingestion.ingest_faq --rebuild   # 三源合并入库
  python -m app.ai.ingestion.ingest_faq --dry-run   # 预览合并结果
  python -m app.ai.ingestion.ingest_faq --cleanup   # 清理旧 FAQ 集合
        """,
    )
    parser.add_argument("--rebuild", "-r", action="store_true",
                        help="三源合并入库到新集合并自动切换")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="仅预览合并结果，不写入")
    parser.add_argument("--cleanup", action="store_true",
                        help="清理非活跃的 FAQ 旧集合")
    parser.add_argument("--output", "-o", default="",
                        help="导出合并后的 JSONL 到指定文件（不写入 Qdrant）")
    args = parser.parse_args()

    if args.cleanup:
        await _cleanup_old_faq_collections()
        return

    # 1. 加载三源
    jsonl_entries = load_jsonl()
    xlsx_entries = load_xlsx()
    docx_entries = load_docx_faq() if DOCX_PATH.exists() else []

    # 2. 三源合并：JSONL + XLSX → 再 + Docx（docx 图片自然带入）
    merged = merge_entries(jsonl_entries, xlsx_entries)
    if docx_entries:
        merged = merge_entries(merged, docx_entries)

    print_summary(merged)

    # --output: 导出合并后的 JSONL 供审阅
    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = Path.cwd() / out_path
        with open(out_path, 'w', encoding='utf-8') as f:
            for e in merged:
                f.write(json.dumps({
                    'faq_id': e.id,
                    'question': e.question,
                    'answer': e.answer,
                    'source_ids': e.source_ids,
                    'images': e.images,
                }, ensure_ascii=False) + '\n')
        print(f"\n[OUTPUT] 合并结果已导出: {out_path} ({len(merged)} 条)")
        return

    if args.dry_run:
        print("\n[DRY] 预览模式，跳过 Qdrant 写入")
        # 显示部分条目
        for e in merged[:5]:
            img_tag = f" [IMG]x{len(e.images)}" if e.images else ""
            print(f"  [{e.source_type}] {e.question[:60]}...{img_tag}")
        return

    # 5. 入库
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"{FAQ_COLLECTION_PREFIX}_{ts}"
    print(f"\n[COLLECTION] 新集合: {collection_name}")

    result = await ingest_faq(merged, collection_name=collection_name, rebuild=args.rebuild)

    if result["status"] != "ok":
        print(f"\n[ERR] {result}")
        return

    # 6. 切换活跃 FAQ 集合
    from app.ai.config import get_active_faq_collection, _write_active_faq_collection
    old_faq = get_active_faq_collection()
    _write_active_faq_collection(collection_name)
    print(f"\n[SWITCH] FAQ 活跃集合: {old_faq or '(new)'} -> {collection_name}")
    print(f"[OK] FAQ 导入完成！")

    # 7. 自动清理：保留最新 2 个 FAQ 集合
    await _cleanup_old_faq_collections(keep=2)


# ============================================================
# FAQ 集合指针管理（委托给 config.py 统一管理）
# ============================================================

async def _cleanup_old_faq_collections(keep: int = 1):
    """
    删除旧 FAQ 集合，保留最新的 keep 个。
    keep=1: 仅保留活跃集合（--cleanup 手动清理）
    keep=2: 保留活跃 + 上一版本（--rebuild 自动清理）
    """
    from app.ai.config import get_ai_config, get_active_faq_collection
    from qdrant_client import QdrantClient

    config = get_ai_config()
    active = get_active_faq_collection() or ""

    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _backend_dir / local
        client = QdrantClient(path=str(local))
    else:
        client = QdrantClient(
            host=config.qdrant_host,
            port=config.qdrant_port,
            timeout=config.qdrant_timeout,
        )

    try:
        collections = [c.name for c in client.get_collections().collections]
    except Exception as e:
        print(f"[ERR] 获取集合列表失败: {e}")
        return

    our = sorted(
        [c for c in collections if c.startswith(FAQ_COLLECTION_PREFIX)],
        reverse=True,
    )
    print(f"[CLEANUP] 找到 {len(our)} 个 FAQ 集合，保留最新 {keep} 个")
    print(f"  活跃集合: {active or '(无)'}")

    deleted = 0
    for c in our:
        if c in our[:keep]:
            print(f"  [KEEP] {c}")
            continue
        try:
            client.delete_collection(c)
            print(f"  [DEL] {c}")
            deleted += 1
        except Exception as e:
            print(f"  [ERR] 删除 {c} 失败: {e}")

    if deleted:
        print(f"\n[OK] 清理完成，删除 {deleted} 个旧集合。")
    else:
        print(f"\n[OK] 无需清理。")


if __name__ == "__main__":
    asyncio.run(main())
