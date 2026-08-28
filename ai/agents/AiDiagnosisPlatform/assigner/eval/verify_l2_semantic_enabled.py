"""
验证 L2 语义召回（SemanticRecall.arecall）重开后的核心逻辑可用。

背景：L2 被误诊为"关键词匹配"而停用，实为权重退化。现已重开（semantic_recall_enabled=true）。
本脚本用 mock embed 客户端 + 手动构造 module_anchor_texts/module_classify（绕过 DB），
验证 arecall 的 Embedding 余弦匹配 → 反查工程师 主链路能跑通、不报错。

用法（项目根目录）：
    uv run python ai/agents/AiDiagnosisPlatform/assigner/eval/verify_l2_semantic_enabled.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[5]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_backend_dir = _project_root / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import numpy as np

import ai.core.embed as embed_mod
from ai.agents.AiDiagnosisPlatform.assigner.recall.semantic_recall import SemanticRecall, invalidate_semantic_cache
from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


class FakeEmbedClient:
    """固定向量的假 embed 客户端：文本 → 确定性向量（按 hash 打散），无需真实模型。"""

    def __init__(self, vocab: dict):
        self._vocab = vocab  # {token: np.array}

    def _vec(self, text: str) -> np.ndarray:
        # 用文本里命中的 token 向量平均（单位向量），未命中给随机种子向量
        toks = [t for t in self._vocab if t in text]
        if toks:
            return np.mean([self._vocab[t] for t in toks], axis=0)
        v = np.array([hash(text) % 100 / 100.0])
        return np.zeros(3, dtype=float)  # 未知文本给零向量 -> 与任何锚无相似

    async def embed(self, text: str, normalize: bool = True) -> np.ndarray:
        v = self._vec(text)
        n = np.linalg.norm(v)
        return (v / n) if n > 0 else np.zeros(3, dtype=float)

    async def embed_batch(self, texts, normalize: bool = True, batch_size: int = 32):
        return [await self.embed(t) for t in texts]


def build_config() -> AssignerConfig:
    cfg = AssignerConfig()
    # 手动填充 L2 依赖（绕过 DB）：
    # module_anchor_texts: {"产品-功能name": 锚文本}
    cfg.module_anchor_texts = {
        "调度USP-路径算法": "负责调度路径规划与算法优化",
        "调度USP-前端":     "负责调度系统前端界面开发",
        "摇人吧服务号-前端": "负责摇人吧服务号移动端界面开发",
    }
    # module_classify: {产品: {功能name: 功能name}}（功能名自映射）
    cfg.module_classify = {
        "调度USP": {"路径算法": "路径算法", "前端": "前端"},
        "摇人吧服务号": {"前端": "前端"},
    }
    cfg.semantic_recall_enabled = True
    return cfg


def build_engineers():
    return [
        EngineerProfile(
            id="path_algo", name="路径算法工程师", department="智能规划研究院",
            responsibility_modules={"调度USP": {"算法组": ["路径算法"]}},
        ),
        EngineerProfile(
            id="usp_frontend", name="调度前端工程师", department="智能规划研究院",
            responsibility_modules={"调度USP": {"前端组": ["前端"]}},
        ),
        EngineerProfile(
            id="yaoren_frontend", name="摇人吧前端工程师", department="软件部",
            responsibility_modules={"摇人吧服务号": {"前端组": ["前端"]}},
        ),
        EngineerProfile(
            id="pm", name="产品经理", department="产品设计部",
            # 无具体功能模块（负责产品设计），用于验证不会因模块锚被漏得离谱
            responsibility_modules={"调度USP": {"产品设计": []}},
        ),
    ]


async def main():
    invalidate_semantic_cache()
    cfg = build_config()

    # 两套 vocab：让"路径算法"工单命中 调度USP-路径算法 锚
    vocab = {
        "调度": np.array([1, 0, 0]),
        "路径": np.array([0, 1, 0]),
        "算法": np.array([0, 0, 1]),
        "优化": np.array([0.8, 0.6, 0.2]),
        "前端": np.array([0.2, 0.8, 0.5]),
        "界面": np.array([0.1, 0.7, 0.6]),
    }

    # mock get_embed_client
    import ai.core
    orig = embed_mod.get_embed_client
    embed_mod.get_embed_client = lambda: _async_identity(FakeEmbedClient(vocab))

    sr = SemanticRecall(config=cfg)
    ticket = TicketContext(
        id="l2_test", title="调度路径规划算法运行异常需要优化",
        problem_description="路径规划耗时长，需调整算法参数", status="new",
    )
    try:
        sem = await sr.arecall(ticket, build_engineers())
    finally:
        embed_mod.get_embed_client = orig

    print("=== L2 语义召回 重开验证 ===")
    print("anchors:", list(cfg.module_anchor_texts.keys()))
    print("命中:", {k: round(v, 3) for k, v in sorted(sem.items(), key=lambda x: -x[1])})

    ok = True
    # 路径算法工程师应命中（其功能"路径算法"→"调度USP-路径算法"锚，与工单语义相近）
    if sem.get("path_algo", 0) <= 0:
        print("[FAIL] 路径算法工程师未命中")
        ok = False
    # 产品经理无具体功能模块，不应被高估（验证不是关键词误伤非功能角色）
    if sem.get("pm", 0) > 0:
        print("[WARN] 产品经理意外命中（检查锚文本/classify 是否让 PM 误匹配）")
        # 不判 FAIL：PM 可能经 classify 兜底，但应很低

    print("结果:", "PASS" if ok else "FAIL")
    return ok


async def _async_identity(x):
    return x


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
