# -*- coding: utf-8 -*-
"""生成话题切分审核工具（单 HTML，浏览器直接打开）。

输入：processed/conversations_split.jsonl + conversations_classified.jsonl
     + Downloads/manual_segmentation*.json（人工边界，可选）
     + l3_judge_all_*.json（AI 预标，可选） + retrieval_check_*.json（检索判定，可选）
输出：processed/segmentation_tool.html（数据内嵌，无依赖离线可用）

两轮用法（0910 起：先人工定边界、后按人工边界判定——judge 与检索重放的
输入就是人工认可的话题段，标注轮预标全部有效）：
  --bounds-only  切题轮：不注入预标/检索，只核对修正边界，「保存到工作台」
                 → export_dar/{env}/manual_segmentation.json（自动重算 L1）
  （缺省）       标注轮：注入 AI 四类预标 + 检索判定 + judge 理由，段头一键采纳；
                 数字键 1-6 选标签，「保存到工作台」收标签
已导出过人工边界的会话，初始切分即人工边界（与 localStorage 双重一致）。
预标按段首索引锚定，边界改动后该段预标不显示（防错位）。
进度存 localStorage（换浏览器/清缓存会丢，审完及时导出）。
"""
import argparse
import glob
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dar_segs  # noqa: E402  段首统一口径：bounds 优先 + 老窗口续聊追加（0920）

ENV = os.environ.get("DAR_ENV", "test")
# 附件图片直链前缀（与 dar_studio seg_page 同源：prod=生产站点、test=测试站点）
SITE_BASE = ("https://usp.ep-zl.com/p" if ENV == "prod"
             else "http://125.122.97.107/t")
IMG_BASE = SITE_BASE + "/api/call/files/"
OUT = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}/processed"
SPLIT = os.path.join(OUT, "conversations_split.jsonl")
CLS = os.path.join(OUT, "conversations_classified.jsonl")
TPL = os.path.join(HERE, "segmentation_tool.template.html")
# 人工切分/标注：随数据集放 export_dar/{env}/（0910-6 迁出 Downloads；
# 工具「保存到工作台」经 dar_studio /api/save_manual 直写这里）
MANUAL = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}/manual_segmentation.json"

WINDOW = timedelta(minutes=30)
A_MAX = 3000  # AI 回答注入上限（审核看话题够用）


def latest(pattern):
    files = sorted(glob.glob(os.path.join(OUT, pattern)))
    return files[-1] if files else ""


def load_pre():
    """AI 预标 + 检索 chunks {cid: {ai段首索引: {pre, rv, reason, chunks}}}。
    l3 预标缺席时仍注入 chunks-only 行（检索审核不依赖 l3）。"""
    j_path, r_path = latest("l3_judge_all_*.json"), latest("retrieval_check_*.json")
    j_rows = json.load(open(j_path, encoding="utf-8")) if j_path else []
    chk = {}
    if r_path:
        for r in json.load(open(r_path, encoding="utf-8")):
            chk[(str(r["cid"]), r.get("astart", r.get("seg")))] = {
                "rv": r.get("verdict", ""), "chunks": r.get("chunks") or []}
    pre = {}
    for r in j_rows:
        astart = r.get("astart")
        if astart is None:
            continue
        c = chk.get((str(r["cid"]), astart), {})
        pre.setdefault(str(r["cid"]), {})[astart] = {
            "pre": r.get("pre", ""), "rv": c.get("rv", ""),
            "reason": (r.get("reason") or "")[:80], "chunks": c.get("chunks", []),
        }
    for (cid, astart), c in chk.items():
        pre.setdefault(cid, {}).setdefault(
            astart, {"pre": "", "rv": c["rv"], "reason": "", "chunks": c["chunks"]})
    src = []
    if j_path:
        src.append(f"预标 {j_path}")
    if r_path:
        src.append(f"检索 {r_path}")
    print(f"注入：{'｜'.join(src) or '（无预标无检索判定）'}（{len(pre)} 会话）")
    return pre


def ts(s):
    try:
        return datetime.fromisoformat((s or "").split(".")[0]).timestamp() * 1000
    except ValueError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bounds-only", action="store_true",
                    help="切题轮：不注入 AI 预标/检索判定（边界定稿后再判定）")
    args = ap.parse_args()

    convs = [json.loads(l) for l in open(SPLIT, encoding="utf-8")]
    cls_all = {j["conversation_id"]: j["cls"] for j in
               (json.loads(l) for l in open(CLS, encoding="utf-8"))}
    man_bounds, man_labels, man_frozen = {}, {}, {}
    if os.path.exists(MANUAL):
        _man = json.load(open(MANUAL, encoding="utf-8"))
        man_bounds = _man.get("bounds") or {}
        man_labels = _man.get("labels") or {}
        man_frozen = _man.get("frozen_len") or {}
    pre = {} if args.bounds_only else load_pre()
    if args.bounds_only:
        print("切题轮：不注入预标/检索（先人工定边界，判定在边界定稿后跑）")
    # 标注轮会话筛选（0920）：与漏斗「未标注」同口径——只列含待标注段的会话，
    # 不再全量塞 7/8 月已判定的老会话淹没用。待标注段=无人工标签∧无 L3 预标
    # ∧段内有咨询（非寒暄）∧段内问题不在「猜你想问」推荐池（元筛选层，旁支）
    suggested_pool = set()
    if not args.bounds_only:
        sug_p = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                             "frontend", "src", "shared", "data", "suggestedQuestions.ts")
        try:
            suggested_pool = {m.group(1).strip()
                              for m in re.finditer(r"'([^']+)'", open(sug_p, encoding="utf-8").read())}
        except OSError:
            print("猜你想问池缺失（suggestedQuestions.ts），元筛选跳过")

    def _has_pending_seg(cid, rounds, cls):
        """该会话是否含待标注段（漏斗 unprocessed 的会话级判据，bounds/预标同口径）。"""
        if cid in man_bounds:
            starts = dar_segs.effective_starts(rounds, cid, man_bounds, man_labels,
                                               pre_starts={int(x) for x in (pre.get(cid) or {})
                                                           if str(x).isdigit() or isinstance(x, int)},
                                               frozen_len=man_frozen)
        else:
            starts = [0] + [i for i in range(1, len(cls))
                            if cls[i].get("topic", 0) != cls[i - 1].get("topic", 0)]
        for tid, s in enumerate(starts):
            e = starts[tid + 1] if tid + 1 < len(starts) else len(rounds)
            if (man_labels.get(cid) or {}).get(str(s)):
                continue
            # 0929 修复：不再按「有 AI 预标」过滤——向导第 5 步「回车采纳预标」
            # 的对象就是预标段（有预标未人工标 = 待复核）；0920 对齐漏斗时误加
            # 的这条把复核职能砍没了（l3 全量预标后工具直接空白，用户实锤）。
            # 待标注 = 无人工标签的段（老会话已标段照旧排除）。
            # 0920 口径（与漏斗 _seg_rows 同）：段内须有「提问且 AI 有回答」的
            # 回合——纯寒暄、AI 未回答/回答全空的服务异常段都不可标注
            if not any(cls[i].get("q") and any(a.strip() for a in (rounds[i].get("a") or []))
                       for i in range(s, e)):
                continue
            # 猜你想问：仅段内**全部**咨询回合都命中推荐池才判 suggested——
            # 多轮段碰巧含一条推荐问题不再整段旁支（混合段不过滤，用户拍板）
            consult = [(rounds[i].get("q") or "").strip() for i in range(s, e)
                       if cls[i].get("q") and (rounds[i].get("q") or "").strip()]
            if consult and all(q in suggested_pool for q in consult):
                continue
            return True
        return False

    out = []
    n_man = n_llm_multi = n_noise = n_done = 0
    for c in convs:
        rounds = c["rounds"]
        # 切题轮：单回合无切分余地，跳过；标注轮保留——单问单答也要打标签，
        # 否则漏斗「未标注」里的一问一答会话在工具里永远看不到（0920 走查实锤：
        # 漏斗十多个未标注=14 个单回合会话，工具里一个都没有）
        if args.bounds_only and len(rounds) < 2:
            continue
        cid = str(c["conversation_id"])
        cls = cls_all.get(cid)
        if not cls or len(cls) != len(rounds):
            cls = [{"q": True, "t": False, "topic": 0} for _ in rounds]
        # 全程无咨询且无工单（纯寒暄）：无话题可切、无标签可打，不进工具
        # （带工单的保留——纯提单会话是「直接提单」标的标的；指标层本就跳过无咨询段）
        if not any(k["q"] for k in cls) and not (c.get("tasks") or []):
            n_noise += 1
            continue
        # 标注轮：无待标注段的会话不进列表（已全部判定，07/08 月老会话的来源）；
        # tester 会话同漏斗口径排除（元筛选层，7/8 月测试流量的大头）
        if not args.bounds_only and (c.get("is_tester") or not _has_pending_seg(cid, rounds, cls)):
            n_done += 1
            continue
        if len({k.get("topic", 0) for k in cls}) >= 2:
            n_llm_multi += 1
        task_ts = [ts(t["at"]) for t in c.get("tasks") or []]
        rj = []
        for i, r in enumerate(rounds):
            rt = ts(r["at"])
            rj.append({
                "q": (r["q"] or "")[:500],
                "a": "\n".join(r["a"])[:A_MAX],
                "at": (r["at"] or "")[:16],
                "ts": rt,
                "cq": cls[i]["q"],
                "t": cls[i].get("topic", 0),
                "tk": any(rt <= tt <= rt + WINDOW.total_seconds() * 1000
                          for tt in task_ts),
                # 附件原图（0920：走查页有图、标注工具没有——看图标注是硬需求）。
                # 直链=站点前缀+object_path（与 dar_studio._img_html 同源），大图/gif
                # 不自动加载，模板里占位点击
                "fs": [{"p": IMG_BASE + (f.get("object_path") or ""),
                        "n": f.get("filename") or "", "s": int(f.get("size") or 0)}
                       for f in (r.get("files") or [])],
            })
        # 人工边界覆盖初始切分：没导出过边界的会话仍按 LLM topic 展示。
        # 段首统一展开（0920）：末段已判定时老窗口续聊追加新段——走查工具里
        # 能看到并保存追加段首，否则每次保存会把漏斗里的追加段洗回去。
        # 预标段首（label 模式）作为「末段已判定」的判定锚，与漏斗同口径。
        b = man_bounds.get(cid)
        if b:
            n_man += 1
            starts = dar_segs.effective_starts(
                rounds, cid, man_bounds, man_labels,
                pre_starts={int(x) for x in (pre.get(cid) or {})
                            if str(x).isdigit() or isinstance(x, int)},
                frozen_len=man_frozen)
            si = 0
            for i in range(len(rj)):
                if si + 1 < len(starts) and i >= starts[si + 1]:
                    si += 1
                rj[i]["t"] = si
        out.append({
            "conversation_id": cid,
            "name": c.get("name") or "",
            "created_at": (c["created_at"] or "")[:16],
            "tester": c["is_tester"],
            "tasks": [{"id": t["id"], "at": (t.get("at") or "")[:16], "ts": ts(t.get("at"))}
                      for t in c.get("tasks") or []],
            "rounds": rj,
            "pre": pre.get(cid) or {},
        })
    out.sort(key=lambda x: x["created_at"])

    tpl = open(TPL, encoding="utf-8").read()
    # </ 转义：JSON 内嵌 <script> 时，内容里出现 </script> 会提前截断脚本（JS 字符串里 \/ 合法）
    payload = json.dumps({"convs": out, "env": ENV, "site": SITE_BASE,
                          "mode": "bounds" if args.bounds_only else "label"},
                         ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("__DATA__", payload)
    # 切题版（--bounds-only）写独立文件：与标注版互不覆写（0920 实锤：l3 后
    # 重跑 tool0 会把标注版工具冲回切分页）。title 按模式区分——两版同模板
    # 同 title 时浏览器标签页无法分辨开的是哪版（0929 向导错开旧标注页实锤）。
    is_bounds = args.bounds_only
    html = html.replace(
        "<title>话题切分审核</title>",
        "<title>话题切分审核（切题版·蓝）</title>" if is_bounds
        else "<title>话题切分审核（标注版·绿）</title>")
    path = os.path.join(OUT, "segmentation_tool_bounds.html" if is_bounds
                        else "segmentation_tool.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"生成 {path}")
    print(f"会话 {len(out)} 个（≥2 回合），回合 {sum(len(c['rounds']) for c in out)}"
          + (f"，滤掉纯寒暄（无咨询无工单）{n_noise} 个" if n_noise else "")
          + (f"，滤掉已全部判定的 {n_done} 个（标注轮只列有待标注段的会话）" if n_done else ""))
    if man_bounds:
        print(f"人工边界嵌入 {n_man} 个已切会话（初始切分=人工边界），LLM 切出多话题的 {n_llm_multi} 个")
    else:
        print(f"LLM 切出多话题的 {n_llm_multi} 个")


if __name__ == "__main__":
    main()
