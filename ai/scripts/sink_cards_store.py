# -*- coding: utf-8 -*-
"""工单沉淀卡本地留存：export 审核材料 → kb/sink_cards/*.md。

数据流：工作台沉淀页签每周 导出→审核→应用（应用回写生产库，本地库不留卡）。
本模块把各批 export 里的 cards.md ∩ review.csv 中**判定 approved** 的卡落成
md 文件——通过的才会进检索库当知识，驳回/测试单不留档。规则：
- 同 point_id 多批次导出时，后批次判定覆盖前批次
- 文件内标注判定状态；approved 另有检索价值，rejected/test 仅作审核留档
- 输出目录 kb/sink_cards/ 不属于任何 domain，ingest_all 不会扫到它
"""
import csv
import re
from pathlib import Path

SINK_ROOT = Path(r"D:/Code/OpenRobotService_Data/review/ticket_resolutions")
OUT_DIR = Path(r"D:/Code/OpenRobotService_Data/kb/sink_cards")
_PID_RE = re.compile(r"- point_id: `([0-9a-f\-]{8,})`")

_VERDICT_CN = {"approved": "✅ 通过", "rejected": "❌ 驳回", "test": "🧪 测试单"}
_VERDICT_TAG = {"approved": "✅", "rejected": "❌", "test": "🧪"}


def parse_cards(cards_md: str) -> dict:
    """cards.md → {point_id: 卡片块文本}。块从 `## 工单` 行起，含提炼卡全段。"""
    cards = {}
    pid = None
    buf = []
    for ln in cards_md.splitlines():
        if ln.startswith("## 工单 "):
            if pid:
                cards[pid] = "\n".join(buf).strip()
            pid, buf = None, [ln]
            continue
        if not buf:
            continue
        if pid is None:
            m = _PID_RE.search(ln)
            if m:
                pid = m.group(1)
        buf.append(ln)
    if pid:
        cards[pid] = "\n".join(buf).strip()
    return cards


def exports_signature(sink_root: Path = SINK_ROOT) -> str:
    """全部导出批次的 (目录名, review.csv mtime, cards.md mtime) 签名。"""
    parts = []
    for exp in sorted(sink_root.glob("export_*")):
        for f in (exp / "review.csv", exp / "cards.md"):
            if f.is_file():
                parts.append(f"{f}:{f.stat().st_mtime_ns}")
    return "|".join(parts)


def sync_sink_cards(sink_root: Path = SINK_ROOT, out_dir: Path = OUT_DIR) -> dict:
    """全部导出批次 ∩ 已判定卡 → 落成 md（含判定标注）；判定翻转时重建。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    best = {}  # point_id -> (exp_name, verdict, reason, note, block)
    for exp in sorted(sink_root.glob("export_*")):
        cards_p, csv_p = exp / "cards.md", exp / "review.csv"
        if not cards_p.is_file() or not csv_p.is_file():
            continue
        verdicts = {}
        with open(csv_p, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                pid = (row.get("point_id") or "").strip()
                v = (row.get("verdict") or "").strip().lower()
                if pid and v:
                    verdicts[pid] = (v, (row.get("reason") or "").strip(),
                                     (row.get("note") or "").strip())
        for pid, block in parse_cards(cards_p.read_text(encoding="utf-8")).items():
            vd = verdicts.get(pid)
            if vd is not None:
                best[pid] = (exp.name, vd[0], vd[1], vd[2], block)

    # 只留存 approved——通过的才会进检索库当知识；驳回/测试单不留档
    best = {pid: t for pid, t in best.items() if t[1] == "approved"}
    keep_suffixes = {pid[:12] for pid in best}

    removed = 0
    for f in sorted(out_dir.glob("*.md")):
        if f.stem.rsplit("_", 1)[-1] not in keep_suffixes:
            f.unlink()
            removed += 1

    stats = {"approved": 0, "rejected": 0, "test": 0}
    written = 0
    for pid, (exp_name, v, reason, note, block) in best.items():
        m = re.search(r"^## (工单 #\d+：.+)$", block, re.MULTILINE)
        title = m.group(1).strip() if m else f"工单沉淀卡 {pid[:8]}"
        tid = re.search(r"工单 #(\d+)", title)
        fname = (f"工单{tid.group(1)}_{pid[:12]}.md" if tid
                 else f"card_{pid[:12]}.md")
        body = re.sub(r"^## 工单[^\n]*\n", "", block, count=1).strip()
        verdict_line = _VERDICT_CN.get(v, v)
        if v == "rejected" and reason:
            verdict_line += f"（{reason}" + (f"：{note}" if note else "") + "）"
        elif v == "rejected" and note:
            verdict_line += f"（{note}）"
        content = (f"# {title}\n\n"
                   f"- 判定: {verdict_line}\n"
                   f"- 审核批次: {exp_name}\n\n{body}\n")
        (out_dir / fname).write_text(content, encoding="utf-8")
        stats[v] = stats.get(v, 0) + 1
        written += 1
    return {"written": written, "removed": removed, **stats}
