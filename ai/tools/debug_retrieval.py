"""Retrieval transparency debugger — 模拟 pipeline 检索全链路，展示每一步细节。

Usage:
    python ai/tools/debug_retrieval.py "车子电量够但不打断充电"
    python ai/tools/debug_retrieval.py "优先级配置不生效" --top 10 --no-cache
"""
import argparse, asyncio, io, os, sys, time, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, 'ai', '.env'))

from ai.core.retrieval import get_retrieval_service

# ── helpers ──────────────────────────────────────────────────────────

def _trunc(s: str, n: int = 120) -> str:
    """截断字符串，保留前后文"""
    s = s.strip()
    if len(s) <= n:
        return s
    return s[:n//2] + " … " + s[-n//2:]


def _bar(label: str, width: int = 70):
    print(f"\n{'─'*width}")
    print(f"  {label}")
    print(f"{'─'*width}")


def _score_bar(score: float, width: int = 20) -> str:
    """可视化分数条"""
    filled = int(score * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {score:.4f}"


async def debug_retrieval(query: str, top_k: int = 6, no_cache: bool = False):
    """完整透明检索：team / company / industry 三路 + 合并去重排序"""

    service = await get_retrieval_service()
    t0 = time.perf_counter()

    # ── 三路并行检索 ──
    _bar("🔍 三路域检索（模拟 pipeline）")

    tasks = [
        ("team",    6,  service.retrieve_domain(query, "team",     top_k=6)),
        ("company", 4,  service.retrieve_domain(query, "company",  top_k=4)),
        ("industry",3,  service.retrieve_domain(query, "industry", top_k=3)),
    ]

    all_by_domain = {}
    for domain, top_n, coro in tasks:
        t1 = time.perf_counter()
        results = await coro
        elapsed = (time.perf_counter() - t1) * 1000
        all_by_domain[domain] = results

        print(f"\n  📂 {domain.upper()} (top_{top_n}) — {len(results)} results in {elapsed:.0f}ms")
        if not results:
            print("     (空)")
            continue
        for i, r in enumerate(results):
            flag = ""
            if i == 0:
                flag = " 👈 top-1"
            print(f"     #{i+1} [{r.score:.4f}] {_score_bar(r.score, 15)} {r.title or '(无标题)'}")
            print(f"         sub_domain={r.sub_domain or '-'}  vec={r.vector_score:.4f}  sparse={r.sparse_score:.4f}")
            print(f"         {_trunc(r.content, 100)}{flag}")

    # ── 合并去重排序 ──
    _bar("🔄 合并 + 去重 + 按 score 降序")

    all_results = []
    for domain_results in all_by_domain.values():
        all_results.extend(domain_results)

    seen = set()
    uniq = []
    dupes = []
    for r in sorted(all_results, key=lambda r: r.score, reverse=True):
        if r.id not in seen:
            seen.add(r.id)
            uniq.append(r)
        else:
            dupes.append(r)

    print(f"\n  合并总数: {len(all_results)}")
    print(f"  去重后:   {len(uniq)}")
    if dupes:
        print(f"  重复移除: {len(dupes)} 条")
        for d in dupes:
            print(f"    ✂ {d.title or d.id} (保留更高分)")

    # ── Top-N 最终结果 ──
    _bar(f"✅ 最终送入 LLM 的 Top-{top_k} 文档")

    final = uniq[:top_k]
    _sub_labels = {
        "platform": "🎫 服务号", "faq": "📋 FAQ", "usp_faq": "📋 FAQ",
        "cheduan_errors": "🚗 车端", "cheduan_implementation": "🚗 车端",
        "translation": "🌐 翻译", "diagnosis": "🏭 诊断",
        "usp_manual": "📖 手册", "usp_product": "📖 产品",
        "product_catalog": "🏢 产品", "vda5050_protocol": "🏢 协议",
        "navigation": "📐 导航", "standards": "📐 标准",
    }

    for i, r in enumerate(final):
        label = _sub_labels.get(r.sub_domain, f"📄 {r.sub_domain or '知识库'}")
        print(f"\n  {label} #{i+1}  [{r.score:.4f}]  {r.title or '(无标题)'}")
        print(f"  domain={r.domain}  sub_domain={r.sub_domain}")
        print(f"  vector={r.vector_score:.4f}  sparse={r.sparse_score:.4f}")
        print(f"  id={r.id}")
        if r.images:
            print(f"  images: {r.images}")
        # 内容预览（前 300 字）
        content_preview = r.content[:300].replace('\n', ' ').strip()
        print(f"  ┌ 内容预览 ─────────────────────────────")
        print(f"  │ {content_preview}")
        if len(r.content) > 300:
            print(f"  │ … ({len(r.content)} chars total)")
        print(f"  └────────────────────────────────────────")

    if not final:
        print("\n  ⚠️ 无匹配结果 — LLM 会收到「知识库暂无匹配文档」")

    # ── 统计 ──
    _bar("📊 统计")
    total = (time.perf_counter() - t0) * 1000
    print(f"\n  总耗时: {total:.0f}ms")
    print(f"  各域命中: ", end="")
    for domain, results in all_by_domain.items():
        print(f"{domain}={len(results)} ", end="")
    print()
    print(f"  最终送入 LLM: {len(final)} chunks")

    # 分数分布
    scores = [r.score for r in final]
    if scores:
        print(f"  分数区间: {min(scores):.4f} ~ {max(scores):.4f}")
        print(f"  平均分数: {sum(scores)/len(scores):.4f}")

    return final


# ── CLI ──────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="透明化检索调试工具")
    parser.add_argument("query", help="检索查询文本")
    parser.add_argument("--top", type=int, default=6, help="最终送入 LLM 的文档数 (default: 6)")
    parser.add_argument("--no-cache", action="store_true", help="跳过缓存")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"  🔎 检索查询: {args.query}")
    print(f"{'='*70}")

    await debug_retrieval(args.query, top_k=args.top, no_cache=args.no_cache)

    print(f"\n{'='*70}")
    print(f"  完成")
    print(f"{'='*70}\n")


if __name__ == '__main__':
    asyncio.run(main())
