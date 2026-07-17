"""
问题排查树导入 → Qdrant

读取 docs/问题排查树_v1.json，将每个 symptom 的决策树线性化为
可读的步骤文本，向量化写入独立 Qdrant collection。

流程：
  1. 加载 JSON，遍历 categories[].symptoms[]
  2. 递归线性化每个 symptom 的 tree
  3. 向量化写入 Qdrant（troubleshooting 专用 collection）
  4. 写指针文件 hot-swap

使用方法：
    python -m ai.ingestion.ingest_troubleshooting --rebuild   # 入库
    python -m ai.ingestion.ingest_troubleshooting --dry-run   # 预览
    python -m ai.ingestion.ingest_troubleshooting --cleanup   # 清理旧集合
"""
import sys
import json
import hashlib
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

# 确保项目根在 path 中
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")
# (ai/.env 不再需要，已移至项目根 ai/ 目录)

# 路径常量
def _docs_dir() -> Path:
    from ai.config import get_docs_dir
    return get_docs_dir()

JSON_PATH = _docs_dir() / "问题排查树_v1.json"

# Qdrant 排查树集合前缀
TROUBLESHOOTING_COLLECTION_PREFIX = "troubleshooting"


# ============================================================
# 数据模型
# ============================================================

@dataclass
class TroubleshootingChunk:
    """排查树 chunk"""
    symptom_id: str
    symptom_name: str
    category: str
    linearized_tree: str


# ============================================================
# 步骤 1：加载 JSON
# ============================================================

def load_troubleshooting_json() -> List[TroubleshootingChunk]:
    """加载 JSON 文件，线性化所有 symptom"""
    with open(JSON_PATH, encoding='utf-8') as f:
        data = json.load(f)

    chunks = []
    for category in data.get("categories", []):
        cat_name = category.get("name", "")
        for symptom in category.get("symptoms", []):
            sid = symptom.get("id", "")
            sname = symptom.get("name", "")
            tree = symptom.get("tree", {})

            linearized = linearize_symptom(sname, tree)

            chunks.append(TroubleshootingChunk(
                symptom_id=sid,
                symptom_name=sname,
                category=cat_name,
                linearized_tree=linearized,
            ))

    return chunks


# ============================================================
# 步骤 2：线性化决策树
# ============================================================

def linearize_symptom(symptom_name: str, tree: Dict) -> str:
    """
    将一棵 symptom 的决策树递归拍平为可读文本。

    格式：
        {symptom_name}

        第1步：{root.description}
          → 用户说「{condition}」→ 【结论】原因：{cause}。方案：{solution}
          → 用户说「{condition}」→ 进入第2步

        第2步：{next.description}
          ...
    """
    lines = [symptom_name, ""]
    root = tree.get("root", {})
    if root:
        _walk_node(root, lines, step_counter=[0])
    return "\n".join(lines)


def _walk_node(node: Dict, lines: List[str], step_counter: List[int], indent: str = ""):
    """递归遍历节点，追加到 lines"""
    node_type = node.get("node_type", "step")

    # ---- conclusion: 直接输出 cause + solution ----
    if node_type == "conclusion":
        cause = node.get("cause", "")
        solution = node.get("solution", "")
        if cause or solution:
            cause_str = f"原因：{cause}。" if cause else ""
            lines.append(f"{indent}【结论】{cause_str}方案：{solution}")
        return

    # ---- checklist: 逐条检查项 ----
    if node_type == "checklist":
        desc = node.get("description", "")
        if desc:
            lines.append(f"{indent}{desc}")
        for i, item in enumerate(node.get("items", []), 1):
            check = item.get("check", "")
            result = item.get("result", {})
            r_cause = result.get("cause", "")
            r_solution = result.get("solution", "")
            lines.append(f"{indent}  {i}. 检查：{check}")
            if r_cause or r_solution:
                c_str = f"原因：{r_cause}。" if r_cause else ""
                lines.append(f"{indent}     → 如异常：【结论】{c_str}方案：{r_solution}")
        return

    # ---- step / diagnosis / classification: 有序步骤 ----
    step_counter[0] += 1
    step_num = step_counter[0]
    desc = node.get("description", "")
    lines.append(f"第{step_num}步：{desc}")

    branches = node.get("branches", [])
    if not branches:
        return

    for branch in branches:
        condition = branch.get("condition", "")

        if "result" in branch:
            result = branch["result"]
            cause = result.get("cause", "")
            solution = result.get("solution", "")
            c_str = f"原因：{cause}。" if cause else ""
            lines.append(f"{indent}  → 用户说「{condition}」→ 【结论】{c_str}方案：{solution}")

        elif "next" in branch:
            next_node = branch["next"]
            # 特殊处理：condition 为 "以上都不是" 时不带编号，因为它是兜底分支
            lines.append(f"{indent}  → 用户说「{condition}」→ 进入第{step_counter[0] + 1}步")
            _walk_node(next_node, lines, step_counter, indent + "    ")


# ============================================================
# 步骤 3：入库
# ============================================================

def chunk_to_text(chunk: TroubleshootingChunk) -> str:
    """生成向量化文本（symptom_name + linearized_tree）"""
    return f"{chunk.symptom_name}\n{chunk.linearized_tree}"


async def ingest_troubleshooting(
    chunks: List[TroubleshootingChunk],
    collection_name: str,
    rebuild: bool = False,
) -> Dict[str, Any]:
    """将排查树 chunk 向量化写入 Qdrant"""
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
        print(f"\n[INGEST] {len(chunks)} troubleshooting chunks -> Qdrant (local: {local})")
    else:
        print(f"\n[INGEST] {len(chunks)} troubleshooting chunks -> Qdrant ({config.qdrant_host}:{config.qdrant_port})")
    print(f"   Collection: {collection_name}")

    # 1. 向量化
    print(f"\n[EMBED] generating vectors ({len(chunks)} texts)...")
    texts = [chunk_to_text(c) for c in chunks]
    vectors = await embed_client.embed_batch(texts)
    dim = vectors[0].shape[-1]
    print(f"   dim={dim}")

    # 2. 连接 Qdrant
    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _project_root / local
        client = QdrantClient(path=str(local))
    else:
        client = QdrantClient(
            host=config.qdrant_host,
            port=config.qdrant_port,
            timeout=config.qdrant_timeout,
            check_compatibility=False,
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
    print(f"\n[WRITE] upserting {len(chunks)} points...")
    points = [
        PointStruct(
            id=hashlib.md5(c.symptom_id.encode()).hexdigest(),
            vector=v.tolist(),
            payload={
                "symptom_id": c.symptom_id,
                "symptom_name": c.symptom_name,
                "category": c.category,
                "linearized_tree": c.linearized_tree,
                "content": chunk_to_text(c),
                "source": "问题排查树_v1.json",
            },
        )
        for c, v in zip(chunks, vectors)
    ]

    batch_size = 50
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        client.upsert(collection_name=collection_name, points=batch)
        print(f"   [{i + len(batch)}/{len(points)}]")

    return {
        "status": "ok",
        "entries": len(chunks),
        "dimension": dim,
        "collection": collection_name,
    }


# ============================================================
# 辅助
# ============================================================

def print_summary(chunks: List[TroubleshootingChunk]):
    """打印汇总"""
    print(f"\n[SUMMARY] {len(chunks)} troubleshooting chunks:")
    cats = {}
    for c in chunks:
        cats[c.category] = cats.get(c.category, 0) + 1
    for cat, count in cats.items():
        print(f"  {cat}: {count} symptoms")


async def _cleanup_old_troubleshooting_collections(keep: int = 1):
    """删除旧排查树集合，保留最新的 keep 个"""
    from ai.config import get_ai_config, get_active_troubleshooting_collection
    from qdrant_client import QdrantClient

    config = get_ai_config()
    active = get_active_troubleshooting_collection() or ""

    if config.qdrant_local_path:
        local = Path(config.qdrant_local_path)
        if not local.is_absolute():
            local = _project_root / local
        client = QdrantClient(path=str(local))
    else:
        client = QdrantClient(
            host=config.qdrant_host,
            port=config.qdrant_port,
            timeout=config.qdrant_timeout,
            check_compatibility=False,
        )

    try:
        collections = [c.name for c in client.get_collections().collections]
    except Exception as e:
        print(f"[ERR] 获取集合列表失败: {e}")
        return

    our = sorted(
        [c for c in collections if c.startswith(TROUBLESHOOTING_COLLECTION_PREFIX)],
        reverse=True,
    )
    print(f"[CLEANUP] 找到 {len(our)} 个排查树集合，保留最新 {keep} 个")
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


# ============================================================
# 主入口
# ============================================================

async def auto_ingest() -> bool:
    """首次启动自动入库（被 main.py 调用）"""
    from datetime import datetime
    from ai.config import _write_active_troubleshooting_collection

    chunks = load_troubleshooting_json()
    print_summary(chunks)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"{TROUBLESHOOTING_COLLECTION_PREFIX}_{ts}"
    result = await ingest_troubleshooting(chunks, collection_name=collection_name, rebuild=True)
    if result["status"] != "ok":
        print(f"[ERR] auto_ingest 排查树入库失败: {result}")
        return False

    _write_active_troubleshooting_collection(collection_name)
    print(f"[AUTO-INGEST] 排查树入库完成: {collection_name}")

    await _cleanup_old_troubleshooting_collections(keep=2)
    return True


async def main():
    import argparse
    from datetime import datetime
    from ai.config import (
        get_active_troubleshooting_collection,
        _write_active_troubleshooting_collection,
    )

    parser = argparse.ArgumentParser(
        description="问题排查树 -> Qdrant 知识库导入",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m ai.ingestion.ingest_troubleshooting --rebuild   # 入库
  python -m ai.ingestion.ingest_troubleshooting --dry-run   # 预览
  python -m ai.ingestion.ingest_troubleshooting --cleanup   # 清理旧集合
        """,
    )
    parser.add_argument("--rebuild", "-r", action="store_true",
                        help="线性化入库到新集合并自动切换")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="仅预览线性化结果，不写入")
    parser.add_argument("--cleanup", action="store_true",
                        help="清理非活跃的排查树旧集合")
    args = parser.parse_args()

    if args.cleanup:
        await _cleanup_old_troubleshooting_collections()
        return

    # 1. 加载 + 线性化
    chunks = load_troubleshooting_json()
    print_summary(chunks)

    if args.dry_run:
        print("\n[Dry-run] 线性化预览 (前 3 个 symptom):\n")
        for c in chunks[:3]:
            print(f"━━━ {c.symptom_id}: {c.symptom_name} [{c.category}] ━━━")
            print(c.linearized_tree)
            print()
        return

    # 2. 入库
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collection_name = f"{TROUBLESHOOTING_COLLECTION_PREFIX}_{ts}"
    print(f"\n[COLLECTION] 新集合: {collection_name}")

    result = await ingest_troubleshooting(chunks, collection_name=collection_name, rebuild=args.rebuild)

    if result["status"] != "ok":
        print(f"\n[ERR] {result}")
        return

    # 3. 切换活跃集合
    old = get_active_troubleshooting_collection()
    _write_active_troubleshooting_collection(collection_name)
    print(f"\n[SWITCH] 排查树活跃集合: {old or '(new)'} -> {collection_name}")
    print(f"[OK] 排查树导入完成！")

    # 4. 清理旧集合
    await _cleanup_old_troubleshooting_collections(keep=2)


if __name__ == "__main__":
    asyncio.run(main())
