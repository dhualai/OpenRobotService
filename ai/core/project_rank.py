"""
项目智能排序（ORS-713）：对话信号 → 项目匹配 → 弹窗/选题环排序。

链路：
  1. 会话自带信号（零 LLM）：扫码会话在 wechat_qrcodes 录入行有
     vehicle_model/project_code/customer_name/project_location；车型定制模式
     metadata.vehicle_mode.model。
  2. LLM 从近期对话提取补充信号：车型/客户/地点/项目名碎片（无信号关键词表，
     提取判断全交模型）。
  3. 项目画像：project 表（name/description/project_region/expected_trend/
     ext_info）+ qrcode 录入行聚合的 车型/客户 → 项目 映射。
  4. 机械评分：信号命中计分 + 生命周期阶段权重（活跃阶段加权，中止/已验收
     沉底）；机械无命中时 LLM 语义精排兜底。

排序只做推荐：弹窗仍可搜索全量，票史/名下信号排在其后，任何失败整体退化
为现状排序，不阻塞选项目。
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

_CACHE_TTL = 3600          # 项目画像缓存（项目变动不频繁，与 matcher 同口径）
_RANK_TOP_N = 5            # 返回给弹窗/选题环的推荐数
_LLM_TIMEOUT = 8.0         # 信号提取/精排共用硬超时：弹窗打开路径不能慢
_LLM_RERANK_THRESHOLD = 30 # 机械最高分低于此值 → LLM 语义精排兜底

# 生命周期阶段分层（真实数据在 project.status 列——正在实施 36/验收运营 20/
# 项目中止 4/实施暂停 10 等；expected_trend 几乎全空只作兜底）。
# 阶段是「层」不是「分」：同一个项目不会因为车型+40 就把已验收的顶到前面。
#   tier 0 进行中（最高优先级）/ tier 1 未启动（售前、签约、待进场）
#   tier 2 终态（验收运营、项目结束）——不参与推荐，弹窗里沉底
#   tier 3 异常终态（中止、暂停）——同上，且比正常终态更靠后
_STAGE_TIER = {
    "正在实施": 0, "实施运行": 0, "试运行中": 0, "内部运维": 0,
    "即将进场": 1, "延期进场": 1, "出厂测试": 1, "已签合同": 1, "项目变更": 1,
    "售前方案": 1, "签单洽谈": 1, "投标阶段": 1, "active": 1,
    "验收运营": 2, "项目结束": 2,
    "实施暂停": 3, "项目中止": 3,
}
_TERMINAL_TIERS = (2, 3)


def _stage_tier(stage: str) -> int:
    """未知/空阶段按 tier 1 处理（宁可正常参与，不误沉底）。"""
    return _STAGE_TIER.get((stage or "").strip(), 1)


def is_terminal_stage(stage: str) -> bool:
    """终态项目（已验收/已结束/中止/暂停）：不参与推荐，列表里沉底。"""
    return _stage_tier(stage) in _TERMINAL_TIERS

# 机械评分：信号类型 → 分值（同一类型多处命中只计一次，多类型累加）
_SCORES = {
    "qrcode_project": 60,  # 扫码行自带的项目（扫的就是这台车所属项目）
    "vehicle": 40,         # 车型命中（qrcode 聚合映射 / 项目名 / ext_info）
    "customer": 25,        # 客户名命中
    "region": 15,          # 地点命中
    "name_hint": 30,       # 项目名碎片命中
}


def _db() -> str:
    return os.getenv("HELPDESK_DB", "helpdesk_724")


class _Profiles:
    """项目画像缓存（进程内单份，TTL 刷新）。"""

    def __init__(self) -> None:
        self.by_code: Dict[str, Dict[str, Any]] = {}
        self.vehicle_map: Dict[str, set] = {}   # 车型(大写) → {project_code}
        self.customer_map: Dict[str, set] = {}  # 客户名 → {project_code}
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    def fresh(self) -> bool:
        return bool(self.by_code) and (time.time() - self._loaded_at) < _CACHE_TTL

    async def ensure_loaded(self) -> bool:
        if self.fresh():
            return True
        async with self._lock:
            if self.fresh():
                return True
            try:
                await asyncio.wait_for(self._load(), timeout=10)
                return True
            except Exception as e:
                logger.warning(f"[project_rank] 画像加载失败: {e}")
                return self.fresh()  # 过期数据也退回用，好过没有

    async def _load(self) -> None:
        from ai.core.database import SessionLocal
        loop = asyncio.get_running_loop()

        def _query():
            s = SessionLocal()
            try:
                db = _db()
                projs = s.execute(text(
                    f"SELECT code, name, description, status, expected_trend, "
                    f"project_region, ext_info FROM {db}.project "
                    f"WHERE undertake_status = '是' AND status != '已删除'"
                )).fetchall()
                qrs = s.execute(text(
                    f"SELECT project_code, vehicle_model, customer_name, "
                    f"project_location FROM {db}.wechat_qrcodes "
                    f"WHERE project_code IS NOT NULL AND project_code != ''"
                )).fetchall()
                return projs, qrs
            finally:
                s.close()

        projs, qrs = await loop.run_in_executor(None, _query)

        by_code: Dict[str, Dict[str, Any]] = {}
        for code, name, desc, status, trend, region, ext in projs:
            code = str(code or "").strip()
            if not code:
                continue
            by_code[code] = {
                "code": code, "name": str(name or "").strip(),
                "desc": str(desc or "")[:400],
                # 阶段真身在 status 列（中文生命周期值）；expected_trend 几乎
                # 全空，只作 status 缺失时的兜底
                "stage": (str(status or "").strip()
                          if str(status or "").strip() in _STAGE_TIER
                          else (str(trend or "").strip() or str(status or "").strip())),
                "region": str(region or "").strip(),
                # ext_info 递归 JSON：通用捞「车型样」的字符串值，结构不约定
                "ext_vehicles": _collect_vehicle_like(ext),
            }

        vehicle_map: Dict[str, set] = {}
        customer_map: Dict[str, set] = {}
        for code, vmodel, customer, location in qrs:
            code = str(code or "").strip()
            if not code:
                continue
            for v in _split_multi(vmodel):
                vehicle_map.setdefault(v, set()).add(code)
            for c in _split_multi(customer):
                customer_map.setdefault(c, set()).add(code)

        # 项目名/描述里的车型痕迹并入车型映射（LLM 之外的兜底画像源）
        for code, p in by_code.items():
            blob = f"{p['name']}"
            for v in list(vehicle_map.keys()):
                if v in blob:
                    vehicle_map[v].add(code)

        self.by_code = by_code
        self.vehicle_map = vehicle_map
        self.customer_map = customer_map
        self._loaded_at = time.time()
        logger.info(
            f"[project_rank] 画像加载: {len(by_code)} 项目, "
            f"{len(vehicle_map)} 车型, {len(customer_map)} 客户")


def _split_multi(raw) -> List[str]:
    """录入行字段可能是「A、B / A,B / A B」多值：拆开、去空。"""
    if not raw:
        return []
    s = str(raw).strip()
    if not s:
        return []
    out, cur = [], ""
    for ch in s:
        if ch in "、，,;/／| ":
            if cur.strip():
                out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _collect_vehicle_like(ext) -> List[str]:
    """ext_info 递归遍历，收集 key/值看起来像车型代号的短字符串。

    约定：不假设结构——遇到 key 含 model/vehicle/车型 的字符串值（≤32 字）收下；
    嵌套 dict/list 递归。空/超长值不收，避免把描述文本当车型。
    """
    out: List[str] = []

    def _walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and any(
                        t in str(k).lower() for t in ("model", "vehicle", "车型")):
                    for piece in _split_multi(v):
                        if 1 < len(piece) <= 32:
                            out.append(piece)
                else:
                    _walk(v, depth + 1)
        elif isinstance(node, list):
            for it in node:
                _walk(it, depth + 1)

    if ext:
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except Exception:
                return out
        _walk(ext)
    return out


_PROFILES = _Profiles()


# ============================================================
# 信号聚合
# ============================================================

async def collect_session_signals(memory) -> Dict[str, Any]:
    """会话自带信号（机械，零 LLM）：车型模式 + 扫码 qrcode 行。"""
    meta = (getattr(memory, "metadata", None) or {}) if memory else {}
    signals: Dict[str, Any] = {"vehicles": [], "customers": [], "regions": [],
                               "project_codes": [], "project_names": []}
    vm = meta.get("vehicle_mode") or {}
    if isinstance(vm, dict) and vm.get("model"):
        for m in _split_multi(vm.get("model")):
            if m not in signals["vehicles"]:
                signals["vehicles"].append(m)

    scene = str(meta.get("scene") or "").strip()
    if scene.isdigit():
        row = await _lookup_qrcode_row(scene)
        if row:
            for m in _split_multi(row.get("vehicle_model")):
                if m not in signals["vehicles"]:
                    signals["vehicles"].append(m)
            for c in _split_multi(row.get("customer_name")):
                if c not in signals["customers"]:
                    signals["customers"].append(c)
            for r in _split_multi(row.get("project_location")):
                if r not in signals["regions"]:
                    signals["regions"].append(r)
            code = str(row.get("project_code") or "").strip()
            if code:
                signals["project_codes"].append(code)
            nm = str(row.get("project_name") or "").strip()
            if nm:
                signals["project_names"].append(nm)
    return signals


async def _lookup_qrcode_row(scene: str) -> Optional[Dict[str, Any]]:
    from ai.core.database import SessionLocal
    loop = asyncio.get_running_loop()

    def _query():
        s = SessionLocal()
        try:
            row = s.execute(text(
                f"SELECT project_code, project_name, customer_name, "
                f"vehicle_model, project_location FROM {_db()}.wechat_qrcodes "
                f"WHERE id = :sid"), {"sid": int(scene)}).first()
            if not row:
                return None
            return {"project_code": row[0], "project_name": row[1],
                    "customer_name": row[2], "vehicle_model": row[3],
                    "project_location": row[4]}
        finally:
            s.close()

    try:
        return await loop.run_in_executor(None, _query)
    except Exception as e:
        logger.warning(f"[project_rank] qrcode 查询失败 scene={scene}: {e}")
        return None


# ============================================================
# LLM：对话信号提取 / 语义精排
# ============================================================

_EXTRACT_PROMPT = """你是工单系统的信息抽取器。从下面的客服对话里提取「能帮助判断属于哪个交付项目」的信号，只输出 JSON，不要解释。
字段（没有就给空数组）：
- vehicles: 对话中出现的机器人/车辆型号代号（字母数字混合的型号，含用户口语里提到的车型系列）
- customers: 出现的客户公司名（全称或明显简称）
- regions: 出现的现场地点（城市/厂区/园区名）
- project_names: 对话中直接提到的项目名或项目名碎片
要求：只提取用户侧明确说出的信息，不要推测、不要扩写；每项原样摘录。

对话：
{dialog}"""


async def extract_signals_llm(turns: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """LLM 从近期对话提取信号；失败/超时返回 None（机械信号不受影响）。"""
    dialog = "\n".join(
        f"{'用户' if t.get('role') == 'user' else '客服'}: {str(t.get('content') or '')[:500]}"
        for t in (turns or [])[-12:]
        if t.get("role") in ("user", "assistant"))
    if len(dialog.strip()) < 10:
        return None
    try:
        from ai.core.llm import get_llm_client
        client = await get_llm_client()
        raw = await asyncio.wait_for(
            client.chat([
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": dialog},
            ], temperature=0.0, max_tokens=300), timeout=_LLM_TIMEOUT)
        txt = (raw or "").strip()
        # 容错剥离 markdown 代码栅栏
        if txt.startswith("```"):
            txt = txt.strip("`").lstrip("json").strip()
        data = json.loads(txt)
        out: Dict[str, Any] = {"vehicles": [], "customers": [],
                               "regions": [], "project_names": []}
        for k in out:
            v = data.get(k)
            if isinstance(v, list):
                out[k] = [str(x).strip() for x in v
                          if isinstance(x, (str, int)) and str(x).strip()][:6]
        return out
    except Exception as e:
        logger.info(f"[project_rank] LLM 信号提取跳过: {e}")
        return None


_RERANK_PROMPT = """你是工单系统的项目匹配器。用户在对话里描述了一个现场问题，下面是候选交付项目列表。判断哪些项目与问题描述最相关，只输出 JSON 数组（按相关度降序），元素形如 {{"code": "项目编号", "reason": "≤10字理由"}}，最多 5 个；都不相关输出 []。不要解释。

问题描述：
{summary}

候选项目：
{candidates}"""


async def llm_rerank(turns: List[Dict[str, str]],
                     candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """机械评分无强命中时，LLM 语义精排候选（描述与项目的语义相关性）。"""
    summary = "\n".join(
        f"{'用户' if t.get('role') == 'user' else '客服'}: {str(t.get('content') or '')[:300]}"
        for t in (turns or [])[-8:] if t.get("role") in ("user", "assistant"))
    if len(summary.strip()) < 10 or not candidates:
        return []
    cand_txt = "\n".join(
        f"- {c['code']} {c['name']}（阶段:{c.get('stage') or '未知'}"
        f" 地点:{c.get('region') or '未知'}）{c.get('desc') or ''}"
        for c in candidates[:30])
    try:
        from ai.core.llm import get_llm_client
        client = await get_llm_client()
        raw = await asyncio.wait_for(
            client.chat([
                {"role": "system",
                 "content": _RERANK_PROMPT.format(summary=summary,
                                                 candidates=cand_txt)},
                {"role": "user", "content": summary},
            ], temperature=0.0, max_tokens=400), timeout=_LLM_TIMEOUT)
        txt = (raw or "").strip()
        if txt.startswith("```"):
            txt = txt.strip("`").lstrip("json").strip()
        data = json.loads(txt)
        if not isinstance(data, list):
            return []
        by_code = {c["code"]: c for c in candidates}
        out = []
        for it in data[:_RANK_TOP_N]:
            if isinstance(it, dict) and it.get("code") in by_code:
                c = by_code[it["code"]]
                out.append({"code": c["code"], "name": c["name"],
                            "reason": str(it.get("reason") or "")[:20]})
        return out
    except Exception as e:
        logger.info(f"[project_rank] LLM 精排跳过: {e}")
        return []


# ============================================================
# 机械评分 + 排序入口
# ============================================================

def _score_project(signals: Dict[str, Any], p: Dict[str, Any]) -> int:
    """纯信号分（不含阶段）——阶段是分层，见 _stage_tier / rank_for_session。"""
    if p["code"] in signals.get("project_codes", []):
        # 扫码自带项目是身份事实而非推测
        return _SCORES["qrcode_project"]
    score = 0
    for v in signals.get("vehicles", []):
        vu = v.upper()
        hit = (p["code"] in _PROFILES.vehicle_map.get(vu, set())
               or vu in p["name"].upper()
               or any(vu == ev.upper() for ev in p.get("ext_vehicles", [])))
        if hit:
            score += _SCORES["vehicle"]
            break
    for c in signals.get("customers", []):
        if p["code"] in _PROFILES.customer_map.get(c, set()) or c in p["name"]:
            score += _SCORES["customer"]
            break
    for r in signals.get("regions", []):
        if r and r in p["region"]:
            score += _SCORES["region"]
            break
    for nm in signals.get("project_names", []):
        if nm and (nm in p["name"] or p["name"] and p["name"] in nm):
            score += _SCORES["name_hint"]
            break
    return score


async def rank_for_session(memory, turns: List[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """入口：会话 → 推荐项目列表（top N，含 reason 内参）。

    任何一步失败都返回 []——调用方（弹窗/选题环）退化为现状排序。
    """
    if not await _PROFILES.ensure_loaded():
        return []
    try:
        signals = await collect_session_signals(memory)
    except Exception as e:
        logger.warning(f"[project_rank] 信号聚合失败: {e}")
        signals = {"vehicles": [], "customers": [], "regions": [],
                   "project_codes": [], "project_names": []}

    has_mech = any(signals.get(k) for k in
                   ("vehicles", "customers", "regions", "project_codes", "project_names"))
    if not has_mech:
        llm_sig = await extract_signals_llm(turns or [])
        if llm_sig:
            for k in ("vehicles", "customers", "regions", "project_names"):
                for v in llm_sig.get(k, []):
                    if v not in signals[k]:
                        signals[k].append(v)

    scanned = []
    for p in _PROFILES.by_code.values():
        s = _score_project(signals, p)
        if s <= 0:
            continue
        # 阶段分层：终态项目（验收运营/项目结束/中止/暂停）不进推荐——已验收
        # 的项目对现场问题没有参考价值，且用户实测「XNA 不动了」时被顶到前排
        # 的正是已结束的展会项目。唯一例外：扫码自带项目（身份事实，扫的就
        # 是这台车所属项目，哪怕项目已结束用户提单仍指向它）。
        _is_self = p["code"] in signals.get("project_codes", [])
        if is_terminal_stage(p.get("stage")) and not _is_self:
            continue
        # 扫码自带项目强制置顶（tier -1）：它是身份事实不是候选项，哪怕项目
        # 已结束，用户扫的就是这台车所属项目
        _tier = -1 if _is_self else _stage_tier(p.get("stage"))
        scanned.append((_tier, -s, p["name"], p))
    # 先按阶段层（进行中 > 未启动 > 终态），层内按信号分降序、同分按名称
    scanned.sort(key=lambda x: (x[0], x[1], x[2]))
    scored = [(-neg, p) for _tier, neg, _nm, p in scanned]

    # 机械零分 = 对话里连可匹配信号都没有：不推（误推成本高——排最前+高亮；
    # 弹窗用户自己会看票史），LLM 精排也不做（无信号无候选依据，全量取前 N
    # 等于随机候选）。机械强命中直接用；弱命中才走 LLM 语义精排。
    if not scored:
        return []
    if scored[0][0] >= _LLM_RERANK_THRESHOLD:
        return [{"code": p["code"], "name": p["name"], "reason": "机械"}
                for _, p in scored[:_RANK_TOP_N]]

    reranked = await llm_rerank(turns or [], [p for _, p in scored[:30]])
    if reranked:
        return reranked
    return [{"code": p["code"], "name": p["name"], "reason": "机械弱"}
            for _, p in scored[:_RANK_TOP_N]]
