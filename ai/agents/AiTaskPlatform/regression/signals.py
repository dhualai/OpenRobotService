"""回归信号：**客观可核对**的那几个，一个 LLM 都不用调。

## 为什么是这几个，而不是「判得对不对」

这一轮（issues.md #22）删掉了自评和旧 eval —— 因为那把尺子量不出东西（手写陷阱用例
换配置照样全过）。但删完就**没有任何度量**了：改完只能说「逻辑对」，说不了「答得更好」。

这里换个思路：**不去判「答得对不对」（那需要标准答案），只判「有没有明显的错法」**。
下面每一个都是确定性的、可复现的、不需要判官的：

  - S1 人名幻觉：回答里提到的**人名**，在「工单描述 + 提问之前的讨论史」里出现过吗
  - S2 引用真实性：回答里 `#N` 引用的工单，库里真的存在吗
  - S3 放弃率：「无法确认/信息不足」这类话术的占比有没有涨
  - S4 覆盖率：这批用例里有多少条**能算**（材料为空就无从核对，不能算通过）

## 边界（别把它当质量分读）

- S1 只查**人名**，不查错误码/数字。因为错误码的合法来源（日志分析、知识库）**不在**
  我们重建的材料里 —— 拿它当幻觉会大面积误报。
- 材料是「工单描述 + 提问前讨论史」，**不含**当年的附件分析/检索结果/记忆。
  所以 S1 抓的是「凭空冒出来的人」这类硬错，不是全部幻觉。
- 这些都是**错法检测**：降下去不等于答得更好，只是「没踩这几类坑」。
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# ── S1 人名幻觉 ─────────────────────────────────────────────

# 明显不是人名的条目 —— 实测踩到的（`users.name` 里真的存在）：
#   `...`（有人名字就是三个点）、`管理员`（角色词，回答里说「需管理员重启」会被误标）。
# 这类条目混进人名表会让 S1 大面积误报，必须在**匹配之前**滤掉。
_ROLE_WORDS = {
    "管理员", "系统", "客服", "测试", "匿名", "未知", "用户", "访客",
    "admin", "system", "guest", "none", "null", "unknown",
}
_NAME_EXTRA_CHARS = {"·", "-", " "}     # 阿依古丽·买买提 / Mary-Jane 这类


def is_plausible_person_name(name: str) -> bool:
    """像不像一个人名（用于清洗 users 表来的候选）。

    宁可严一点：漏掉真名只是少一处核对，误报一个角色词会让整批数字失真。
    """
    n = str(name or "").strip()
    if not (2 <= len(n) <= 20):
        return False
    if n.lower() in _ROLE_WORDS:
        return False
    return all(ch.isalpha() or ch in _NAME_EXTRA_CHARS for ch in n)


def find_mentioned_names(text: str, known_names: Iterable[str]) -> list[str]:
    """从 text 里找出「已知人名」的出现（长名优先，避免短名吃掉长名的一部分）。

    用「已知人名集合」而不是做 NER：这个域的人名基本都在 users 表里，
    拿表去匹配**没有误报**，也不需要模型。
    """
    names = sorted({str(n).strip() for n in (known_names or [])
                    if is_plausible_person_name(n)},
                   key=len, reverse=True)
    taken: list[tuple[int, int]] = []
    found: list[str] = []
    for name in names:
        start = 0
        while True:
            i = text.find(name, start)
            if i < 0:
                break
            span = (i, i + len(name))
            # 与已匹配的长名重叠 → 跳过（「张三丰」命中后不该再报「张三」）
            if not any(not (span[1] <= a or span[0] >= b) for a, b in taken):
                taken.append(span)
                if name not in found:
                    found.append(name)
            start = i + 1
    return found


def name_hallucination(answer: str, material: str, known_names: Iterable[str]) -> dict:
    """回答里提到、但材料里**完全没有**的人名。

    material 为空时返回 supported=False（**材料都没有，就无从核对** —— 不算通过，
    也不算失败，单独标出来，别让它混进比率里）。
    """
    if not (material or "").strip():
        return {"supported": False, "mentioned": [], "unsupported": []}
    mentioned = find_mentioned_names(answer or "", known_names)
    unsupported = [n for n in mentioned if n not in (material or "")]
    return {"supported": True, "mentioned": mentioned, "unsupported": unsupported}


# ── S2 引用真实性 ───────────────────────────────────────────

_TICKET_REF = re.compile(r"#(\d{1,7})")


def cited_ticket_ids(text: str) -> list[str]:
    """回答里 `#N` 形式的工单引用（去重、保序）。"""
    out: list[str] = []
    for m in _TICKET_REF.finditer(text or ""):
        tid = m.group(1)
        if tid not in out:
            out.append(tid)
    return out


def check_citations(answer: str, existing_ids: Iterable[str]) -> dict:
    """回答引用的工单号是否存在。不存在的引用是**确定错**（凭空引用）。"""
    known = {str(x) for x in (existing_ids or [])}
    cited = cited_ticket_ids(answer or "")
    missing = [t for t in cited if t not in known]
    return {"cited": cited, "missing": missing}


# ── S3 放弃率 ───────────────────────────────────────────────

# 「没答上来」的话术。注意不要把正常的「需要你补充 X」算进来 —— 那是产品设计
# （澄清闭环），不是放弃。所以只收「我无法确认/没有足够信息」这一类。
_GIVEUP_WORDS = (
    "无法确认", "无法核实", "无法判断", "无法确定",
    "没有足够", "信息不足", "资料不足", "证据不足",
    "暂时无法", "暂无法", "不能确认", "不清楚具体情况",
)


def is_giveup(answer: str) -> dict:
    """回答是否是「放弃式」收尾（只报现象，不给结论）。"""
    text = answer or ""
    hits = [w for w in _GIVEUP_WORDS if w in text]
    return {"giveup": bool(hits), "hits": hits}


# ── 汇总 ────────────────────────────────────────────────────

def score_case(case: dict, known_names: Iterable[str], existing_ticket_ids: Iterable[str]) -> dict:
    """跑一条用例的全部信号。case 需含 answer / material。"""
    answer = case.get("answer") or ""
    material = case.get("material") or ""
    hall = name_hallucination(answer, material, known_names)
    cite = check_citations(answer, existing_ticket_ids)
    give = is_giveup(answer)
    return {
        "case_id": case.get("case_id"),
        "name_supported": hall["supported"],
        "names_mentioned": hall["mentioned"],
        "names_unsupported": hall["unsupported"],
        "cited": cite["cited"],
        "citations_missing": cite["missing"],
        "giveup": give["giveup"],
        "giveup_hits": give["hits"],
    }


def aggregate(rows: list[dict]) -> dict:
    """把逐条结果汇总成可比对的数字。

    分母一律写清楚：`name_supported` 的用例才进 S1 的分母；`cited` 非空的才进 S2 的
    分母 —— 否则「没提到人名」会被算成「人名幻觉良好」，那是假的好看。
    """
    n = len(rows)
    name_rows = [r for r in rows if r.get("name_supported")]
    cite_rows = [r for r in rows if r.get("cited")]
    return {
        "cases": n,
        "name_checked": len(name_rows),
        "name_bad": sum(1 for r in name_rows if r.get("names_unsupported")),
        "cited_checked": len(cite_rows),
        "cited_bad": sum(1 for r in cite_rows if r.get("citations_missing")),
        "giveup": sum(1 for r in rows if r.get("giveup")),
    }


def compare(baseline: Optional[dict], current: dict) -> dict:
    """与基线比。**只在变差时报警** —— 变好不需要拦。"""
    if not baseline:
        return {"has_baseline": False, "regressions": []}
    worse = []
    for key in ("name_bad", "cited_bad", "giveup"):
        b, c = int(baseline.get(key) or 0), int(current.get(key) or 0)
        if c > b:
            worse.append({"metric": key, "baseline": b, "current": c})
    return {"has_baseline": True, "regressions": worse}
