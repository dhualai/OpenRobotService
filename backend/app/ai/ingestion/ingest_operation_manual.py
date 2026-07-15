"""
USP 实施与操作手册 → Qdrant 知识库导入

流程：
  1. .docx → pandoc 转 markdown（.md 跳过此步）
  2. 去除封面/目录，只保留正文（# 0 文档信息 起）
  3. 按 ## 二级标题切块，保留 ###/#### 层级在块内
  4. 向量化写入 Qdrant（collection: operation_docs）

使用方法：
    python -m app.ai.ingestion.ingest_operation_manual --rebuild   # docx → 入库
    python -m app.ai.ingestion.ingest_operation_manual --dry-run   # 仅预览切分
    python -m app.ai.ingestion.ingest_operation_manual -f other.md # 指定源文件
"""
import os
import re
import sys
import hashlib
import asyncio
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from dotenv import load_dotenv

# 确保 backend 在 path 中
_backend_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

load_dotenv(_backend_dir / ".env")
load_dotenv(_backend_dir / "app" / "ai" / ".env")

# 默认源文件：通过 get_docs_dir() 动态获取文档路径
def _default_source() -> str:
    from app.ai.config import get_docs_dir
    return str(get_docs_dir() / "operation_doc" / "USP 实施与操作手册.docx")

# 正文开始的标记（去除封面/目录/修订记录等）
CONTENT_START_MARKERS = [
    r'^# \*?\*?0 文档信息',
    r'^# \*?\*?1 USP',
    r'^# \*?\*?1 部署',
]


# ============================================================
# 数据模型
# ============================================================

@dataclass
class Chunk:
    """一个知识块 = 一个 ## 标题段落"""
    id: str
    title: str          # 如 "2.1 机器人上线"
    section: str         # 如 "2.1"
    chapter: str         # 如 "2"
    content: str         # 完整正文（含 ## 标题 + ###/#### 子节）
    images: List[str] = field(default_factory=list)


# ============================================================
# 步骤 1：docx → markdown（pandoc）
# ============================================================

def convert_docx(filepath: str) -> str:
    """
    用 pandoc 将 .docx 转为 markdown 文本。

    Returns:
        markdown 字符串
    Raises:
        subprocess.CalledProcessError: pandoc 执行失败
        FileNotFoundError: pandoc 未安装
    """
    result = subprocess.run(
        ["pandoc", filepath, "-f", "docx", "-t", "markdown", "--wrap=none"],
        capture_output=True, encoding="utf-8", check=True,
    )
    return result.stdout


# ============================================================
# 步骤 2：去除前端内容
# ============================================================

def strip_frontmatter(text: str) -> str:
    """
    去除封面、目录等前端内容，从第一个正文标题开始保留。

    查找顺序： # 0 文档信息 → # 1 USP → # 1 部署
    没找到时返回原文。
    """
    for marker in CONTENT_START_MARKERS:
        m = re.search(marker, text, re.MULTILINE)
        if m:
            return text[m.start():]
    return text


# ============================================================
# 步骤 3：按 ## 切块
# ============================================================

def parse_manual(text: str, source_label: str = "usp-manual") -> List[Chunk]:
    """
    按 ## 二级标题切块，保留 ###/#### 标题层级在块内供 LLM 区分子主题。

    Args:
        text: markdown 正文
        source_label: 用于生成 chunk ID 的标识
    """
    # 匹配 ## 标题（[ \t] 而非 \s，避免 Windows \r\n 下跨行匹配）
    heading_pattern = re.compile(r'^##[ \t]+(.+?)[ \t\r]*$', re.MULTILINE)
    matches = list(heading_pattern.finditer(text))

    if not matches:
        print("[ERR] 未找到任何 ## 标题，请检查文档格式")
        return []

    chunks: List[Chunk] = []
    for i, m in enumerate(matches):
        raw_title = m.group(1).strip()
        # 剥离 ** 粗体标记：**2.1 机器人上线** → 2.1 机器人上线
        title = raw_title.strip('*').strip()
        # 防御：清理可能的残余 ## 前缀（\r\n 跨行匹配遗留）
        if title.startswith('##'):
            title = title.lstrip('#').strip()
        if not title:
            continue  # 跳过空标题行

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        raw_content = text[start:end].strip()

        # 跳过只有标题没有正文的块
        body_lines = raw_content.split('\n')[1:]
        body = '\n'.join(body_lines).strip()
        if not body:
            continue

        # 提取章节号
        sec_match = re.match(r'([\d.]+|[^\s]+)\s', title)
        section_num = sec_match.group(1) if sec_match else title[:20]
        chapter_num = section_num.split('.')[0] if '.' in section_num else section_num

        # 提取图片路径
        images = [
            img_m.group(1)
            for img_m in re.finditer(r'!\[.*?\]\((media/[^)]+)\)', raw_content)
        ]

        # 生成稳定 ID
        chunk_id = hashlib.md5((f"{source_label}:" + title).encode()).hexdigest()

        chunks.append(Chunk(
            id=chunk_id,
            title=title,
            section=section_num,
            chapter=chapter_num,
            content=raw_content,
            images=images,
        ))

    # 第二遍：对含 ### 子标题的大块按 ### 拆分，提升 LLM 子主题匹配精度
    chunks = _split_by_h3(chunks, source_label)

    return chunks


def _split_by_h3(chunks: List[Chunk], source_label: str) -> List[Chunk]:
    """
    仅拆分 §2.1（机器人上线），其 ### 子节（自研车/睿芯行/科钛车）差异大，
    需要独立成块供 LLM 精确匹配。其他节的 ### 保持完整。
    """
    SPLIT_SECTIONS = {"2.1"}  # 只拆这些 ## 节
    h3_pattern = re.compile(r'^###[ \t]+(.+?)[ \t\r]*$', re.MULTILINE)
    result: List[Chunk] = []

    for c in chunks:
        if c.section not in SPLIT_SECTIONS:
            result.append(c)
            continue

        h3_matches = list(h3_pattern.finditer(c.content))
        if not h3_matches:
            result.append(c)
            continue

        parent_title_line = c.content.split('\n')[0].strip()
        parent_title = parent_title_line.lstrip('#').strip().strip('*').strip()

        for i, m in enumerate(h3_matches):
            raw_h3 = m.group(1).strip()
            h3_title = raw_h3.strip('*').strip()
            if not h3_title:
                continue

            start = m.start()
            end = h3_matches[i + 1].start() if i + 1 < len(h3_matches) else len(c.content)

            h3_content = c.content[start:end].strip()
            body = '\n'.join(h3_content.split('\n')[1:]).strip()
            if not body:
                continue

            images = [
                img_m.group(1)
                for img_m in re.finditer(r'!\[.*?\]\((media/[^)]+)\)', h3_content)
            ]

            full_title = f"{parent_title} > {h3_title}"
            sub_section = f"{c.section}.{i + 1}"
            chunk_id = hashlib.md5((f"{source_label}:" + full_title).encode()).hexdigest()

            result.append(Chunk(
                id=chunk_id,
                title=full_title,
                section=sub_section,
                chapter=c.chapter,
                content=h3_content,
                images=images,
            ))

    return result


# ============================================================
# 步骤 4：向量化 & 写入 Qdrant
# ============================================================

async def ingest_chunks(chunks: List[Chunk], collection_name: str, rebuild: bool = False) -> Dict[str, Any]:
    """将 chunk 列表向量化并写入 Qdrant 指定集合"""
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
        print(f"\n[INGEST] {len(chunks)} chunks -> Qdrant (local: {local})")
    else:
        print(f"\n[INGEST] {len(chunks)} chunks -> Qdrant ({config.qdrant_host}:{config.qdrant_port})")
    print(f"   Collection: {collection_name}")

    # 1. 向量化
    print(f"\n[EMBED] generating vectors ({len(chunks)} texts)...")
    texts = [f"{c.title}\n{c.content}" for c in chunks]
    vectors = await embed_client.embed_batch(texts)
    dim = vectors[0].shape[-1]
    print(f"   dim={dim}")

    # 2. 连接 Qdrant
    if config.qdrant_local_path:
        # 将相对路径解析为绝对路径（不受 CWD 影响），与 retrieval.py 逻辑一致
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

    collection = collection_name

    # 3. 重建（同名集合存在则先删）
    if rebuild:
        print(f"\n[DROP] deleting old collection '{collection}'...")
        try:
            client.delete_collection(collection)
        except Exception:
            pass

    # 4. 确保集合存在
    if not client.collection_exists(collection):
        print(f"\n[CREATE] creating collection '{collection}'...")
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    # 5. 写入
    print(f"\n[WRITE] upserting {len(chunks)} points...")
    points = [
        PointStruct(
            id=c.id,
            vector=v.tolist(),
            payload={
                "title": c.title,
                "section": c.section,
                "chapter": c.chapter,
                "content": c.content,
                "images": c.images,
                "source": "USP实施与操作手册",
            },
        )
        for c, v in zip(chunks, vectors)
    ]

    batch_size = 50
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        client.upsert(collection_name=collection, points=batch)
        print(f"   [{i + len(batch)}/{len(points)}]")

    return {
        "status": "ok",
        "chunks": len(chunks),
        "dimension": dim,
        "collection": collection,
    }


# ============================================================
# 辅助
# ============================================================

def print_chunks_summary(chunks: List[Chunk]):
    """打印切分摘要"""
    print(f"\n[PARSE] {len(chunks)} chunks:")
    print("-" * 65)
    for c in chunks:
        img_tag = f" [IMG]x{len(c.images)}" if c.images else ""
        print(f"  [{c.section:>6}] {c.title[:45]:<45} {len(c.content):>5} chars{img_tag}")
    print("-" * 65)


def resolve_source(filepath: str) -> str:
    """解析源文件路径，支持自动查找备选路径"""
    if os.path.isfile(filepath):
        return filepath

    print(f"[WARN] 文件不存在: {filepath}")

    # 备选路径
    from app.ai.config import get_docs_dir
    docs = get_docs_dir()
    alternatives = [
        docs / "operation_doc" / "USP 实施与操作手册.docx",
        docs / "operation_doc" / "USP实施与操作手册.md",
    ]
    for alt in alternatives:
        if alt.is_file():
            print(f"   → 使用: {alt}")
            return str(alt)

    raise FileNotFoundError(f"未找到源文件，请用 --file 指定路径。尝试过: {filepath}")


# ============================================================
# 主入口
# ============================================================

async def auto_ingest() -> bool:
    """首次启动 / clone 后自动入库（无 argparse，可被 main.py 调用）"""
    from datetime import datetime
    from app.ai.config import _write_active_collection, get_active_collection

    filepath = resolve_source(_default_source())
    print(f"[AUTO-INGEST] 操作手册源文件: {filepath}")

    if filepath.lower().endswith('.docx'):
        try:
            text = convert_docx(filepath)
        except FileNotFoundError:
            print("[ERR] pandoc 未安装，跳过自动入库")
            return False
        except subprocess.CalledProcessError as e:
            print(f"[ERR] pandoc 转换失败: {e.stderr}")
            return False
    else:
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()

    text = strip_frontmatter(text)
    chunks = parse_manual(text)
    if not chunks:
        print("[ERR] auto_ingest: 未提取到任何 chunk")
        return False

    print_chunks_summary(chunks)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"operation_docs_{ts}"
    result = await ingest_chunks(chunks, collection_name=collection_name, rebuild=True)
    if result["status"] != "ok":
        print(f"[ERR] auto_ingest 入库失败: {result}")
        return False

    _write_active_collection(collection_name)
    print(f"[AUTO-INGEST] 操作手册入库完成: {collection_name}")

    await _cleanup_old_collections(keep=2)
    return True


async def main():
    import argparse
    from datetime import datetime
    from app.ai.config import _write_active_collection, get_active_collection

    parser = argparse.ArgumentParser(
        description="USP 实施与操作手册 -> Qdrant 知识库导入",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m app.ai.ingestion.ingest_operation_manual --rebuild   # 入库（新集合+自动切换）
  python -m app.ai.ingestion.ingest_operation_manual --dry-run   # 预览切分
  python -m app.ai.ingestion.ingest_operation_manual --cleanup   # 清理旧集合
        """,
    )
    parser.add_argument(
        "--file", "-f",
        default=_default_source(),
        help="源文件路径，支持 .docx / .md",
    )
    parser.add_argument(
        "--rebuild", "-r",
        action="store_true",
        help="入库到新集合并自动切换为活跃集合",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="仅切分预览，不写入 Qdrant",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="清理非活跃的旧集合",
    )
    args = parser.parse_args()

    if args.cleanup:
        await _cleanup_old_collections()
        return

    # 0. 解析源文件
    filepath = resolve_source(args.file)

    # 1. 获取 markdown 文本
    if filepath.lower().endswith('.docx'):
        print(f"[CONVERT] pandoc: {filepath}")
        try:
            text = convert_docx(filepath)
        except FileNotFoundError:
            print("[ERR] pandoc 未安装。请先安装 pandoc：https://pandoc.org/installing.html")
            return
        except subprocess.CalledProcessError as e:
            print(f"[ERR] pandoc 转换失败: {e.stderr}")
            return
        print(f"   → {len(text)} chars")
    else:
        print(f"[READ] {filepath}")
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()

    # 2. 去除前端内容
    before = len(text)
    text = strip_frontmatter(text)
    stripped = before - len(text)
    if stripped:
        print(f"[CLEAN] 去除前端 {stripped} chars（封面/目录）")

    # 3. 切块
    chunks = parse_manual(text)

    if not chunks:
        print("[ERR] 未提取到任何块，请检查文档格式。"
              " 确保有 ## 二级标题（如 ## **2.1 机器人上线**）")
        return

    print_chunks_summary(chunks)

    if args.dry_run:
        print("\n[DRY] 预览模式，跳过 Qdrant 写入")
        return

    # 4. 生成新集合名（带时间戳，避免与正在使用的集合冲突）
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"operation_docs_{ts}"
    print(f"\n[COLLECTION] 新集合: {collection_name}")

    # 5. 向量化 & 写入新集合
    result = await ingest_chunks(chunks, collection_name=collection_name, rebuild=args.rebuild)

    if result["status"] != "ok":
        print(f"\n[ERR] {result}")
        return

    # 6. 写入指针文件，切换活跃集合
    old_collection = get_active_collection()
    _write_active_collection(collection_name)
    print(f"\n[SWITCH] 活跃集合已切换: {old_collection} -> {collection_name}")
    print(f"[OK] 导入完成！服务无需重启，下次检索自动使用新集合。")

    # 7. 自动清理：保留最新 2 个集合（当前 + 上一个备回滚）
    await _cleanup_old_collections(keep=2)


async def _cleanup_old_collections(keep: int = 1):
    """
    删除旧集合，保留最新的 keep 个。
    keep=1: 仅保留活跃集合（--cleanup 手动清理）
    keep=2: 保留活跃 + 上一版本（--rebuild 自动清理）
    """
    from app.ai.config import get_ai_config, get_active_collection
    from qdrant_client import QdrantClient

    config = get_ai_config()
    active = get_active_collection()

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

    our_collections = sorted(
        [c for c in collections if c.startswith("operation_docs")],
        reverse=True,  # 最新的在前
    )
    print(f"[CLEANUP] 找到 {len(our_collections)} 个集合，保留最新 {keep} 个")
    print(f"  活跃集合: {active}")

    deleted = 0
    for c in our_collections:
        if c in our_collections[:keep]:
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
