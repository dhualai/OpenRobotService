# -*- coding: utf-8 -*-
"""症状化管线试点：topo 功能文档 → 症状对照 chunk（L3 语言对齐层）。

问题：文档说「阻塞拦截/NO_PATH」，用户说「绕远路/不规划路径」——语义和字面都对不上，
检索命中不了算法层知识。本管线在**入库时**把每篇功能文档的「结果状态/异常/边界」
自动转成「用户会怎么问」的症状对照小节（LLM 生成 + 人工抽检），追加到原文档末尾，
使每篇文档自带一层口语症状入口。算法同事每更新文档，重跑本管线即可增量对齐。

用法：
    python ai/scripts/symptomize_docs.py                # 试点 4 篇路径规划文档
    python ai/scripts/symptomize_docs.py --docs sv_plan_单车路径规划.md
    python ai/scripts/symptomize_docs.py --all          # 全部 topo 文档（后续铺开用）

流程：读 topo 源文档 → 抽功能摘要 + 异常相关小节 → LLM 生成症状对照 →
追加到原文 → 写 kb/team/USP/algorithm/（入库：ingest_all --domain team）。
"""
import argparse
import asyncio
import re
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

SRC = Path(r"D:/Code/OpenRobotService_Data/usp_topo_export")
OUT = Path(r"D:/Code/OpenRobotService_Data/kb/team/USP/algorithm")

PILOT = ["sv_plan_单车路径规划.md", "mv_plan_多车路径规划.md",
         "lmapf_L-MAPF.md", "mv_cent_中心化多车路径规划.md"]

# 抽异常相关小节的标题关键词
_ANOMALY_HEAD = re.compile(
    r"结果状态|异常|校验|阻塞|边界|超时|失败|限制|状态|拦截|错误|冲突|降级|兜底")
_SUMMARY_CHARS = 1800
_ANOMALY_CHARS = 5000

_PROMPT = """你是 USP 大调度系统的知识库工程师。现场用户不会用文档语言提问，他们说口语：
「车绕远路」「不走最短路径」「不规划路径」「规划不出来」「任务卡住不动」「下发就报错」「车不动」等。
下面是一篇功能文档的【功能摘要】和【异常相关小节原文】。请把它们翻译成一个「症状对照」小节，
让用户用口语问问题时，这段内容能被检索命中且自包含地回答。

要求：
1. 直接输出 markdown，以 `## 症状对照（用户会怎么问）` 开头，不要任何解释或代码围栏
2. 3~6 个症状块，每块格式：
   **「用户口语症状」**（对应文档机制：XXX）
   - 为什么：用文档里的原理一句话解释（自包含，不写"见上文"，不写"详见其他文档"）
   - 现场表现：用户在界面/日志里实际看到什么
   - 下一步：一句可操作的确认方法
3. 症状词用现场口语并尽量多样（同义说法可在同一块内并列），块与块之间可区分
4. 只用文档里存在的原因，禁止编造文档没有的机制；文档没覆盖的常见口语症状可加一块
   "文档未覆盖时的通用排查建议"，但要注明
5. 保留文档中的专有名词（如 NO_PATH、blocked_edges、srp_timeout），口语词与之并列

【功能摘要】
{summary}

【异常相关小节原文】
{anomaly}
"""


def extract_sections(text: str):
    """拆 ## 小节，返回 (功能摘要文本, 异常相关小节拼接)。"""
    sections = re.split(r"\n(?=## )", text)
    summary_parts, anomaly_parts = [], []
    for s in sections:
        m = re.match(r"##\s*(.+)", s or "")
        head = (m.group(1).strip() if m else (s[:40] if s else ""))
        if re.match(r"^(问题设定|功能简介|概述|$)", head) or (not m and s.strip()):
            summary_parts.append(s.strip())
        if _ANOMALY_HEAD.search(head):
            anomaly_parts.append(s.strip())
    summary = "\n\n".join(summary_parts)[:_SUMMARY_CHARS]
    if len(summary) < 200:  # 摘要太短就补前文
        summary = text[:_SUMMARY_CHARS]
    anomaly = "\n\n".join(anomaly_parts)[:_ANOMALY_CHARS]
    return summary, anomaly


def strip_fence(s: str) -> str:
    """LLM 有时会把整段包进 ```markdown 围栏，剥掉。"""
    s = s.strip()
    m = re.match(r"^```[a-z]*\n(.*)\n```$", s, re.DOTALL)
    return m.group(1).strip() if m else s


async def symptomize_one(llm, src: Path) -> str:
    text = src.read_text(encoding="utf-8")
    if "## 症状对照" in text:
        print(f"  [SKIP] {src.name} 已含症状对照")
        return ""
    summary, anomaly = extract_sections(text)
    # 异常节缺失 + 文档太短（<800字）= 概念/总览，不值得做症状化，跳过
    if not anomaly and len(text) < 800:
        print(f"  [SKIP] {src.name} 无异常节且篇幅过短")
        return ""
    if not anomaly:
        # 无标准异常节但篇幅够：把全文当 anomaly 输入，让 LLM 也能榨出症状
        anomaly = text[:_ANOMALY_CHARS]
    prompt = _PROMPT.format(summary=summary, anomaly=anomaly)
    out = await llm.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=3000, temperature=0.2)
    return strip_fence(out)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default="", help="逗号分隔的源文件名，缺省=试点 4 篇")
    ap.add_argument("--all", action="store_true", help="全部 topo md")
    ap.add_argument("--dry-run", action="store_true", help="只打印生成结果，不落盘")
    args = ap.parse_args()

    if args.all:
        names = sorted(f.name for f in SRC.glob("*.md")
                       if not f.name.startswith(("usp_dev", "usp_latest", "usp_stable")))
    else:
        names = ([x.strip() for x in args.docs.split(",") if x.strip()]
                 if args.docs else PILOT)

    from ai.core.llm import get_llm_client
    llm = await get_llm_client()

    OUT.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    done = 0
    for name in names:
        src = SRC / name
        if not src.is_file():
            print(f"[MISS] {name}")
            fail += 1
            continue
        print(f"[{done+1}/{len(names)}] {name} ...", flush=True)
        attempts = 0
        symptom = ""
        while attempts < 3 and not symptom:
            attempts += 1
            try:
                symptom = await symptomize_one(llm, src)
            except Exception as e:
                print(f"  [RETRY {attempts}] {type(e).__name__}: {str(e)[:80]}", flush=True)
                await asyncio.sleep(2)
        if not symptom:
            fail += 1
            done += 1
            continue
        done += 1
        if "## 症状对照" not in symptom:
            print("  [WARN] 输出未含症状对照标题，原样保留供检查")
        text = src.read_text(encoding="utf-8")
        merged = (text.rstrip()
                  + "\n\n---\n\n"
                  + "> 本节由症状化管线生成（源：" + name + "），人工抽检后入库。\n\n"
                  + symptom.strip() + "\n")
        if args.dry_run:
            print("=" * 60)
            print(symptom[:2000])
        else:
            (OUT / name).write_text(merged, encoding="utf-8")
            print(f"  [OK] -> {OUT / name}（症状节 {len(symptom)} 字）")
        ok += 1
    print(f"\n[DONE] 成功 {ok} / 失败 {fail}")


if __name__ == "__main__":
    asyncio.run(main())
