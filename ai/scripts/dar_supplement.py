# -*- coding: utf-8 -*-
"""未覆盖段 → 待补充知识点聚类（ORS-1159 知识补充上服务号·数据侧）。

读 dar 周分析产物里「知识库未覆盖」的段，交给 LLM 归纳成**通用知识点**：
问法不同但本质相同的问题合并，标题去掉车型/项目/库位等上下文，写成用户
会问的通用问题（未来知识卡入库后的 H1，也就是检索入口）。

产物：export_dar/{env}/processed/supplement_candidates_YYYYMMDD.json
  items[] = {id, title, description, qtype, count, sources, status}
  status: pending（待人工审核）/ excluded（剔除）/ pushed（已推送服务号）

工作台「知识补充」页签读这份文件做审核、编辑、剔除、推送。

用法：
    python ai/scripts/dar_supplement.py --env prod
    （dar_weekly 周流程 step=supplement 调用，默认 prod 跟随 --env）
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime as _dt, timedelta as _td

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA_ROOT = r"C:/Users/PAJ26020/Desktop/export_dar"

BATCH = 40          # 单次 LLM 聚类的问题条数（80 条时输出 JSON 超 4k token 被截断，实锤）
MAX_INPUT = 600     # 最多参与聚类的去重问题数（全量统计，超出按出现顺序截断）

# 琐碎问题不列为知识点（与周报样例过滤同口径）：账号/密码/登录类、太短无信息量
TRIVIAL_KEYS = ("账号", "密码", "登录", "注册", "怎么下载", "在哪下载")

QTYPES = ("故障处置", "操作指引", "功能咨询", "状态查询", "资料查询", "其他")

PROMPT = """你是知识库知识缺口分析助手。下面是用户向客服 AI 提问、但 AI 没能解决的问题记录，分两类：
[未覆盖] 知识库完全没有对应资料；
[未直答] 知识库有资料，但回答没解决问题（资料不完整/不准确/检索没命中）。两类都要归纳成待补充知识点，不要因为「可能有资料」而排除。

请把它们归纳成「待补充知识点」清单。规则：
1. 每条知识点 = 一个**通用问题标题** + 简要描述，代表一类本质相同的问题。
2. 标题必须抽象、去上下文：去掉车型编号（如 XQE/XFL）、项目名、库位号、人名、时间等具体信息，保留问题本质，用用户的口吻提问。
   正例：「AGV 报目标不可达怎么办」；反例：「XQE 在 3 号库位取货报目标不可达」。
3. 问法不同但本质相同的问题**必须合并**为一条（如「识别超时」「取货一直识别失败」「堆垛报识别超时」→ 合并成一条）。
4. 纯账号申请、忘记密码、软件在哪下载这类琐碎问题**不要**列为知识点。
5. 同一知识点的「未覆盖」和「未直答」问题合并为一条（补的知识要同时消除这两类缺口）。
6. qtype 只能取：故障处置 / 操作指引 / 功能咨询 / 状态查询 / 资料查询 / 其他。
7. description 写 2~3 句：这个知识点要回答什么（现象、触发条件、影响），**不要写答案、不要臆造原因**。

输入问题（格式：序号|[类型]问题原文）：
{lines}

只输出 JSON，不要任何解释：
{{"items": [{{"title": "通用问题标题", "description": "2~3 句描述", "qtype": "故障处置", "indices": [0, 3, 7]}}]}}
indices 是归入该知识点的问题序号（整数，可多个）。"""

MERGE_PROMPT = """下面是两批分别归纳出的「待补充知识点」。请合并去重：本质相同的知识点合并为一条（保留信息更全的标题与描述，indices 取并集）。

输入知识点：
{lines}

只输出 JSON：{{"items": [{{"title": "...", "description": "...", "qtype": "...", "indices": [0, 5, 9]}}]}}
indices 是归入该知识点的输入知识点序号（整数，可多个）。"""


def _out_dir(env: str) -> str:
    return os.path.join(DATA_ROOT, env, "processed")


def _week_window(now=None):
    """最近一个完整自然周 [上周一 0 点, 本周一 0 点)。与周报周口径一致。"""
    now = now or _dt.now()
    this_monday = (now - _td(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return this_monday - _td(days=7), this_monday


def _parse_at(s):
    try:
        return _dt.fromisoformat((s or "")[:19].replace("T", " "))
    except ValueError:
        return None


LAYER_LABEL = {"uncovered": "未覆盖", "unanswered": "未直答"}


def collect_gaps(env: str, since: _dt | None, until: _dt):
    """知识缺口段 → 去重问题列表 [{question, sources}]。

    两类一起收、一起总结（1126 用户定）：
      uncovered（未覆盖）= 知识库没有对应资料；
      unanswered（未直答）= 有资料但回答没解决问题——多为现有内容不完善，
                            同样需要补/完善。
    since=None 表示不设起始（全量历史，截至最新数据）；until 之后不计。"""
    import dar_studio

    rows, meta = dar_studio._seg_rows(env)
    if not rows:
        raise RuntimeError(f"段级清单为空（{meta.get('reason', '缺产物，先跑周流程')}）")

    seen, out = {}, []
    for r in rows:
        if r.get("layer") not in ("uncovered", "unanswered"):
            continue
        t = _parse_at(r.get("at"))
        if not t or t >= until or (since is not None and t < since):
            continue
        q = (r.get("question") or "").replace("\n", " ").strip()
        if len(q) < 5 or any(k in q for k in TRIVIAL_KEYS):
            continue
        key = re.sub(r"\s+", "", q)
        src = {"cid": str(r.get("cid")), "astart": r.get("astart"),
               "layer": r.get("layer")}
        if key in seen:
            seen[key]["sources"].append(src)
            seen[key]["occ"] = seen[key].get("occ", 1) + 1
            continue
        item = {"question": q[:200], "sources": [src], "occ": 1}
        seen[key] = item
        out.append(item)
    return out[:MAX_INPUT]


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("LLM 输出无 JSON")
    return json.loads(m.group(0))


async def _cluster_batch(llm, items: list[dict]) -> list[dict]:
    lines = "\n".join(
        f"{i}|[{LAYER_LABEL.get((it['sources'][0].get('layer') if it.get('sources') else '') or '', '')}]"
        f"{it['question']}"
        for i, it in enumerate(items))
    last = None
    for _attempt in range(2):  # 输出偶发截断/坏 JSON：重试一次，别丢整批
        try:
            raw = await llm.complete(prompt=PROMPT.format(lines=lines),
                                     max_tokens=8000, temperature=0.1, thinking=False)
            return _parse_json(raw).get("items") or []
        except Exception as e:
            last = e
    raise last  # type: ignore[misc]


def _attach(items: list[dict], batch_no: int, batch: list[dict]) -> list[dict]:
    """把一批 LLM 结果映射回原始问题（sources/count）。"""
    out = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        idx = [i for i in (it.get("indices") or []) if isinstance(i, int) and 0 <= i < len(batch)]
        srcs, seen_q = [], set()
        for i in idx:
            q = batch[i]["question"]
            if q in seen_q:
                continue
            seen_q.add(q)
            base = batch[i]["sources"][0]
            srcs.append({"cid": str(base["cid"]), "astart": base["astart"],
                         "layer": base.get("layer", ""), "q": q[:200]})
        out.append({
            "title": title[:80],
            "description": (it.get("description") or "").strip()[:400],
            "qtype": it.get("qtype") if it.get("qtype") in QTYPES else "其他",
            "count": sum(batch[i].get("occ", 1) for i in idx),
            "uncovered": sum(1 for x in srcs if x.get("layer") == "uncovered"),
            "unanswered": sum(1 for x in srcs if x.get("layer") == "unanswered"),
            "sources": srcs,
        })
    return out


async def _merge_pass(llm, cands: list[dict]) -> list[dict]:
    """一轮合并：每 60 条知识点一组组内合并（189 条一次送 prompt 必超时，实锤）。"""
    out = []
    for i in range(0, len(cands), 60):
        batch = cands[i:i + 60]
        if len(batch) <= 1:
            out.extend(batch)
            continue
        lines = "\n".join(
            f"{j}|{c['title']}——{c['description'][:100]}" for j, c in enumerate(batch))
        try:
            raw = await llm.complete(prompt=MERGE_PROMPT.format(lines=lines),
                                     max_tokens=8000, temperature=0.1, thinking=False)
            items = _parse_json(raw).get("items") or []
        except Exception as e:
            print(f"（合并批次 {i // 60 + 1} 失败，保留原样：{e}）")
            out.extend(batch)
            continue
        merged = []
        for it in items:
            title = (it.get("title") or "").strip()
            idx = [j for j in (it.get("indices") or [])
                   if isinstance(j, int) and 0 <= j < len(batch)]
            if not title or not idx:
                continue
            group = [batch[j] for j in idx]
            desc = it.get("description") or ""
            longest = max(group, key=lambda g: len(g["description"]))
            if len(desc) < len(longest["description"]):
                desc = longest["description"]
            qtype = it.get("qtype") if it.get("qtype") in QTYPES else group[0]["qtype"]
            merged.append({
                "title": title[:80],
                "description": (desc or "").strip()[:400],
                "qtype": qtype,
                "count": sum(g["count"] for g in group),
                "uncovered": sum(g.get("uncovered", 0) for g in group),
                "unanswered": sum(g.get("unanswered", 0) for g in group),
                "sources": _merge_sources(group),
            })
        out.extend(merged or batch)
    return out


async def _merge(llm, cands: list[dict]) -> list[dict]:
    """迭代合并直到收敛（每轮至少减少 10% 才继续，最多 3 轮）。"""
    for _round in range(3):
        before = len(cands)
        cands = await _merge_pass(llm, cands)
        print(f"  二次合并第 {_round + 1} 轮：{before} → {len(cands)} 条")
        if len(cands) >= before * 0.9:
            break
    return cands


def _merge_sources(groups: list[dict]) -> list[dict]:
    """合并多组来源，按问题原文去重。"""
    seen, out = set(), []
    for g in groups:
        for src in g.get("sources") or []:
            key = src.get("q") or f"{src.get('cid')}:{src.get('astart')}"
            if key in seen:
                continue
            seen.add(key)
            out.append(src)
    return out


def _make_id(title: str) -> str:
    return "sk" + hashlib.md5(title.encode("utf-8")).hexdigest()[:10]


def _carry_status(new_items: list[dict], old_path: str) -> None:
    """重跑保留人工状态：同 id 的 status/edited 字段继承（标题被人工改过则按内容匹配）。"""
    if not os.path.exists(old_path):
        return
    try:
        old = json.load(open(old_path, encoding="utf-8"))
    except Exception:
        return
    by_id = {it.get("id"): it for it in old.get("items") or []}
    for it in new_items:
        prev = by_id.get(it["id"])
        if prev:
            for k in ("status", "edited"):
                if k in prev:
                    it[k] = prev[k]


async def main_async(env: str) -> dict:
    # 全量口径（1126 用户定）：截至最新数据导出，历史上所有未覆盖 + 未直答问题都纳入；
    # 之后每周周流程增量再跑（工作台推送后同 id 不重复，已推送的不会再列）。
    until = _dt.now()
    print(f"知识补充候选：全量知识缺口（未覆盖 + 未直答，截至最新数据，{env}）")
    questions = collect_gaps(env, None, until)
    print(f"去重后可参与聚类的问题：{len(questions)} 条")
    if not questions:
        print("没有需要补的知识缺口")
        return {"items": []}

    from dar_llm import get_dar_client
    llm = await get_dar_client()

    cands = []
    for i in range(0, len(questions), BATCH):
        batch = questions[i:i + BATCH]
        print(f"  聚类批次 {i // BATCH + 1}：{len(batch)} 条…")
        try:
            items = await _cluster_batch(llm, batch)
            cands.extend(_attach(items, i // BATCH, batch))
        except Exception as e:
            print(f"  ⚠️ 批次 {i // BATCH + 1} 聚类失败，跳过：{e}")

    cands = await _merge(llm, cands)

    # 合并后按出现次数降序；id 由标题决定（重跑稳定）
    cands.sort(key=lambda c: -c["count"])
    items = []
    for c in cands:
        items.append({
            "id": _make_id(c["title"]),
            "title": c["title"],
            "description": c["description"],
            "qtype": c["qtype"],
            "count": c["count"],
            "uncovered": c.get("uncovered", 0),
            "unanswered": c.get("unanswered", 0),
            "sources": c["sources"][:30],   # 来源提问只留前 30 条，够核对
            "status": "pending",
            "edited": False,
        })

    out_path = os.path.join(_out_dir(env), f"supplement_candidates_{_dt.now():%Y%m%d}.json")
    _carry_status(items, out_path)
    data = {
        "env": env,
        "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "week_from": "全量",
        "week_to": _dt.now().strftime("%Y-%m-%d"),
        "model": getattr(llm, "model", ""),
        "stats": {"uncovered_unique": len(questions), "items": len(items)},
        "items": items,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f"✅ 知识点候选 {len(items)} 条 → {out_path}")
    for it in items[:8]:
        print(f"  · [{it['qtype']}] {it['title']}（{it['count']} 次）")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=os.environ.get("DAR_ENV", "prod"),
                    choices=["prod", "test"])
    args = ap.parse_args()
    asyncio.run(main_async(args.env))


if __name__ == "__main__":
    main()
