# -*- coding: utf-8 -*-
"""0902 知识沉淀人工审核工具：导出对照材料 → 人工标注 → 回写判定。

沉淀链路写卡默认 review_status=pending（不可检索），本工具负责三件事：
  --status              按 review_status 统计 ticket_resolutions 卡数
  --export              导出 pending 卡的「工单原文 ↔ 提炼卡」对照材料：
                        cards.md（阅读版）+ review.csv（标注用，填 verdict 列）
  --apply review.csv    读标注结果回写：approved → set_payload 放行可检索；
                        rejected / test → 从 Qdrant 删点（DB 的 solution_indexed
                        标记仍为 true，worker 不会重建该卡——驳回即终态）

标注列：verdict（approved/rejected/test）+ reason（结构化驳回理由，rejected
必填：hallucination/wrong_focus/incomplete/low_value/duplicate/other）+
note（自由备注）。apply 把全部判定连同理由追加到 review_history.jsonl——
点删了理由也留得住，这份记录是后续优化提炼 prompt / 转审核 agent 的依据。

用法（本地/生产自适应 qdrant，连哪个库由 DATABASE_URL 决定）：
  python -m ai.tools.review_resolutions --status
  python -m ai.tools.review_resolutions --export
  python -m ai.tools.review_resolutions --apply <export_dir>/review.csv --reviewer 张三

输出目录：OpenRobotService_Data/review/ticket_resolutions/（与 kb/ 并列，
不进仓库）。将来 agent 审核走同一 apply 接口，--reviewer 填 agent 名。
"""
import argparse
import csv
import json
import os
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, "ai", ".env"))

_VERDICTS = ("approved", "rejected", "test")

# 结构化驳回理由（0902 用户要求：审核结论要能作为后续优化沉淀 agent 的依据，
# 不能只有通过/不通过）。approved 也可选填记改进建议。
_REASONS = {
    "hallucination": "提炼内容是原文没有的（编造/脑补）",
    "wrong_focus":   "问题或根因抓错重点",
    "incomplete":    "漏了关键解决步骤",
    "low_value":     "工单本身无知识含量，不该沉淀",
    "duplicate":     "与已有知识/卡重复",
    "other":         "其他（note 列写具体说明）",
}


def _review_dir() -> str:
    from ai.config import _KB_DIR
    return str(_KB_DIR.parent / "review" / "ticket_resolutions")


def _get_client():
    from qdrant_client import QdrantClient
    from ai.config import get_active_collection_for
    col = get_active_collection_for("company")
    if not col:
        print("!! company 域没有 active collection 指针（.env）")
        sys.exit(1)
    lp = os.getenv("QDRANT_LOCAL_PATH", "").strip()
    qc = QdrantClient(path=lp) if lp else QdrantClient(
        host=os.getenv("QDRANT_HOST", "localhost"),
        port=int(os.getenv("QDRANT_PORT", "6333")),
        check_compatibility=False,
    )
    return qc, col


def _scroll_tickets(qc, col, status: str | None):
    """scroll 出 ticket_resolutions 的卡（status=None 不过滤 review_status）。"""
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    must = [FieldCondition(key="sub_domain", match=MatchValue(value="ticket_resolutions"))]
    if status:
        must.append(FieldCondition(key="review_status", match=MatchValue(value=status)))
    points, offset = [], None
    while True:
        batch, offset = qc.scroll(
            collection_name=col,
            scroll_filter=Filter(must=must),
            limit=64, offset=offset, with_payload=True, with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            break
    return points


def _db_available() -> bool:
    try:
        from ai.core.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return True
    except Exception as e:
        print(f"（DB 不可达，对照材料用卡内自带素材: {e}）")
        return False


def _db_ticket(task_id) -> dict | None:
    """工单原文（title/description/resolution_summary/评论）——给提炼器的同一份输入。

    附带 resolver：按修复后的 resolver_name 逻辑重算（users 未命中回退评论
    最后发言人）——存量卡 payload 里的 resolver 可能是没映射上的微信 openid。
    """
    try:
        from ai.core.database import SessionLocal
        from ai.core.solution_sink import load_comments_text, resolver_name
        from sqlalchemy import text
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT title, description, metadata_info, assigned_to, task_type "
                "FROM tasks WHERE id = :tid"),
                {"tid": int(task_id)}).mappings().first()
        finally:
            db.close()
        if not row:
            return None
        meta = row.get("metadata_info") or {}
        if not isinstance(meta, dict):
            try:
                meta = json.loads(meta) if meta else {}
            except Exception:
                meta = {}
        comments, last_human = load_comments_text(int(task_id))
        from ai.core.solution_sink import _resolver_from_oplog
        return {
            "title": row.get("title") or "",
            "description": row.get("description") or "",
            "resolution_summary": meta.get("resolution_summary") or "",
            "comments_text": comments,
            "resolver": (_resolver_from_oplog(int(task_id))
                         or resolver_name(row.get("assigned_to") or "", last_human)),
            "ticket_type": getattr(row.get("task_type"), "value",
                                   row.get("task_type")) or "",
        }
    except Exception:
        return None


def _sec(title: str, body: str) -> str:
    body = (body or "").strip()
    return f"**{title}**\n\n{(body or '（空）')}\n\n"


def cmd_status(qc, col) -> None:
    points = _scroll_tickets(qc, col, None)
    counts: dict[str, int] = {}
    for p in points:
        st = (p.payload or {}).get("review_status", "") or "(无字段)"
        counts[st] = counts.get(st, 0) + 1
    print(f"ticket_resolutions 卡总数: {len(points)}")
    for st, n in sorted(counts.items()):
        print(f"  {st}: {n}")


def _card_material(pl: dict, src: dict | None):
    """单卡展示材料：((原文段列表), (提炼段列表), 素材来源说明)。md/html 共用。"""
    if src:
        origin = [
            ("问题描述", src["description"]),
            ("结案总结 resolution_summary", src["resolution_summary"]),
            ("评论区（提炼器所见）", src["comments_text"]),
        ]
        origin_note = "DB 全文"
    else:
        origin = [
            ("问题描述（入库时截断至 1000 字）", pl.get("description", "")),
            ("评论区（入库时截断）", pl.get("comments_text", "")),
        ]
        origin_note = "卡内自带（DB 不可达）"
    distilled = [
        ("问题", pl.get("problem_summary", "")),
        ("根因", pl.get("root_cause", "")),
        ("解决步骤", pl.get("solution_steps", "")),
    ]
    return origin, distilled, origin_note


_TYPE_CN = {"problem": "问题", "bug": "缺陷", "feature": "功能",
            "support": "支持", "other": "其他"}


def _card_meta_bits(pl: dict, src: dict | None) -> list[str]:
    """meta 行：类型、解决人（DB 增强版优先）、项目、故障码/车型（有值才显示）。"""
    ttype = (src or {}).get("ticket_type") or pl.get("ticket_type", "")
    bits = []
    if ttype:
        # 库里 task_type 存大写（BUG/FEATURE），映射表小写——统一 lower 后再查
        bits.append(f"类型: {_TYPE_CN.get(ttype.lower(), ttype)}({ttype})")
    resolver = (src or {}).get("resolver") or pl.get("resolver", "")
    bits.append(f"解决人: {resolver}")
    bits.append(f"项目: {pl.get('project_name', '')}")
    if pl.get("fault_code"):
        bits.append(f"故障码: {pl['fault_code']}")
    if pl.get("robot_type"):
        bits.append(f"车型: {pl['robot_type']}")
    return bits


# 自包含审核页（无任何外部依赖，浏览器打开即用）：逐卡点选判定，导出填好的 CSV。
# 数据经 json.dumps 注入，</ 转义防 script 提前闭合。
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>工单沉淀审核</title>
<style>
  body { font-family: system-ui, "Microsoft YaHei", sans-serif; margin: 0; background: #f5f6f8; }
  #bar { position: sticky; top: 0; z-index: 9; background: #fff; border-bottom: 1px solid #ddd;
         padding: 10px 16px; display: flex; align-items: center; gap: 16px; }
  #bar b { font-size: 15px; }
  #bar button { padding: 8px 18px; font-size: 14px; cursor: pointer;
                background: #2563eb; color: #fff; border: none; border-radius: 6px; }
  #cards { max-width: 980px; margin: 16px auto; padding: 0 12px; }
  .card { background: #fff; border: 1px solid #e2e4e8; border-left: 5px solid #cbd5e1;
          border-radius: 8px; padding: 14px 18px; margin-bottom: 14px; }
  .card.v-approved { border-left-color: #16a34a; }
  .card.v-rejected { border-left-color: #dc2626; }
  .card.v-test     { border-left-color: #9ca3af; }
  .card h3 { margin: 0 0 6px; font-size: 15px; }
  .meta { color: #666; font-size: 12.5px; margin-bottom: 8px; }
  details { margin: 6px 0; }
  summary { cursor: pointer; font-weight: 600; font-size: 13.5px; color: #374151; }
  .block { font-size: 13.5px; }
  .block h4 { margin: 8px 0 2px; font-size: 13px; color: #6b7280; }
  .block p { margin: 2px 0; white-space: pre-wrap; }
  .distill { background: #f0f9ff; border-radius: 6px; padding: 8px 12px; margin-top: 8px; }
  .ops { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; }
  .ops button { padding: 7px 16px; font-size: 13.5px; cursor: pointer; border-radius: 6px;
                border: 1px solid #cbd5e1; background: #fff; }
  .ops button.on-approved { background: #16a34a; color: #fff; border-color: #16a34a; }
  .ops button.on-rejected { background: #dc2626; color: #fff; border-color: #dc2626; }
  .ops button.on-test { background: #6b7280; color: #fff; border-color: #6b7280; }
  .ops select, .ops input { padding: 6px 8px; font-size: 13px; border-radius: 6px;
                            border: 1px solid #cbd5e1; }
  .ops input { flex: 1; min-width: 160px; }
  .ops .lbl { font-size: 12.5px; color: #666; }
  .skip-box { background: #fff; border: 1px dashed #d1a32a; border-radius: 8px;
              max-width: 980px; margin: 20px auto; padding: 12px 16px; }
  .skip-box > summary { font-size: 14.5px; color: #92700c; }
  .skip-card { border-top: 1px solid #eee; padding: 8px 0 4px; margin-top: 8px; }
  .skip-card h3 { margin: 0 0 4px; font-size: 14px; }
  .skip-card .meta { margin-bottom: 4px; }
  .skip-note { font-size: 12.5px; color: #92700c; margin: 6px 0 0; }
</style>
</head>
<body>
<div id="bar"><b>工单沉淀审核</b> <span id="progress"></span>
  <select id="typeFilter" onchange="filterType(this.value)"></select>
  <button onclick="bulkTest()">可见未判定 → 测试单</button>
  <button onclick="saveCsv()">保存标注结果</button></div>
<div id="cards"></div>
<details class="skip-box" id="skippedBox" style="display:none">
  <summary>▢ AI 判定跳过（无知识可提）的工单（点开抽检误杀）</summary>
  <div id="skipList"></div>
</details>
<script>
const CARDS = __CARDS__;
const REASONS = __REASONS__;
const TYPES = __TYPES__;
const SKIPPED = __SKIPPED__;
const state = {};  // point_id -> {verdict, reason, note}
// 判定本地持久化（0910 实锤：审完直接关页/刷新全丢——state 只在内存）：
// 每步点选即存 localStorage（按导出目录分键），重开页面自动恢复
const DIR = new URLSearchParams(location.search).get("dir") || "local";
const LS_KEY = "sink_review_" + DIR;
function persistState() { try { localStorage.setItem(LS_KEY, JSON.stringify(state)); } catch (e) {} }
try {
  const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  for (const k in saved) if (saved[k] && saved[k].verdict) state[k] = saved[k];
} catch (e) {}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function render() {
  const root = document.getElementById("cards");
  root.innerHTML = CARDS.map((c, i) => {
    const origin = c.origin.map(([t, b]) => `<h4>${esc(t)}</h4><p>${esc(b) || "（空）"}</p>`).join("");
    const distill = c.distill.map(([t, b]) => `<h4>${esc(t)}</h4><p>${esc(b) || "（空）"}</p>`).join("");
    const reasonOpts = Object.entries(REASONS)
      .map(([k, v]) => `<option value="${esc(k)}">${esc(k)} — ${esc(v)}</option>`).join("");
    return `<div class="card" id="card-${i}" data-type="${esc(c.type)}">
      <h3>${i + 1}. 工单 #${esc(c.task_id)}：${esc(c.title)}</h3>
      <div class="meta">${esc(c.meta.join("　|　"))}　(${esc(c.origin_note)})</div>
      <details><summary>工单原文（点击展开）</summary><div class="block">${origin}</div></details>
      <div class="distill block">${distill}</div>
      <div class="ops">
        <button data-v="approved" onclick="judge(${i},'approved')">✓ 通过</button>
        <button data-v="rejected" onclick="judge(${i},'rejected')">✗ 驳回</button>
        <button data-v="test" onclick="judge(${i},'test')">🗑 测试单</button>
        <span class="lbl">驳回理由</span>
        <select onchange="setReason(${i}, this.value)">
          <option value="">（未选）</option>${reasonOpts}</select>
        <input placeholder="备注 note（可选）" onchange="setNote(${i}, this.value)">
      </div></div>`;
  }).join("");
  updateBar();
}
function judge(i, v) {
  const c = CARDS[i], cur = state[c.point_id];
  state[c.point_id] = {
    verdict: (cur && cur.verdict === v) ? "" : v,
    reason: cur ? cur.reason : "", note: cur ? cur.note : "",
  };
  persistState();
  const card = document.getElementById("card-" + i);
  card.className = "card" + (state[c.point_id].verdict ? " v-" + state[c.point_id].verdict : "");
  card.querySelectorAll(".ops button").forEach(b =>
    b.className = b.dataset.v === state[c.point_id].verdict ? "on-" + b.dataset.v : "");
  if (v === "rejected" && state[c.point_id].verdict === "rejected") {
    card.querySelector("select").focus();  // 驳回必须选理由
  }
  updateBar();
}
function setReason(i, val) {
  const c = CARDS[i]; state[c.point_id] = state[c.point_id] || {verdict: "", reason: "", note: ""};
  state[c.point_id].reason = val;
  persistState();
}
function setNote(i, val) {
  const c = CARDS[i]; state[c.point_id] = state[c.point_id] || {verdict: "", reason: "", note: ""};
  state[c.point_id].note = val;
  persistState();
}
function updateBar() {
  const done = CARDS.filter(c => state[c.point_id] && state[c.point_id].verdict).length;
  document.getElementById("progress").textContent = `已判 ${done} / ${CARDS.length}`;
}
function filterType(v) {
  document.querySelectorAll(".card").forEach(el => {
    el.style.display = (!v || el.dataset.type === v) ? "" : "none";
  });
}
function bulkTest() {
  let n = 0;
  document.querySelectorAll(".card").forEach(el => {
    if (el.style.display === "none") return;
    const i = +el.id.slice(5), c = CARDS[i];
    if (!(state[c.point_id] && state[c.point_id].verdict)) { judge(i, "test"); n++; }
  });
  alert(`已将 ${n} 张可见未判定卡标为测试单`);
}
function csvCell(s) {
  s = String(s == null ? "" : s);
  return /[",\\n\\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function buildCsv() {
  const bad = CARDS.filter(c => state[c.point_id] && state[c.point_id].verdict === "rejected"
                               && !state[c.point_id].reason);
  if (bad.length) { alert("以下驳回的卡还没选理由：\\n" + bad.map(c => "#" + c.task_id).join(", ")); return null; }
  const lines = ["point_id,task_id,title,verdict,reason,note"];
  for (const c of CARDS) {
    const s = state[c.point_id] || {};
    lines.push([c.point_id, c.task_id, c.title, s.verdict || "", s.reason || "", s.note || ""]
      .map(csvCell).join(","));
  }
  return lines.join("\\r\\n");
}
// 保存到工作台：POST 直写导出目录的 review.csv（apply 按钮读它）；
// 以文件方式直开（无工作台来源）时接口不通，退回浏览器下载兜底
async function saveCsv() {
  const csv = buildCsv();
  if (csv === null) return;
  const dir = new URLSearchParams(location.search).get("dir") || "";
  try {
    const r = await fetch("/api/sink_save", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({dir, csv})});
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
    localStorage.removeItem(LS_KEY);  // 保存成功，本地备份让位给 CSV 真相
    alert("已保存到工作台（已判 " + (j.judged || 0) + "/" + (j.total || 0)
      + " 张）——回工作台点「③ 应用判定」");
  } catch (e) {
    const blob = new Blob(["\\ufeff" + csv], {type: "text/csv;charset=utf-8"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "review.csv"; a.click();
    alert("接口不通（非工作台入口打开），已退回浏览器下载 review.csv");
  }
}
render();
(function applyRestored() {
  CARDS.forEach((c, i) => {
    const s = state[c.point_id];
    if (!s || !s.verdict) return;
    const card = document.getElementById("card-" + i);
    if (!card) return;
    card.className = "card v-" + s.verdict;
    card.querySelectorAll(".ops button").forEach(b =>
      b.className = b.dataset.v === s.verdict ? "on-" + b.dataset.v : "");
    const sel = card.querySelector("select"); if (sel && s.reason) sel.value = s.reason;
    const inp = card.querySelector("input"); if (inp && s.note) inp.value = s.note;
  });
  updateBar();
})();
(function renderSkipped() {
  if (!SKIPPED.length) return;
  document.getElementById("skippedBox").style.display = "";
  const origin = s => [["问题描述", s.description], ["解决方式（工程师填写）", s.resolution_summary],
                       ["人类评论", s.comments_text]]
    .map(([t, b]) => `<h4>${esc(t)}</h4><p>${esc(b) || "（空）"}</p>`).join("");
  document.getElementById("skipList").innerHTML = SKIPPED.map(s =>
    `<div class="skip-card">
      <h3>工单 #${esc(s.task_id)}：${esc(s.title)}</h3>
      <div class="meta">${esc([s.project_name ? "项目: " + s.project_name : "",
        s.resolver ? "解决人: " + s.resolver : ""].filter(Boolean).join("　|　"))}</div>
      <details><summary>工单原文（点击展开）</summary><div class="block">${origin(s)}</div></details>
      <p class="skip-note">AI 判定：无知识可提，未入库。认为误杀 → 跑
        backfill_resolutions --task-id ${esc(s.task_id)} 单张重跑</p>
    </div>`).join("");
})();
(function initFilter() {
  const sel = document.getElementById("typeFilter");
  const types = [...new Set(CARDS.map(c => c.type).filter(Boolean))];
  sel.innerHTML = `<option value="">全部类型</option>` + types
    .map(t => `<option value="${esc(t)}">${esc(TYPES[t.toLowerCase()] || t)}(${esc(t)})</option>`).join("");
})();
</script>
</body>
</html>
"""


def _db_skipped_tickets() -> list[dict]:
    """AI 判空跳过的工单（solution_index_status='empty'），含给提炼器的同一份素材。

    0903 用户要求：判入库与判跳过两侧都要可见，才能验证沉淀准确率。
    """
    from ai.core.database import SessionLocal
    from ai.core.solution_sink import load_comments_text, resolver_name, _resolver_from_oplog
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT t.id, t.title, t.description, t.metadata_info, t.assigned_to, "
            "       t.project_name, t.task_type "
            "FROM tasks t "
            "WHERE JSON_EXTRACT(t.metadata_info, '$.solution_index_status') = 'empty' "
            "ORDER BY t.updated_at DESC")).mappings().all()
    finally:
        db.close()
    out = []
    for r in rows:
        meta = r.get("metadata_info") or {}
        if not isinstance(meta, dict):
            try:
                meta = json.loads(meta) if meta else {}
            except Exception:
                meta = {}
        comments, last_human = load_comments_text(r["id"])
        out.append({
            "task_id": r["id"], "title": r.get("title") or "",
            "description": r.get("description") or "",
            "resolution_summary": meta.get("resolution_summary") or "",
            "comments_text": comments,
            "resolver": (_resolver_from_oplog(r["id"])
                         or resolver_name(r.get("assigned_to") or "", last_human)),
            "project_name": r.get("project_name") or "",
            "ticket_type": getattr(r.get("task_type"), "value", r.get("task_type")) or "",
        })
    return out


def cmd_export(qc, col, status: str, limit: int) -> None:
    points = _scroll_tickets(qc, col, status)
    if limit:
        points = points[:limit]
    use_db = _db_available()
    skipped = _db_skipped_tickets() if use_db else []
    if not points and not skipped:
        print(f"没有 review_status={status} 的卡，也无量内空卡工单，无需导出")
        return

    outdir = os.path.join(_review_dir(), f"export_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(outdir, exist_ok=True)
    md_path = os.path.join(outdir, "cards.md")
    csv_path = os.path.join(outdir, "review.csv")
    html_path = os.path.join(outdir, "review.html")

    md = [f"# 工单沉淀审核材料（{len(points)} 张，review_status={status}）\n"
          f"\n审核标准建议：提炼卡是否忠实于原文（无幻觉）、问题/根因是否抓对、"
          f"是否测试单。\n"]
    rows, html_cards = [], []
    for p in points:
        pl = p.payload or {}
        tid = pl.get("task_id", "")
        src = _db_ticket(tid) if (use_db and tid) else None
        origin, distilled, origin_note = _card_material(pl, src)
        meta_bits = _card_meta_bits(pl, src)
        rows.append({
            "point_id": str(p.id), "task_id": tid,
            "title": pl.get("title", ""), "verdict": "", "reason": "", "note": "",
        })
        md.append(f"\n---\n\n## 工单 #{tid}：{pl.get('title', '')}\n")
        md.append(f"- point_id: `{p.id}`\n- " + "  | ".join(meta_bits) + "\n\n")
        md.append(f"### 工单原文（素材来源：{origin_note}）\n\n")
        for t, b in origin:
            md.append(_sec(t, b))
        md.append("### 提炼卡（LLM 输出，待审）\n\n")
        for t, b in distilled:
            md.append(_sec(t, b))
        html_cards.append({
            "point_id": str(p.id), "task_id": tid, "title": pl.get("title", ""),
            "meta": meta_bits, "origin_note": origin_note,
            "origin": origin, "distill": distilled,
            "type": (src or {}).get("ticket_type") or pl.get("ticket_type", ""),
        })

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("".join(md))
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["point_id", "task_id", "title", "verdict", "reason", "note"])
        w.writeheader()
        w.writerows(rows)
    if skipped:
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n\n# AI 判定跳过（无知识可提，未入库）：{len(skipped)} 张\n\n"
                    "抽检这些单该不该空——判空依据通常是测试单/敷衍解法/无解法/平台自身"
                    "问题。认为误杀 → `HF_HUB_OFFLINE=1 python -m "
                    "ai.tools.backfill_resolutions --task-id <工单号>` 单张重跑。\n")
            for s in skipped:
                f.write(f"\n---\n\n## 工单 #{s['task_id']}：{s['title']}\n"
                        f"- 项目: {s['project_name'] or '(无)'} | 解决人: {s['resolver']}\n\n")
                for t, b in (("问题描述", s["description"]),
                             ("解决方式（工程师填写）", s["resolution_summary"]),
                             ("人类评论", s["comments_text"])):
                    f.write(_sec(t, b))
    html_skipped = [{
        "task_id": s["task_id"], "title": s["title"],
        "project_name": s["project_name"], "resolver": s["resolver"],
        "description": (s["description"] or "")[:600],
        "resolution_summary": s["resolution_summary"],
        "comments_text": s["comments_text"],
    } for s in skipped]
    html = (_HTML_TEMPLATE
            .replace("__CARDS__", json.dumps(html_cards, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__REASONS__", json.dumps(_REASONS, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__TYPES__", json.dumps(_TYPE_CN, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__SKIPPED__", json.dumps(html_skipped, ensure_ascii=False).replace("</", "<\\/")))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已导出 {len(points)} 张："
          f"\n  ★ 交互标注页: {html_path}"
          f"（浏览器打开，点选判定，页内导出 CSV）"
          f"\n  阅读材料: {md_path}\n  空白标注表: {csv_path}")
    if skipped:
        print(f"  附: AI 判空跳过 {len(skipped)} 张已并入材料末尾（HTML 折叠区 + md 末章），供抽检误杀")
    print("标注后回写："
          f"\n  python -m ai.tools.review_resolutions --apply <标注后的review.csv> --reviewer <你的名字>")


def cmd_apply(qc, col, csv_path: str, reviewer: str) -> None:
    if not os.path.isfile(csv_path):
        print(f"!! 找不到 {csv_path}")
        sys.exit(1)
    from qdrant_client.models import PointIdsList
    approve_rows, delete_rows, skipped = [], [], 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            verdict = (row.get("verdict") or "").strip().lower()
            reason = (row.get("reason") or "").strip()
            note = (row.get("note") or "").strip()
            pid = (row.get("point_id") or "").strip()
            if not pid or not verdict:
                skipped += 1
                continue
            if verdict not in _VERDICTS:
                print(f"!! 未知 verdict={verdict!r}（合法值 {'/'.join(_VERDICTS)}），"
                      f"跳过 task #{row.get('task_id', '')}")
                skipped += 1
                continue
            if verdict == "rejected" and reason not in _REASONS:
                print(f"!! rejected 必须填 reason（合法值 {'/'.join(_REASONS)}），"
                      f"跳过 task #{row.get('task_id', '')}")
                skipped += 1
                continue
            row["_verdict"], row["_reason"], row["_note"] = verdict, reason, note
            (approve_rows if verdict == "approved" else delete_rows).append(row)
    if not approve_rows and not delete_rows:
        print("没有可执行的判定（verdict 未填或校验不过，见上方提示）")
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if approve_rows:
        qc.set_payload(
            collection_name=col,
            payload={"review_status": "approved",
                     "reviewed_by": reviewer, "reviewed_at": now},
            points=[r["point_id"] for r in approve_rows],
        )
        print(f"approved（放行可检索）: {len(approve_rows)} 张")
    if delete_rows:
        qc.delete(collection_name=col,
                  points_selector=PointIdsList(
                      points=[r["point_id"] for r in delete_rows]))
        print(f"rejected/test（已从 Qdrant 删除）: {len(delete_rows)} 张")
    if skipped:
        print(f"跳过未标注/非法行: {skipped}")

    # 审计留档：全部判定（含已删除的点）连同结构化理由追加到 JSONL，
    # 点删了理由也留得住——优化提炼 prompt / 训练审核 agent 吃这份记录
    import json as _json
    hist_path = os.path.join(_review_dir(), "review_history.jsonl")
    with open(hist_path, "a", encoding="utf-8") as f:
        for r in approve_rows + delete_rows:
            f.write(_json.dumps({
                "ts": now, "reviewer": reviewer,
                "task_id": r.get("task_id", ""), "title": r.get("title", ""),
                "point_id": r["point_id"], "verdict": r["_verdict"],
                "reason": r["_reason"], "note": r["_note"],
            }, ensure_ascii=False) + "\n")
    # 源文件随 apply 同步：approved 更新行、rejected/test 移除行——源文件是
    # 固有资料（换集合靠它搬运），只改 qdrant 会让源滞后到下次入库才追平
    try:
        from ai.core.ticket_card_store import load_source, save_source
        cards = load_source()
        touched = False
        for r in approve_rows:
            c = cards.get(r["point_id"])
            if c:
                c["payload"]["review_status"] = "approved"
                c["payload"]["reviewed_by"] = reviewer
                c["payload"]["reviewed_at"] = now
                touched = True
        for r in delete_rows:
            touched = cards.pop(r["point_id"], None) is not None or touched
        if touched:
            save_source(cards)
            from ai.core.ticket_card_store import _source_path
            print(f"固有资料源文件已随 apply 同步: {_source_path()}")
    except Exception as e:
        print(f"（源文件同步失败，不影响本次 apply: {e}）")


def cmd_export_skipped() -> None:
    """0903 空卡抽检通道：判空跳过的单不进审核材料，误杀无从发现——
    从 DB 捞 solution_index_status='empty' 的单出 skipped.md，人工扫一眼
    「这单真的没知识吗」；发现误判跑 backfill --task-id <id> 单张重跑。"""
    from ai.core.database import SessionLocal
    from ai.core.solution_sink import load_comments_text
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT t.id, t.title, t.description, t.project_name, t.updated_at, "
            "JSON_UNQUOTE(JSON_EXTRACT(t.metadata_info, '$.resolution_summary')) AS rs "
            "FROM tasks t "
            "WHERE JSON_EXTRACT(t.metadata_info, '$.solution_index_status') = 'empty' "
            "ORDER BY t.updated_at DESC")).mappings().all()
    finally:
        db.close()

    out = _review_dir()
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "skipped.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 空卡跳过工单抽检（{len(rows)} 张，判 empty 未入库）\n\n")
        f.write("AI 认为这些工单无知识可提（测试单/无解法/敷衍/平台自身问题等）。\n"
                "逐张看素材：确属无知识 → 忽略；发现误杀 → 单张重跑：\n"
                "`HF_HUB_OFFLINE=1 python -m ai.tools.backfill_resolutions --task-id <工单号>`\n")
        for r in rows:
            comments, _ = load_comments_text(r["id"])
            f.write(f"\n---\n\n## 工单 #{r['id']}：{r['title']}\n"
                    f"- 项目: {r['project_name'] or '(无)'} | 状态更新: {r['updated_at']}\n\n"
                    f"**描述**\n\n{(r['description'] or '')[:600]}\n\n"
                    f"**解决方式（工程师填写）**\n\n{(r['rs'] or '（未填）')}\n\n"
                    f"**人类评论**\n\n{comments or '（无）'}\n")
    print(f"已导出 {len(rows)} 张空卡工单 -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="工单沉淀人工审核工具")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true", help="按 review_status 统计")
    g.add_argument("--export", action="store_true", help="导出待审对照材料")
    g.add_argument("--export-skipped", action="store_true",
                   help="导出被判空卡跳过的工单清单（skipped.md），供抽检 AI 是否误杀")
    g.add_argument("--apply", metavar="CSV", help="回写标注结果")
    ap.add_argument("--pending-status", default="pending",
                    choices=["pending", "approved", ""],
                    help="export 导哪个状态（默认 pending；空串=全部）")
    ap.add_argument("--limit", type=int, default=0, help="export 最多导几张（0=不限）")
    ap.add_argument("--reviewer", default="manual", help="审核人（写入 reviewed_by）")
    args = ap.parse_args()

    load_dotenv()  # QDRANT_*/DATABASE_URL 可来自环境
    qc, col = _get_client()
    try:
        if args.status:
            cmd_status(qc, col)
        elif args.export:
            cmd_export(qc, col, args.pending_status or None, args.limit)
        elif args.export_skipped:
            cmd_export_skipped()
        else:
            cmd_apply(qc, col, args.apply, args.reviewer)
    finally:
        qc.close()


if __name__ == "__main__":
    main()
