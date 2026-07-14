# 路径: backend/app/ai/ingestion/loader.py
"""
知识库文档导入工具

支持格式：
- Markdown 文件（## 标题 + 数字步骤列表）
- 将文档切片后存入 Qdrant 向量数据库

使用方法：
    # 导入单个文件
    python -m app.ai.ingestion.loader docs/manual.md

    # 导入目录
    python -m app.ai.ingestion.loader docs/

    # 重新导入（清空后重建）
    python -m app.ai.ingestion.loader docs/ --rebuild
"""
import os
import re
import glob
import hashlib
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from dotenv import load_dotenv

# 加载配置
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(env_path)


@dataclass
class DocChunk:
    """文档切片"""
    id: str
    title: str
    content: str
    order: int


def parse_markdown_file(content: str, file_path: str) -> List[DocChunk]:
    """
    解析 Markdown 文件，提取标题和步骤

    支持格式：
    ## 3.1 开机操作

    步骤：
    1. 确认急停已释放
    2. 长按红色电源键 3 秒
    3. 等待指示灯变绿

    注意事项：
    - 开机前务必确认急停已释放
    - 首次使用请先充电
    """
    chunks = []

    # 提取所有 ## 标题
    section_pattern = r'^(#{1,6})\s+(.+)$'
    lines = content.split('\n')

    current_title = None
    current_content = []
    current_order = 0

    for line in lines:
        # 跳过空行
        if not line.strip():
            continue

        # 检查是否是标题
        title_match = re.match(section_pattern, line)
        if title_match:
            # 保存之前的切片
            if current_title and current_content:
                chunk_content = '\n'.join(current_content).strip()
                if chunk_content:
                    chunk_id = hashlib.md5(
                        f"{current_title}{chunk_content}".encode()
                    ).hexdigest()[:12]

                    chunks.append(DocChunk(
                        id=chunk_id,
                        title=current_title,
                        content=chunk_content,
                        order=current_order,
                    ))
                    current_order += 1

            # 开始新的切片
            current_title = title_match.group(2).strip()
            current_content = []

        # 检查是否是步骤（数字开头）
        step_match = re.match(r'^\d+[.、:：]\s*(.+)$', line)
        if step_match:
            current_content.append(step_match.group(1).strip())

        # 检查是否是注意事项（列表项）
        bullet_match = re.match(r'^[-\*•]\s+(.+)$', line)
        if bullet_match:
            current_content.append(f"注意: {bullet_match.group(1).strip()}")

    # 保存最后一个切片
    if current_title and current_content:
        chunk_content = '\n'.join(current_content).strip()
        if chunk_content:
            chunk_id = hashlib.md5(
                f"{current_title}{chunk_content}".encode()
            ).hexdigest()[:12]

            chunks.append(DocChunk(
                id=chunk_id,
                title=current_title,
                content=chunk_content,
                order=current_order,
            ))

    return chunks


async def ingest_documents(
    file_paths: List[str],
    rebuild: bool = False,
) -> Dict[str, Any]:
    """
    导入文档到向量数据库

    Args:
        file_paths: 文件路径列表（支持 glob 模式）
        rebuild: 是否先清空再重建

    Returns:
        导入统计信息
    """
    from app.ai.core.retrieval import QdrantClientWrapper as QdrantWrapper
    from app.ai.core import get_embed_client
    from app.ai.config import get_ai_config

    config = get_ai_config()

    # 1. 获取客户端
    qdrant = await QdrantWrapper.from_config()
    embed_client = await get_embed_client()

    # 2. 收集所有文件
    all_files = []
    for pattern in file_paths:
        if os.path.isdir(pattern):
            all_files.extend(glob.glob(os.path.join(pattern, "**/*.md"), recursive=True))
        elif os.path.isfile(pattern):
            all_files.append(pattern)
        else:
            all_files.extend(glob.glob(pattern, recursive=True))

    if not all_files:
        return {"status": "error", "message": f"未找到文件: {file_paths}"}

    # 3. 解析所有文件
    all_chunks = []
    for file_path in all_files:
        print(f"📄 解析文件: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        chunks = parse_markdown_file(content, file_path)
        all_chunks.extend(chunks)

        print(f"   提取了 {len(chunks)} 个切片")

    if not all_chunks:
        return {"status": "error", "message": "未提取到任何切片"}

    # 4. 生成向量
    print(f"\n🔢 生成 {len(all_chunks)} 个向量...")

    vectors = []
    for chunk in all_chunks:
        # 组合标题和内容作为向量输入
        text = f"{chunk.title}\n{chunk.content}"
        vector = await embed_client.embed(text)
        vectors.append(vector)

    # 5. 写入 Qdrant
    print(f"✍️  写入 {len(all_chunks)} 个向量到 Qdrant...")

    collection_name = config.qdrant_collection_name

    # 直接创建客户端用于写入
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    write_client = QdrantClient(
        host=config.qdrant_host,
        port=config.qdrant_port,
        timeout=config.qdrant_timeout,
    )

    # 如果重建，先删除集合
    if rebuild:
        print(f"🗑️  删除旧集合: {collection_name}")
        try:
            write_client.delete_collection(collection_name)
        except Exception:
            pass

    # 创建或获取集合
    await qdrant.create_collection_if_not_exists(
        vector_size=vectors[0].shape[-1],
        sparse_vector_size=10000,
    )

    # 批量写入
    points = []
    for i, (chunk, vector) in enumerate(zip(all_chunks, vectors)):
        points.append(PointStruct(
            id=chunk.id,
            vector=vector.tolist(),
            payload={
                "title": chunk.title,
                "content": chunk.content,
                "order": chunk.order,
                "file": chunk.id,  # 来源文件（简化处理）
            },
        ))

    # 写入
    write_client.upsert(
        collection_name=collection_name,
        points=points,
    )

    return {
        "status": "ok",
        "files": len(all_files),
        "chunks": len(all_chunks),
        "vectors": len(vectors),
    }


async def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="知识库文档导入工具")
    parser.add_argument("paths", nargs="+", help="文件或目录路径")
    parser.add_argument("--rebuild", action="store_true", help="先清空再重建")

    args = parser.parse_args()

    result = await ingest_documents(args.paths, rebuild=args.rebuild)

    if result.get("status") == "ok":
        print(f"\n✅ 导入完成！")
        print(f"   处理文件: {result['files']}")
        print(f"   生成切片: {result['chunks']}")
        print(f"   生成向量: {result['vectors']}")
    else:
        print(f"\n❌ 导入失败: {result.get('message')}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
