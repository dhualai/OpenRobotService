"""离线构建 code_skill 语义索引（embedding）——一次性生成，供本地检索使用。

用法：
    python ai/agents/AiTaskPlatform/code_skill/build_semantic_index.py

产物（与 code_index.json 同目录）：
    code_index_semantic.npy         N×dim 归一化向量矩阵
    code_index_semantic_ids.json    与向量行对齐的函数指纹（name/file_path/line_start）

依赖现有 code_index.json（已由 CodeIndexer 生成）。
可用 code_skill 前先运行一次：生成语义向量后，CodeRetriever 对中文/口语 query
（如「上轨」「避让后重新上轨」）的语义召回显著优于纯关键词匹配。
"""

import asyncio
import os
import sys
from pathlib import Path

# 保证以项目根为入口
_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai.agents.AiTaskPlatform.code_skill.indexer import CodeIndexer  # noqa: E402


async def main() -> None:
    json_path = _ROOT / "ai" / "code_index.json"
    if not json_path.exists():
        print(f"[build_semantic] 未找到 {json_path}，请先运行 CodeIndexer 生成索引")
        return

    indexer = CodeIndexer.load(str(json_path))
    print(f"[build_semantic] 加载 {indexer.function_count} 个函数，构建语义索引（本地 bge 模型）...")
    await indexer.build_semantic(str(json_path))
    if indexer.semantic is not None and indexer.semantic.is_ready:
        print(f"[build_semantic] ✅ 完成：{len(indexer.semantic)} 个函数已向量化")
    else:
        print("[build_semantic] ⚠️ 构建失败或返回空，请检查 embedding 环境")


if __name__ == "__main__":
    asyncio.run(main())
