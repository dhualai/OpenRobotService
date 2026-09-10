# -*- coding: utf-8 -*-
"""全部咨询段逐条过真实检索，LLM 判断知识库现有内容能否支撑直答（归因三分类）。

用途：区分「没检索到/知识库没有」和「检索到了但没答好」——
     人工标签 × 检索判定交叉，定位问题在覆盖层还是回答层。
段切分：有人工 bounds 用人工边界（lab=人工标签），否则用 AI topic 切分
     （lab=未标）——测试组/未标会话全覆盖。
增量：当日输出文件已存在的段（cid+段首问题匹配）复用判定，只补跑新段。
断点续跑：判定成功的段逐条追加 .jsonl（异常段不落，重跑自动重试），
     LLM 断网/中途杀进程后重跑只补未判段；跑完仍写全量 .json 快照。
每行带 astart（段首回合索引）= 预标注入的锚定键。

用法：
  python ai/scripts/dar_retrieval_check.py
"""
import asyncio
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime as _dt

sys.stdout.reconfigure(encoding="utf-8")  # 不换 wrapper 对象：pytest 捕获下替换会炸
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)
os.chdir(_PROJ)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJ, "ai", ".env"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# 环境随 dar_weekly --env 走（subprocess 继承 DAR_ENV）；单独跑缺省 test
ENV = os.environ.get("DAR_ENV", "test")
# 检索源：prod=连生产 qdrant 只读重放（隧道+切指针，见 dar_qdrant.py），local=本地知识库。
# 检索源（规定，用户 0910 定调）：一律走服务器测试环境（与 dar_l3 同源同规则）；
# DAR_QDRANT=local 仅作应急，不作缺省。对话数据由 DAR_ENV 决定。
QDRANT = os.environ.get("DAR_QDRANT", "test")
OUT = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}/processed"
SPLIT = os.path.join(OUT, "conversations_split.jsonl")
CLS = os.path.join(OUT, "conversations_classified.jsonl")
_MANUAL_NAME = {"test": "manual_segmentation.json",
                "prod": "manual_segmentation_prod.json"}
MANUAL = rf"C:/Users/PAJ26020/Downloads/{_MANUAL_NAME[ENV]}"
CONCURRENCY = 8

# chunk 首行：『{emoji路别} N（标题）：』；title 可缺（FAQ/翻译表等无名块）
_HEAD = re.compile(r"^(?P<route>[^\d（]+?)\s*(?P<idx>\d+)?\s*(?:（(?P<title>[^）]*)）)?：\s*$")


def parse_retrieval_chunks(ctx, max_chunks=6, text_cap=200):
    """检索 ctx 拼接文本 → [{route,title,text}]，供标注工具折叠展示。

    块边界=行首 `---`（pipeline._retrieve_with_context 每 chunk 自带前后 ---）；
    首行不匹配编号格式的块（如 🚗 提示）route 取冒号前原文。纯函数，单测覆盖。
    """
    chunks = []
    for part in re.split(r"(?m)^---\s*$", ctx or ""):
        lines = [l for l in part.splitlines() if l.strip()]
        if not lines:
            continue
        m = _HEAD.match(lines[0].strip())
        if m:
            route, title = m.group("route").strip(), (m.group("title") or "").strip()
            body = lines[1:]
        else:
            head, sep, rest = lines[0].strip().partition("：")
            route, title = head, ""
            body = ([rest.strip()] if sep and rest.strip() else []) + lines[1:]
        text = " ".join(l.strip() for l in body)[:text_cap]
        chunks.append({"route": route, "title": title, "text": text})
        if len(chunks) >= max_chunks:
            break
    return chunks


def build_rows():
    """全部咨询段（真实组+测试组都跑，grp 区分）：每段取段首问题+标签。

    人工 bounds 优先；缺失会话用 AI topic 切分 fallback。astart=段首回合索引。
    """
    convs = [json.loads(l) for l in open(SPLIT, encoding="utf-8")]
    cls_all = {j["conversation_id"]: j["cls"] for j in
               (json.loads(l) for l in open(CLS, encoding="utf-8"))}
    man = ({ } if not os.path.exists(MANUAL)
           else json.load(open(MANUAL, encoding="utf-8")))
    bounds, labels = man.get("bounds") or {}, man.get("labels") or {}
    legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
    rows = []
    for c in convs:
        cid = str(c["conversation_id"])
        grp = "测试组" if c["is_tester"] else "真实组"
        # 测试组=自测流量（0909 起默认不判，占 55% 纯烧时间）；
        # DAR_INCLUDE_TEST=1 开回（周报测试组交叉块要有数据时）
        if grp == "测试组" and os.environ.get("DAR_INCLUDE_TEST") != "1":
            continue
        rounds = c["rounds"]
        cls = cls_all.get(cid)
        if not cls or len(cls) != len(rounds):
            continue
        lab_map = {int(k): legacy.get(v, v) for k, v in
                   (labels.get(cid) or {}).items() if str(k).isdigit()}
        if cid in bounds:
            manual = sorted({0, *(int(x) for x in bounds[cid] if 0 <= int(x) < len(rounds))})
            lab_of = lambda s: lab_map.get(s, "未标")
        else:
            # 段根与标注工具/dar_l3 一致：topic 变化切段（t 布尔不可靠且与
            # 标注工具前端不同源——astart 对不上会导致预标注入 miss）
            manual = sorted({0, *(i for i in range(1, len(rounds))
                                  if cls[i]["topic"] != cls[i - 1]["topic"])})
            lab_of = lambda s: "未标"
        for tid, s in enumerate(manual):
            e = manual[tid + 1] if tid + 1 < len(manual) else len(rounds)
            q_idx = [i for i in range(s, e) if cls[i]["q"]
                     and any(a.strip() for a in rounds[i]["a"])]
            if not q_idx:
                continue
            rows.append({"cid": cid, "seg": tid, "astart": s, "grp": grp,
                         "lab": lab_of(s),
                         "time": rounds[q_idx[0]]["at"][:16],
                         "q": (rounds[q_idx[0]]["q"] or "").strip()})
    return rows


JUDGE_PROMPT = (
    "你是知识库审校员。给你一个用户问题，和知识库检索系统针对它返回的资料"
    "（可能包含多个片段和图片引用）。\n\n"
    "用户问题：{q}\n\n检索资料：\n{ctx}\n\n"
    "请判断：仅依据上面的检索资料，能否直接回答该用户问题（不借助资料外的知识、"
    "不编造）？\n"
    "yes = 资料里就有该问题的答案内容，足以直接回答；\n"
    "partial = 资料与问题相关，但只覆盖一部分，不足以完整回答；\n"
    "no = 资料与问题基本不相关，或不含答案内容。\n"
    "只输出 JSON：{{\"verdict\":\"yes|partial|no\",\"reason\":\"一句话\"}}"
)


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState, get_diagnosis_platform
    from dar_llm import get_dar_client

    if QDRANT == "local":  # 指针残留自愈+校验：否则全轮静默空检索（0910 实锤，见 dar_qdrant）
        from dar_qdrant import heal_local_pointers
        miss = heal_local_pointers()
        if miss:
            print("!! 本地指针指向的集合在本地库不存在：" + "；".join(f"{d}={v}" for d, v in miss)
                  + "\n!! 常见原因：远程跑被中断，指针残留远程值未恢复。此状态下检索全空"
                  "且整轮不报错，判定失真——先修指针再跑。")
            sys.exit(2)

    rows = build_rows()
    path = os.path.join(OUT, f"retrieval_check_{_dt.now():%Y%m%d}.json")
    jpath = path[:-5] + ".jsonl"  # 断点续跑：逐段追加；中断后重跑只补未判段
    # 增量：jsonl（逐段追加，最新）+ json（上次快照）里已判过的段（cid+段首问题
    # 匹配）复用判定，补 astart；jsonl 后读覆盖 json 的旧结果
    done_keys = {}
    for src in (path, jpath):
        if not os.path.exists(src):
            continue
        items = ([json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
                 if src.endswith(".jsonl") else json.load(open(src, encoding="utf-8")))
        for old in items:
            if old.get("verdict") in ("yes", "partial", "no"):
                done_keys[(str(old["cid"]), (old.get("q") or "")[:80])] = old
    for r in rows:  # 复用判定拷回（否则落盘行缺 verdict）；jsonl 后读覆盖 json
        old = done_keys.get((r["cid"], r["q"][:80]))
        if old and "verdict" not in r:
            for k in ("verdict", "reason", "retrieval", "chunks"):
                if old.get(k) is not None:
                    r[k] = old[k]
    n_hit = sum(1 for r in rows if r.get("verdict") in ("yes", "partial", "no"))
    n_err = sum(1 for r in rows if r.get("verdict") == "error")
    if n_hit or n_err:
        print(f"增量：{n_hit}/{len(rows)} 段复用已判结果，补跑 {len(rows) - n_hit} 段")
    todo = [r for r in rows if r.get("verdict") not in ("yes", "partial", "no")]
    print(f"待验证 {len(todo)} 条咨询段（真实组；测试组默认不判，DAR_INCLUDE_TEST=1 开回）")
    if todo:
        platform = await get_diagnosis_platform()
        await platform._ensure_clients()  # 懒加载只在 run 入口触发，直连检索前必须显式初始化
        llm = await get_dar_client()

        sem = asyncio.Semaphore(CONCURRENCY)
        done = [0]
        t0 = time.time()
        lock = asyncio.Lock()

        async def one(r):
            async with sem:
                try:
                    state = AgentState(session_id=f"dar_chk_{r['cid']}_{r['astart']}",
                                       original_query=r["q"])
                    ctx = await platform._retrieve_with_context(state.session_id, state)
                    r["retrieval"] = (ctx or "")[:400]
                    r["chunks"] = parse_retrieval_chunks(ctx)
                    # 资料给全（与线上一致）：ctx 已是装配结果（每块 ≤1500 字、最多 6 块、
                    # 整串不截断）；再砍一刀会让判定看到的资料比回答模型少，偏向 no
                    prompt = JUDGE_PROMPT.format(q=r["q"][:300], ctx=(ctx or ""))
                    raw = await llm.complete(prompt=prompt, max_tokens=200, temperature=0,
                                             thinking=False)
                    obj = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
                    r["verdict"] = str(obj.get("verdict", "?")).lower()
                    r["reason"] = str(obj.get("reason", ""))[:120]
                    if r["verdict"] not in ("yes", "partial", "no"):
                        r["verdict"] = "?"
                except Exception as e:
                    r["verdict"] = "error"
                    r["reason"] = f"{type(e).__name__}: {e}"[:120]
                async with lock:
                    # 成功/格式异常即落 jsonl（断点续跑）；error 不落，重跑重试
                    if r["verdict"] in ("yes", "partial", "no", "?"):
                        with open(jpath, "a", encoding="utf-8") as fh:
                            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                    done[0] += 1
                    if done[0] % 20 == 0:
                        print(f"  {done[0]}/{len(todo)}（{time.time()-t0:.0f}s）")

        await asyncio.gather(*(one(r) for r in todo))
        n_err = sum(1 for r in todo if r.get("verdict") == "error")
        print(f"\n补跑完成 {len(todo)} 条（异常 {n_err} 条，重跑自动重试），"
              f"{time.time()-t0:.0f}s")

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print(f"明细: {path}（{len(rows)} 条）\n")
    cross = Counter((r["grp"], r["lab"], r.get("verdict")) for r in rows)
    labs = ["直答正确", "未直答", "未覆盖", "建议转单", "直接提单", "未标"]
    for grp in ("真实组", "测试组"):
        print(f"== {grp}：人工标签 × 检索判定（yes=资料有答案/partial=部分/no=基本不相关） ==")
        for lab in labs:
            c = {v: cross.get((grp, lab, v), 0) for v in ("yes", "partial", "no", "?", "error")}
            tot = sum(c.values())
            if tot:
                print(f"  {lab:<5}（{tot:>3}）：yes {c['yes']:>3}｜partial {c['partial']:>3}｜"
                      f"no {c['no']:>3}｜异常 {c['?'] + c['error']}")


if __name__ == "__main__":
    # 服务器检索源必须在首次 import pipeline 前切好 env/指针（进程级，退出自动恢复）
    if QDRANT in ("prod", "test"):
        from contextlib import ExitStack

        from dar_qdrant import SSH_HOST, SSH_PORT, remote_qdrant
        with ExitStack() as st:
            try:  # 隧道/指针拉不到=起跑前明确退出，不烧 LLM
                ptr = st.enter_context(remote_qdrant(QDRANT))
            except Exception as ex:
                sys.exit(f"服务器检索源不可用（{QDRANT}）：{type(ex).__name__}: {ex}\n"
                         f"  → 检查免密 ssh {SSH_HOST}:{SSH_PORT}；"
                         "或 DAR_QDRANT=local 用本地知识库跑")
            print(f"检索源={QDRANT} 服务器 qdrant（指针: {ptr}）")
            asyncio.run(main())
    else:
        print("检索源=本地知识库")
        asyncio.run(main())
