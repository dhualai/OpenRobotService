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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)
os.chdir(_PROJ)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJ, "ai", ".env"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# 环境随 dar_weekly --env 走（subprocess 继承 DAR_ENV）；单独跑缺省 test
ENV = os.environ.get("DAR_ENV", "test")
# 检索源：prod/test=连服务器 qdrant（隧道+切指针，见 dar_qdrant.py；两环境指针当前
# 指向同一批集合），local=本地知识库。缺省跟随数据环境——与 dar_retrieval_check
# 同规则，保证 L3 的检索资料与「检索判定/未覆盖」同源（0909 实锤：本地 KB 是
# 0901/0824/0903 旧快照、服务器是 0904 集合，industry 差两周 → faithful/resolved
# 判据与四类组合的「未覆盖」来自不同知识库）
QDRANT = os.environ.get("DAR_QDRANT", "prod" if ENV == "prod" else "local")
OUT = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}/processed"
SPLIT = os.path.join(OUT, "conversations_split.jsonl")
CLS = os.path.join(OUT, "conversations_classified.jsonl")
_MANUAL_NAME = {"test": "manual_segmentation.json",
                "prod": "manual_segmentation_prod.json"}
MANUAL = rf"C:/Users/PAJ26020/Downloads/{_MANUAL_NAME[ENV]}"
RETRIEVAL = os.path.join(OUT, f"retrieval_check_{_dt.now():%Y%m%d}.json")
JUDGE_OUT = os.path.join(OUT, f"l3_judge_{_dt.now():%Y%m%d}.json")      # 校准结果（按日滚动）
ALL_OUT = os.path.join(OUT, f"l3_judge_all_{_dt.now():%Y%m%d}.json")    # 预标结果
# 并发：网关限流/本地嵌入式 qdrant 扛不住时可 DAR_L3_CONC=4 降并发
try:
    CONCURRENCY = max(1, int(os.environ.get("DAR_L3_CONC") or 8))
except ValueError:
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
        # 测试组=自测流量，预标/判定只跑真实组（0909：占 55% 白烧 LLM）；
        # DAR_INCLUDE_TEST=1 可开回（要看测试组交叉对比时）
        if c["is_tester"] and os.environ.get("DAR_INCLUDE_TEST") != "1":
            continue
        if all_mode:
            # 段根与标注工具前端一致（0909 实锤两套不同源：标注工具按 topic 变化
            # 分组、旧代码用 t 布尔翻转——已标会话预标 astart 对不上=全 miss）。
            # 有人工边界用人工（已标会话对齐人工标签），否则 topic 变化切段。
            if cid in bounds:
                manual = sorted({0, *(int(x) for x in bounds[cid]
                                      if 0 <= int(x) < len(c["rounds"]))})
            else:
                manual = sorted({0, *(i for i in range(1, len(c["rounds"]))
                                      if cls[i]["topic"] != cls[i - 1]["topic"])})
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
    print(f"考卷 {len(exam)} 段（{'真实组全部段·预标' if all_mode else '真实组人工已标·校准'}；"
          "测试组默认不判，DAR_INCLUDE_TEST=1 开回）")
    platform = await get_diagnosis_platform()
    await platform._ensure_clients()  # 懒加载只在 run 入口触发，直连检索前必须显式初始化
    llm = await get_dar_client()

    # 增量：jsonl（逐段追加，最新）+ json 快照（上次全量）双源收已判段，jsonl
    # 覆盖 json。不再「产物存在就整段跳过 LLM」——段根/模型变更后重跑即自动
    # 补判差异段（旧行为必须手工删文件才重判，0909 两次踩坑）
    jpath = out_path[:-5] + ".jsonl"
    done_keys = {}
    for src in (out_path, jpath):
        if not os.path.exists(src):
            continue
        items = ([json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
                 if src.endswith(".jsonl") else json.load(open(src, encoding="utf-8")))
        for old in items:
            if old.get("intent") in (None, "error") or old.get("astart") is None:
                continue  # error 不落，重跑自动补
            done_keys[(str(old["cid"]), int(old["astart"]))] = old
    rows = list(done_keys.values())
    todo = [s for s in exam if (str(s["cid"]), int(s["astart"])) not in done_keys]
    # 切模型后旧判定仍按 cid+astart 复用（指标会混口径）——显式提示，不静默
    n_other = sum(1 for r in rows if r.get("model") != llm.model)
    print(f"增量：复用已判 {len(done_keys)} 段，补跑 {len(todo)} 段"
          + (f"；其中 {n_other} 段未记/非当前模型（当前 {llm.model}）——"
             "要统一口径须删对应 .json/.jsonl 重跑" if n_other else ""))

    if todo:
        # LLM 探活：模型名过期/网关不可用时快速失败，别把 419 段全烧成 error
        for attempt in range(3):
            try:
                await llm.complete(prompt="回复：OK", max_tokens=5, temperature=0,
                                   thinking=False)
                print("LLM 探活通过")
                break
            except Exception as ex:
                if attempt == 2:
                    sys.exit(f"LLM 不可用（当前模型 {getattr(llm, 'model', '?')}，"
                             f"过期或网关问题？可 DAR_MODEL=deepseek-v4-flash）："
                             f"{type(ex).__name__}: {ex}")
                await asyncio.sleep(5)
        # 预热：本地嵌入式 qdrant 冷启动加载 >5s 会踩 5s 操作超时 + 30s
        # 快速失败窗口，并发首轮检索全空——先单发一次把库打开
        try:
            await platform._retrieve_with_context(
                "dar_l3_warmup", AgentState(session_id="dar_l3_warmup",
                                            original_query="AGV 上线部署"))
            print("qdrant 预热完成")
        except Exception as ex:
            print(f"qdrant 预热失败（继续，段内会重试）：{type(ex).__name__}: {ex}")

    sem = asyncio.Semaphore(CONCURRENCY)
    done = [0]
    total = [len(todo)]
    t0 = time.time()
    lock = asyncio.Lock()
    err_keys = set()
    # 远程检索源自愈：ssh 隧道掉了就重连（local 模式无隧道，保持 None）
    reconnect = None
    if QDRANT in ("prod", "test"):
        from dar_qdrant import ensure_tunnel
        reconnect = ensure_tunnel

    async def retrieve_ctx(seg, q0):
        """段首问题检索资料。qdrant 冷启动加载 >5s 会触发操作超时 + 30s
        快速失败窗口，窗口内检索全空（0909 实锤：47 段拿空资料判成未直答，
        系统性偏保守）——检测到不可用等冷却后重试，仍不可用抛错让该段记
        error 不落盘（重跑自动补）。"""
        st = AgentState(session_id=f"dar_l3_{seg['cid']}_{seg['astart']}",
                        original_query=q0)
        for _ in range(5):
            if not getattr(platform._retriever, "is_qdrant_unavailable", False):
                try:
                    ctx = await platform._retrieve_with_context(st.session_id, st)
                except Exception:
                    ctx = ""
                if ctx or not getattr(platform._retriever,
                                      "is_qdrant_unavailable", False):
                    return ctx or ""
            if reconnect:  # 隧道断了先重连再等冷却，避免整轮全空
                try:
                    reconnect()
                except Exception as ex:
                    print(f"  [隧道重连失败] {type(ex).__name__}: {ex}")
            await asyncio.sleep(10)  # 等快速失败冷却（30s）后重试
        raise RuntimeError("qdrant 持续不可用（快速失败窗口）")

    async def one(seg, sem_=None):
        async with (sem_ or sem):
            sig = (f"该段结束前用户提了 {seg['n_ticket']} 张工单" if seg["n_ticket"]
                   else "该段未提工单")
            # 段首问题跑真实检索（三件套之一：检索资料）
            q0 = seg["timeline"].split("用户：", 1)[-1].split(" →", 1)[0]
            r = {"cid": seg["cid"], "seg": seg["seg"], "astart": seg["astart"],
                 "grp": seg["grp"], "lab": seg["lab"], "model": llm.model}
            try:
                ctx = await retrieve_ctx(seg, q0)
                prompt = JUDGE_PROMPT.format(prev=seg["prev"], timeline=seg["timeline"],
                                             retrieval=(ctx or "")[:2500], ticket_sig=sig)
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
            async with lock:
                # 判定落 jsonl（增量/断点续跑）；error 不落，重跑自动重试
                if r["intent"] == "error":
                    err_keys.add((str(seg["cid"]), int(seg["astart"])))
                else:
                    with open(jpath, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                done[0] += 1
                if done[0] % 10 == 0 or done[0] == total[0]:
                    print(f"  {done[0]}/{total[0]}（{time.time()-t0:.0f}s）")

    if todo:
        await asyncio.gather(*(one(s) for s in todo))
        if err_keys:
            # 异常段降并发补跑一轮：网关瞬断/超时不拖到下次手工重跑
            retry = [s for s in todo if (str(s["cid"]), int(s["astart"])) in err_keys]
            print(f"异常 {len(retry)} 段，降并发补跑一轮…")
            rows[:] = [r for r in rows
                       if (str(r["cid"]), int(r["astart"])) not in err_keys]
            err_keys.clear()
            done[0], total[0] = 0, len(retry)
            rsem = asyncio.Semaphore(2)
            await asyncio.gather(*(one(s, rsem) for s in retry))
            # 补跑仍失败的也不进 json 快照：error 不是判定，落盘会被预标组合
            # 当成「未直答」注入标注工具、并拉低 L3 指标（测试实锤）
            rows[:] = [r for r in rows
                       if (str(r["cid"]), int(r["astart"])) not in err_keys]
        print(f"judge 完成，{time.time()-t0:.0f}s → {out_path}"
              + (f"（仍异常 {len(err_keys)} 段，重跑自动补）" if err_keys else ""))
    if rows:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)

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
    # 服务器检索源必须在首次 import pipeline 前切好 env/指针（进程级，退出自动恢复）
    if QDRANT in ("prod", "test"):
        from contextlib import ExitStack

        from dar_qdrant import SSH_HOST, SSH_PORT, remote_qdrant
        with ExitStack() as st:
            try:  # 隧道/指针拉不到=起跑前明确退出，不烧 LLM 也不半途炸
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
