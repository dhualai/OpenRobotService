# -*- coding: utf-8 -*-
"""人工边界段首的统一展开口径（0920）：漏斗/L2 指标/L3 预标/标注工具/L1 review 同源。

背景（0915→0920 两次口径演进）：
- 0915「bounds 优先」治 L1 重跑洗人工标签：有人工边界的会话段首恒取 manual.bounds。
- 副作用（0920 实锤）：bounds 只存段首索引，末段恒延伸到会话末尾——老窗口续聊的
  新回合折叠进末段、戴着旧判定（人工标签/L3 预标）在漏斗与周报里隐形。实测本周
  40% 活跃量在老窗口，6/11 个续聊会话的新提问完全丢失于统计。

规则：
  1. bounds 优先（0915 口径不变，旧段/旧标签一律不动）
  2. 末段已有判定（人工标签或 L3 预标）且出现「标注之后的新回合」时追加段首：
     - frozen_len（主规则，精确）：保存人工标注时冻结的回合总数，rounds 超过它
       即从冻结点切新段。frozen_len 由 dar_studio /api/save_manual 落盘时统一
       按 split 回合数注入，标注工具零改动。
     - 时间跳变（兜底，覆盖无 frozen_len 的存量）：末段内相邻回合间隔 ≥48h →
       第一个跳变点追加一刀（保守少切；标注保存后即转主规则精确化）。
  3. 末段无判定 → 新回合本就处于待走查状态（进未标注），不追加。

追加出的新段无任何判定 → 落「未标注对话」模块，等 L3 预标 / 人工走查，
与 0915「无判定不进漏斗六层」的口径衔接。
"""
from datetime import datetime

# 兜底跳变阈值：同周内续聊不切，隔两天以上视为新问题。宁可漏切（老段多兜一天）
# 不可错切——错切只多一个未标注段（可走查修正），漏切则统计隐形（不可见）。
GAP_HOURS = 48.0


def parse_ts(s):
    """回合时间：兼容「2026-09-18 15:03:11」与 ISO「2026-09-10T09:49:01」。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("T", " ").strip())
    except ValueError:
        return None


def effective_starts(rounds, cid, bounds, labels=None, pre_starts=None,
                     frozen_len=None, gap_hours=GAP_HOURS, n=None):
    """展开会话段首索引（升序，0 恒在）。

    rounds: split 的回合数组（元素含 at）；n=段索引上界（默认 len(rounds)，
    供 cls 与 rounds 长度可能不一致的调用方显式传入）。
    bounds: {cid: [段首,...]}；labels: {cid: {段首: 标签}}；pre_starts: 该会话
    已有 L3 预标的段首集合；frozen_len: {cid: 标注时回合总数}。
    无人工 bounds 返回 None——追加规则只对人工已定界的会话生效，调用方
    自行走 LLM/topic 切分。"""
    n = len(rounds) if n is None else n
    b = (bounds or {}).get(str(cid))
    if b is None:
        return None
    starts = sorted({0, *(int(x) for x in b
                          if (isinstance(x, int) or str(x).isdigit())
                          and 0 <= int(x) < n)})
    last = starts[-1]
    lm = (labels or {}).get(str(cid))
    lab = {int(k) for k, v in lm.items() if str(k).isdigit() and v} \
        if isinstance(lm, dict) else set()
    judged = last in lab or (pre_starts is not None and last in pre_starts)
    if not judged or n <= last + 1:
        return starts
    # 主规则：frozen_len 精确追加（标注后新增的回合从冻结点起成新段）。
    # 一旦会话有 frozen_len 就以它为准——fl<=last 说明 bounds 后来被人工改过
    # （标注者看过尾部选择了不切），此时连跳变兜底也不启用（尊重人工判断）。
    fl = (frozen_len or {}).get(str(cid))
    if isinstance(fl, int):
        return sorted(set(starts) | {fl}) if last < fl < n else starts
    # 兜底：末段内首个 ≥gap_hours 的相邻回合跳变处追加一刀。
    t_prev = parse_ts((rounds[last] or {}).get("at"))
    for i in range(last + 1, n):
        t_cur = parse_ts((rounds[i] or {}).get("at"))
        if t_prev and t_cur and (t_cur - t_prev).total_seconds() >= gap_hours * 3600:
            return sorted(set(starts) | {i})
        if t_cur:
            t_prev = t_cur
    return starts
