#!/usr/bin/env python3
"""
检索评测：100 条查询，覆盖全部 12 个 sub_domain，计算 Hit@k / MRR。

每条查询格式：(query, domain, expected) — expected 是文中必定出现的唯一字符串。
检索结果 top-k 中只要有任一结果的 title+content 包含 expected 即视为命中。

用法：
    python ai/tools/eval_retrieval.py
"""
import asyncio
import sys
import os
import time
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

from ai.config import get_ai_config, get_active_collection_for
from ai.core.retrieval import get_retrieval_service


# ============================================================
# 100 条评测查询
# 格式: (query, domain, expected_string)
# expected 是源文档中必定出现的唯一字符串，用于判断检索结果是否命中
# ============================================================

QUERIES = [
    # ═══════════════════════════════════════════
    # company/product_catalog — 车型号精确检索 (44 条)
    # ═══════════════════════════════════════════
    ("XP1152 搬运机器人",         "company", "XP1152"),
    ("XP1151 点对点搬运",         "company", "XP1151"),
    ("XP1201 智能搬运",           "company", "XP1201"),
    ("XP3201",                   "company", "XP3201"),
    ("XNA151 平衡重式",           "company", "XNA151"),
    ("XNA121 窄通道堆高",         "company", "XNA121"),
    ("XNA101 双侧叉堆高",         "company", "XNA101"),
    ("XPL201 高速重载搬运",       "company", "XPL201"),
    ("XPL201P 物流搬运",          "company", "XPL201P"),
    ("XPL201T 薄背搬运",          "company", "XPL201T"),
    ("XPL301 重载搬运",           "company", "XPL301"),
    ("XPL501 重载搬运",           "company", "XPL501"),
    ("XS1151 薄背堆高",           "company", "XS1151"),
    ("XS1152 薄背堆高机器人",     "company", "XS1152"),
    ("XS1161 超薄托盘堆垛",       "company", "XS1161"),
    ("XS2201 重载堆高",           "company", "XS2201"),
    ("XSC081 平衡重式堆高",       "company", "XSC081"),
    ("XSC121 堆高机器人",         "company", "XSC121"),
    ("XSC151 平衡重式堆高",       "company", "XSC151"),
    ("XSC201 平衡重式堆高",       "company", "XSC201"),
    ("XQE151 室内前移式",         "company", "XQE151"),
    ("XQE122 前移式机器人",       "company", "XQE122"),
    ("XQC161 门架前移式",         "company", "XQC161"),
    ("XQC201E 门架前移式",        "company", "XQC201E"),
    ("XQS151 前移式",             "company", "XQS151"),
    ("XQS181 前移式",             "company", "XQS181"),
    ("XFL201 平衡重式具身",       "company", "XFL201"),
    ("XCD061 潜伏顶升搬运",       "company", "XCD061"),
    ("XCD101 嵌入式顶升",         "company", "XCD101"),
    ("XCD151 潜伏顶升搬运",       "company", "XCD151"),
    ("XCD301 潜伏顶升",           "company", "XCD301"),
    ("XCD501 潜伏顶升",           "company", "XCD501"),
    ("XCD031 潜伏顶升",           "company", "XCD031"),
    ("XPG151 步行式自动搬运",     "company", "XPG151"),
    ("RPG201 踏板式搬运",         "company", "RPG201"),
    ("EXP15 极简自动搬运",        "company", "EXP15"),
    ("XCART 智能观光车",          "company", "XCART"),
    ("XTD601 室内牵引式",         "company", "XTD601"),
    ("XTD401 牵引式",             "company", "XTD401"),
    ("XSG121 堆高",               "company", "XSG121"),
    ("XSF101",                   "company", "XSF101"),
    ("XC1051 搬运",               "company", "XC1051"),
    ("XC1061 搬运",               "company", "XC1061"),
    ("XCL0051",                  "company", "XCL0051"),

    # 语义查询（不含型号名）
    ("堆高机器人 窄通道 货架",     "company", "堆高"),
    ("潜伏顶升 AGV 搬运",          "company", "顶升"),
    ("平衡重式叉车 具身机器人",    "company", "平衡重式"),
    ("牵引式机器人 多车牵引",      "company", "牵引"),
    ("前移式 AGV 高位货架",        "company", "前移式"),
    ("薄背堆高 托盘堆垛",          "company", "薄背"),

    # ═══════════════════════════════════════════
    # company/cheduan_errors — 错误码 (10 条)
    # ═══════════════════════════════════════════
    ("错误码 404 是什么",          "company", "404"),
    ("错误码 200",                "company", "200"),
    ("错误码 201",                "company", "201"),
    ("错误码 300 车端故障",        "company", "300"),
    ("错误码 400",                "company", "400"),
    ("错误码 1301 激光传感器",     "company", "1301"),
    ("错误码 413 急停按钮",        "company", "413"),
    ("错误码 6301 矫正动作超时",   "company", "6301"),
    ("激光传感器无数据 错误码",    "company", "激光"),
    ("急停按钮被按下 错误码",      "company", "急停"),

    # ═══════════════════════════════════════════
    # company/cheduan_implementation — 车端实施 (3 条)
    # ═══════════════════════════════════════════
    ("车辆配网 Client模式 自研车", "company", "配网"),
    ("车端vda5050网关 配置文件",   "company", "VDA5050"),
    ("自研车 WIFI设置 车载实施",   "company", "配网"),

    # ═══════════════════════════════════════════
    # company/vda5050_protocol — 通讯协议 (5 条)
    # ═══════════════════════════════════════════
    ("VDA5050 协议 velocity",     "company", "VDA5050"),
    ("VDA5050 heartbeat 心跳",    "company", "心跳"),
    ("VDA5050 状态上报 state",     "company", "state"),
    ("VDA5050 order 订单节点",     "company", "order"),
    ("VDA5050 通讯协议 action 动作","company","action"),

    # ═══════════════════════════════════════════
    # industry/standards — 行业标准 (6 条)
    # ═══════════════════════════════════════════
    ("GB/T 30029 自动导引车 设计通则", "industry", "30029"),
    ("GB/T 30030 AGV 术语定义",       "industry", "30030"),
    ("GB/T 45750 AGV 安全规范",       "industry", "45750"),
    ("自动导引车 安全防护等级",        "industry", "安全防护"),
    ("AGV 术语 导引车 classification", "industry", "术语"),
    ("自动导引车设计 系统设计",        "industry", "系统设计"),

    # ═══════════════════════════════════════════
    # industry/navigation — 导航方式 (5 条)
    # ═══════════════════════════════════════════
    ("AGV 导航方式 技术选型",          "industry", "导航方式"),
    ("激光SLAM导航 原理",              "industry", "SLAM"),
    ("视觉导航 反光 AGV",            "industry", "反光"),
    ("二维码导航 原理 优缺点",          "industry", "二维码"),
    ("磁条导航 电磁导航 传统AGV",       "industry", "磁条"),

    # ═══════════════════════════════════════════
    # team/faq + team/usp_faq — FAQ 知识库 (7 条)
    # ═══════════════════════════════════════════
    ("USP 部署与启动 服务器要求",       "team", "部署"),
    ("机器人怎么上线 自研车上线",       "team", "上线"),
    ("interfaceName 车端配置 mqtt",    "team", "interfaceName"),
    ("地图编辑 slam 旋转建图",          "team", "SLAM"),
    ("库位配置 采点 前置点",            "team", "库位"),
    ("充电策略 充电桩 休息任务",        "team", "充电"),
    ("机器人异常 故障诊断 调度异常",    "team", "异常"),

    # ═══════════════════════════════════════════
    # team/usp_manual — 实施手册 (5 条)
    # ═══════════════════════════════════════════
    ("USP实施 服务器部署 Ubuntu 24.04", "team", "24.04"),
    ("数据盘挂载 docker 数据目录",       "team", "数据盘"),
    ("机器人类型配置 尺寸 车型",         "team", "机器人类型"),
    ("ToDesk 远程桌面 安装 常见问题",    "team", "ToDesk"),
    ("USP License 激活 授权",           "team", "License"),

    # ═══════════════════════════════════════════
    # team/usp_product — 产品手册 (3 条)
    # ═══════════════════════════════════════════
    ("USP 产品功能 调度管理",           "team", "USP"),
    ("USP 使用建议 角色 阅读建议",      "team", "阅读建议"),
    ("USP 术语 定义 架构 调度平台",     "team", "术语"),

    # ═══════════════════════════════════════════
    # team/translation — 翻译表 (5 条)
    # ═══════════════════════════════════════════
    ("取消订单 英文翻译",               "team", "取消订单"),
    ("forkControl 中文翻译",            "team", "forkControl"),
    ("紧急停止 EmergencyStop",          "team", "EmergencyStop"),
    ("MQTT 代理服务器 mqttBroker",      "team", "mqttBroker"),
    ("初始化中 Initializing 翻译",      "team", "Initializing"),

    # ═══════════════════════════════════════════
    # team/diagnosis — 诊断卡片 (7 条)
    # ═══════════════════════════════════════════
    ("定位置信度低 重定位 RESET",        "team", "置信度"),
    ("充电问题 不生成充电任务",          "team", "充电任务"),
    ("取放货失败 库位前置点 操作高度",   "team", "前置点"),
    ("机器人车离线 不可调度",            "team", "离线"),
    ("任务路径规划 任务一直规划中",      "team", "路径规划"),
    ("车辆运行异常 车不动了",            "team", "车不动"),
    ("地图定位 车头朝向 路径箭头不符",   "team", "车头朝向"),
]


# ============================================================
# 评测逻辑
# ============================================================

def hit_at_k(results, expected: str, k: int):
    """检查 top-k 中是否有结果包含 expected 字符串，返回第一个命中排名(1-indexed)或0"""
    for i, r in enumerate(results[:k]):
        text = (r.title or "") + " " + (r.content or "")
        if expected in text:
            return i + 1
    return 0


async def main():
    config = get_ai_config()
    total = len(QUERIES)

    print("=" * 72)
    print(f"检索评测 — {total} queries × 12 sub_domains")
    print(f"Model: {config.embedding_model_name}")
    print(f"Qdrant: {'local' if config.qdrant_local_path else 'REMOTE'}")
    print("=" * 72)

    # 前置检查
    for d in ["team", "company", "industry"]:
        if not get_active_collection_for(d):
            print(f"❌ {d} domain 无活跃集合，请先入库")
            return 1

    service = await get_retrieval_service()

    # 统计
    by_sub = defaultdict(lambda: {"total": 0, "hit1": 0, "hit3": 0, "hit5": 0, "mrr": 0.0})
    all_ranks = []

    print(f"\n{'#':>3} | {'sub_domain':<30} | {'query':<45} | rank | ms")
    print("-" * 120)

    for i, (query, domain, expected) in enumerate(QUERIES, 1):
        t0 = time.perf_counter()
        results = await service.retrieve_domain(query, domain, top_k=5)
        elapsed = (time.perf_counter() - t0) * 1000

        rank = hit_at_k(results, expected, k=5)
        rank1 = hit_at_k(results, expected, k=1)
        rank3 = hit_at_k(results, expected, k=3)

        # 确定 sub_domain（从首个结果取，或从 query 推断）
        sd = results[0].sub_domain if results else "N/A"
        key = f"{domain}/{sd}"

        by_sub[key]["total"] += 1
        by_sub[key]["hit1"] += (1 if rank1 else 0)
        by_sub[key]["hit3"] += (1 if rank3 else 0)
        by_sub[key]["hit5"] += (1 if rank else 0)
        by_sub[key]["mrr"] += (1.0 / rank if rank else 0)
        all_ranks.append(rank)

        status = "✅" if rank else "❌"
        rank_str = f"rank={rank}" if rank else "MISS"
        print(f"{i:>3} | {key:<30} | {query[:44]:<45} | {rank_str:>4} | {elapsed:>4.0f}ms {status}")

        # Miss 时显示 top3 title
        if not rank:
            previews = [r.title or "?" for r in results[:3]]
            print(f"    → {previews}")

    # ── 汇总 ──
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"{'Sub Domain':<35} {'#Q':>4} {'Hit@1':>7} {'Hit@3':>7} {'Hit@5':>7} {'MRR':>7}")
    print("-" * 72)

    total_h1 = total_h3 = total_h5 = 0
    total_mrr = 0.0
    for key in sorted(by_sub.keys()):
        s = by_sub[key]
        n = s["total"]
        print(f"{key:<35} {n:>4} {s['hit1']/n:>6.1%} {s['hit3']/n:>6.1%} {s['hit5']/n:>6.1%} {s['mrr']/n:>7.3f}")
        total_h1 += s["hit1"]
        total_h3 += s["hit3"]
        total_h5 += s["hit5"]
        total_mrr += s["mrr"]

    print("-" * 72)
    print(f"{'TOTAL':<35} {total:>4} {total_h1/total:>6.1%} {total_h3/total:>6.1%} {total_h5/total:>6.1%} {total_mrr/total:>7.3f}")

    # 按域汇总
    print(f"\n{'Domain':<20} {'#Q':>4} {'Hit@1':>7} {'Hit@3':>7} {'Hit@5':>7} {'MRR':>7}")
    print("-" * 55)
    for domain in ["team", "company", "industry"]:
        dom_keys = [k for k in by_sub if k.startswith(domain)]
        qs = sum(by_sub[k]["total"] for k in dom_keys)
        h1 = sum(by_sub[k]["hit1"] for k in dom_keys)
        h3 = sum(by_sub[k]["hit3"] for k in dom_keys)
        h5 = sum(by_sub[k]["hit5"] for k in dom_keys)
        mr = sum(by_sub[k]["mrr"] for k in dom_keys)
        if qs:
            print(f"{domain:<20} {qs:>4} {h1/qs:>6.1%} {h3/qs:>6.1%} {h5/qs:>6.1%} {mr/qs:>7.3f}")

    print("=" * 72)
    overall = total_h5 / total
    if overall >= 0.90:
        print(f"Hit@5 = {overall:.1%}  ✅")
    elif overall >= 0.75:
        print(f"Hit@5 = {overall:.1%}  ⚠️")
    else:
        print(f"Hit@5 = {overall:.1%}  ❌")
    print("=" * 72)

    return 0 if overall >= 0.75 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
