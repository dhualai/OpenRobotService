# -*- coding: utf-8 -*-
"""L3 校准/预标：三件套 judge（时间线+检索资料+段末成单信号）。

校准模式（默认）：考卷=真实组人工已标段，对齐人工标签（≥90% 才放权预标）。
预标模式（--all）：全部段（真实+测试、含未标，AI topic 切分）judge
  组合四类预标，输出 l3_judge_all_*.json 供 build_segmentation_tool 注入。
四类组合：
  intent=ticket                    → 直接提单
  consult + resolved=yes           → 直答正确
  consult + resolved=no + 检索yes/partial → 未直答（资料有，回答层没答好）
  consult + resolved=no + 检索no   → 未覆盖（知识库没有）

用法：
  python ai/scripts/dar_l3.py          # 校准：judge + 对齐分析
  python ai/scripts/dar_l3.py --all    # 预标：全段 judge，输出 l3_judge_all_*.json
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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)
os.chdir(_PROJ)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJ, "ai", ".env"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

ENV = os.environ.get("DAR_ENV", "test")
OUT = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}/processed"
SPLIT = os.path.join(OUT, "conversations_split.jsonl")
CLS = os.path.join(OUT, "conversations_classified.jsonl")
_MANUAL_NAME = {"test": "manual_segmentation.json",
                "prod": "manual_segmentation_prod.json"}
MANUAL = rf"C:/Users/PAJ26020/Downloads/{_MANUAL_NAME[ENV]}"
RETRIEVAL = os.path.join(OUT, f"retrieval_check_{_dt.now():%Y%m%d}.json")
JUDGE_OUT = os.path.join(OUT, f"l3_judge_{_dt.now():%Y%m%d}.json")      # 校准结果（按日滚动）
ALL_OUT = os.path.join(OUT, f"l3_judge_all_{_dt.now():%Y%m%d}.json")    # 预标结果
CONCURRENCY = 8


def _latest_judge():
    """取最新已落盘校准文件（滚动校准集：上周的校准继续可比）。"""
    import glob
    files = sorted(glob.glob(os.path.join(OUT, "l3_judge_[0-9]*.json")))
    return files[-1] if files else ""


def pts(s):
    try:
        return _dt.fromisoformat((s or "").split(".")[0])
    except ValueError:
        return None


def build_exam(all_mode=False):
    """校准：真实组人工已标段（lab=人工标签）。
    预标（--all）：全部会话 AI topic 段（lab=人工标签或未标），astart=段首回合索引。
    """
    convs = [json.loads(l) for l in open(SPLIT, encoding="utf-8")]
    cls_all = {j["conversation_id"]: j["cls"] for j in
               (json.loads(l) for l in open(CLS, encoding="utf-8"))}
    man = ({} if not os.path.exists(MANUAL)
           else json.load(open(MANUAL, encoding="utf-8")))
    bounds, labels = man.get("bounds") or {}, man.get("labels") or {}
    legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
    exam = []
    for c in convs:
        cid = str(c["conversation_id"])
        cls = cls_all.get(cid)
        if not cls or len(cls) != len(c["rounds"]):
            continue
        if all_mode:
            manual = sorted({0, *(i for i in range(1, len(c["rounds"]))
                                  if cls[i]["t"] != cls[i - 1]["t"])})
        else:
            if cid not in bounds or c["is_tester"]:
                continue
            manual = sorted({0, *(int(x) for x in bounds[cid]
                                  if 0 <= int(x) < len(c["rounds"]))})
        rounds = c["rounds"]
        lab_map = {int(k): legacy.get(v, v) for k, v in
                   (labels.get(cid) or {}).items() if str(k).isdigit()}
        for tid, s in enumerate(manual):
            e = manual[tid + 1] if tid + 1 < len(manual) else len(rounds)
            if all_mode and not any(cls[i]["q"] for i in range(s, e)):
                continue  # 段内无咨询回合（纯问候/寒暄，如整段只有「你好」）——无问题可判
            lab = lab_map.get(s)
            if not all_mode and (not lab or lab == "未标"):
                continue
            # 时间线（前 12 轮，每轮截断）
            lines = []
            for i in range(s, min(e, s + 12)):
                q_raw = (rounds[i]["q"] or "").replace("\n", " ")
                q = q_raw[:150] + ("…" if len(q_raw) > 150 else "")
                a_raw = next((x for x in rounds[i]["a"] if x.strip()), "") or ""
                a_raw = a_raw.replace("\n", " ")
                a = a_raw[:200] + ("…(截断)" if len(a_raw) > 200 else "")
                lines.append(f"[{(rounds[i]['at'] or '')[5:16]}] 用户：{q} → 助手：{a}")
            # 上一段尾（承接语境，段首问题可能指代上文）
            prev = ""
            if tid > 0:
                p = manual[tid - 1] if tid - 1 >= 0 else 0
                last = rounds[min(e - 1, p + 12) - 1] if e - 1 > p else None
                if last:
                    pq = (last["q"] or "").replace("\n", " ")[:100]
                    prev = f"（上一话题结尾：用户说「{pq}」）\n"
            # 段内成单归属：单创建时间落在 [段首回合, 下一段首) ；尾段放宽 30min
            start = pts(rounds[s]["at"])
            end = pts(rounds[manual[tid + 1]]["at"]) if tid + 1 < len(manual) else (
                (pts(rounds[e - 1]["at"]).timestamp() + 1800) if e > s else None)
            n_ticket = 0
            for t in c.get("tasks") or []:
                tat = pts(t.get("at"))
                if start and tat and tat >= start:
                    if end and (tat.timestamp() <= end if isinstance(end, float)
                                else tat <= end):
                        n_ticket += 1
            exam.append({"cid": cid, "seg": tid, "astart": s,
                         "grp": "测试组" if c["is_tester"] else "真实组",
                         "lab": lab or "未标",
                         "timeline": "\n".join(lines), "prev": prev,
                         "n_ticket": n_ticket})
    return exam


JUDGE_PROMPT = (
    "你在审核客服对话的一个话题段。背景：AGV 调度平台的服务号对话，用户是现场运维/项目人员，"
    "助手是 AI 客服（可查知识库答题，也可帮用户提工单）。\n"
    "知识库检索系统针对该话题问题的返回资料也给你（可能含多片段、图片引用，可能截断）。\n"
    "判断两件事：\n"
    "intent=用户来意，看用户最初几条消息的形态：\n"
    "ticket=反馈腔或委托腔——开口明确要求提单/转人工/报障登记；或消息是产品缺陷反馈/"
    "功能需求（质问系统为什么不行、指出哪里有 bug、要求系统应该怎样）；或描述问题后不等"
    "解答直接催着处理/提单。这类用户不期待被解答，只想把事情报出去。\n"
    "consult=求助腔——用户想知道怎么做/什么原因/帮忙看看，接受 AI 解答。即使咨询后不满意"
    "转了工单，来意仍是 consult（属于咨询未解决）。\n"
    "resolved=该话题的问题是否被实质解决（仅 intent=consult 时有意义），两个条件须同时成立：\n"
    "①助手的回答给出了可执行的步骤或明确的答案（不是只给方向、只反问、只说联系谁），"
    "且用户没有负面反应（重复问同一问题、明显不满、纠缠不休）；用户沉默、确认、感谢、"
    "转入新话题都视为无负面，不要求显式确认；助手答可能被截断显示，不要因显示截断而判未解决。\n"
    "②回答内容能在检索资料中找到支撑（资料里有对应的知识内容）。若检索资料与该话题基本"
    "无关，而回答看起来完整详细，这说明回答来自资料外的通用知识或推测，不可视为解决。\n"
    "以下任一情况 resolved=no：用户负面反应；该话题以提工单收尾；回答只有方向没有答案。\n"
    "faithful=回答内容是否忠于检索资料（防编造）：\n"
    "yes=回答的关键内容能在检索资料中找到支撑；\n"
    "no=回答看起来完整但资料里没有对应内容（来自资料外通用知识或推测）；\n"
    "na=检索资料与该话题基本无关（此时 resolved 依据①单独判断，faithful 填 na）。\n"
    "resolved=yes 且 faithful=no 的情况：回答流利但是编的，resolved 填 no。\n\n"
    "{prev}话题段时间线（每轮：用户说 → 助手答，内容可能截断）：\n{timeline}\n\n"
    "检索资料：\n{retrieval}\n\n"
    "段末信号：{ticket_sig}\n"
    "只输出 JSON：{{\"intent\":\"consult|ticket\",\"resolved\":\"yes|no\","
    "\"faithful\":\"yes|no|na\",\"reason\":\"一句话\"}}"
)


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState, get_diagnosis_platform
    from dar_llm import get_dar_client

    all_mode = "--all" in sys.argv
    out_path = ALL_OUT if all_mode else JUDGE_OUT
    exam = build_exam(all_mode)
    print(f"考卷 {len(exam)} 段（{'全部段·预标' if all_mode else '真实组人工已标·校准'}）")
    platform = await get_diagnosis_platform()
    await platform._ensure_clients()  # 懒加载只在 run 入口触发，直连检索前必须显式初始化
    llm = await get_dar_client()

    if os.path.exists(out_path):
        rows = json.load(open(out_path, encoding="utf-8"))
        print(f"读已落盘 judge 结果（{len(rows)} 条），跳过 LLM")
    else:
        rows = []
        sem = asyncio.Semaphore(CONCURRENCY)
        done = [0]
        t0 = time.time()

        async def one(seg):
            async with sem:
                sig = (f"该段结束前用户提了 {seg['n_ticket']} 张工单" if seg["n_ticket"]
                       else "该段未提工单")
                # 段首问题跑真实检索（三件套之一：检索资料）
                q0 = seg["timeline"].split("用户：", 1)[-1].split(" →", 1)[0]
                st = AgentState(session_id=f"dar_l3_{seg['cid']}_{seg['astart']}",
                                original_query=q0)
                try:
                    ctx = await platform._retrieve_with_context(st.session_id, st)
                except Exception:
                    ctx = ""
                prompt = JUDGE_PROMPT.format(prev=seg["prev"], timeline=seg["timeline"],
                                             retrieval=(ctx or "")[:2500], ticket_sig=sig)
                r = {"cid": seg["cid"], "seg": seg["seg"], "astart": seg["astart"],
                     "grp": seg["grp"], "lab": seg["lab"]}
                try:
                    raw = await llm.complete(prompt=prompt, max_tokens=200, temperature=0,
                                             thinking=False)
                    obj = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
                    r["intent"] = str(obj.get("intent", "?"))
                    r["resolved"] = str(obj.get("resolved", "?"))
                    r["faithful"] = str(obj.get("faithful", "na"))
                    r["reason"] = str(obj.get("reason", ""))[:120]
                    if r["intent"] not in ("consult", "ticket"):
                        r["intent"] = "?"
                    if r["resolved"] not in ("yes", "no"):
                        r["resolved"] = "?"
                    if r["faithful"] not in ("yes", "no", "na"):
                        r["faithful"] = "na"
                except Exception as ex:
                    r["intent"] = r["resolved"] = "error"
                    r["faithful"] = "na"
                    r["reason"] = f"{type(ex).__name__}: {ex}"[:120]
                rows.append(r)
                done[0] += 1
                if done[0] % 40 == 0:
                    print(f"  {done[0]}/{len(exam)}（{time.time()-t0:.0f}s）")

        await asyncio.gather(*(one(s) for s in exam))
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        print(f"judge 完成，{time.time()-t0:.0f}s → {out_path}")

    # ---- 预标模式：组合四类 pre，落盘 + 分布 ----
    if all_mode:
        rv = {}
        if os.path.exists(RETRIEVAL):
            for r in json.load(open(RETRIEVAL, encoding="utf-8")):
                rv[(str(r["cid"]), r.get("astart", r.get("seg")))] = r.get("verdict")
        else:
            print(f"（{RETRIEVAL} 不存在，consult+no 段统一保守预标未直答）")
        for r in rows:
            if r["intent"] == "ticket":
                r["pre"] = "直接提单"
            elif r["intent"] == "consult" and r["resolved"] == "yes":
                # 忠实性前置：resolved=yes 但回答无资料支撑（编造）→ 未直答，不得直答分
                r["pre"] = "直答正确" if r.get("faithful") != "no" else "未直答"
            elif rv.get((r["cid"], r["astart"])) == "no":
                r["pre"] = "未覆盖"
            else:
                r["pre"] = "未直答"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        print(f"\n== 预标分布 ==")
        for grp in ("真实组", "测试组"):
            sub = [r for r in rows if r.get("grp") == grp]
            if not sub:
                continue
            c = Counter(r["pre"] for r in sub)
            print(f"  {grp}（{len(sub)} 段）：" +
                  "｜".join(f"{k} {v}" for k, v in c.most_common()))
        agree = Counter()
        for r in rows:
            if r["lab"] not in ("未标",) and r.get("pre"):
                agree[(r["lab"], r["pre"] == r["lab"])] += 1
        n_lab = sum(agree.values())
        if n_lab:
            n_hit = sum(v for (_, hit), v in agree.items() if hit)
            print(f"  （参考）已标段预标与人工一致 {n_hit}/{n_lab} = {n_hit/n_lab*100:.0f}%")
        return

    # ---- 三分类对齐（judge 直接输出 vs 人工） ----
    def tri(r):
        if r["intent"] == "ticket":
            return "直接提单"
        if r["intent"] == "consult" and r["resolved"] == "yes":
            # 编造（faithful=no）归未直答：回答层问题，不给直答分
            return "直答正确" if r.get("faithful") != "no" else "未直答/未覆盖"
        return "未直答/未覆盖"  # consult+no，待检索 verdict 分流

    print("\n== judge 三分类 × 人工标签 ==")
    labs = ["直接提单", "直答正确", "未直答", "未覆盖"]
    m3 = Counter((r["lab"], tri(r)) for r in rows)
    for lab in labs:
        parts = [f"{k2} {m3.get((lab, k2), 0)}" for k2 in ("直接提单", "直答正确", "未直答/未覆盖")]
        print(f"  人工{lab:<5}：" + "｜".join(parts))

    # ---- 四类组合（judge × 检索 verdict） ----
    if os.path.exists(RETRIEVAL):
        rv = {(str(r["cid"]), r.get("astart", r.get("seg"))): r.get("verdict") for r in
              json.load(open(RETRIEVAL, encoding="utf-8"))}
        def quad(r):
            t = tri(r)
            if t != "未直答/未覆盖":
                return t
            v = rv.get((r["cid"], r.get("astart", r["seg"])))
            if v == "no":
                return "未覆盖"
            return "未直答"  # yes/partial/error 兜底为未直答
        print("\n== 四类组合 × 人工标签（混淆矩阵） ==")
        mq = Counter((r["lab"], quad(r)) for r in rows)
        print(f"  {'':>10} " + "".join(f"{k:>7}" for k in labs))
        for lab in labs:
            print(f"  人工{lab:<6}" + "".join(f"{mq.get((lab, k2), 0):>7}" for k2 in labs))
        n_hit = sum(mq.get((lab, lab), 0) for lab in labs)
        n = sum(mq.values())
        print(f"  对角线合计 {n_hit}/{n} = {n_hit/n*100:.1f}%")
        print("\n错位明细（前 30）：")
        k = 0
        for r in rows:
            q4 = quad(r)
            if q4 != r["lab"] and k < 30:
                print(f"  [{r['cid']}#{r['seg']}] 人工={r['lab']} L3={q4}"
                      f" 检索={rv.get((r['cid'], r.get('astart', r['seg'])))}｜{r.get('reason','')[:60]}")
                k += 1
    else:
        print(f"\n（{RETRIEVAL} 不存在，四类组合待检索完成后重跑）")


if __name__ == "__main__":
    asyncio.run(main())
