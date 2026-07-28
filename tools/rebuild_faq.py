"""
Rebuild faq.md from faq_merged.jsonl + platform_faq.jsonl.

The current faq.md is missing:
- ~181 general FAQ entries (faq.001-faq.175 + faq.clarify.*)
- ~47 WeChat FAQ entries (xlsx.* — only 17/64 present)

This script reads the merged JSONL (which combines jsonl + xlsx + docx sources)
and generates a complete, well-organized faq.md.
"""
import json
import re
from pathlib import Path
from collections import defaultdict

DOCS_DIR = Path(r"D:\Code\OpenRobotService_Data\docs")
KB_DIR = Path(r"D:\Code\OpenRobotService_Data\kb")
MERGED_JSONL = DOCS_DIR / "faq_doc" / "faq_merged.jsonl"
PLATFORM_JSONL = DOCS_DIR / "platform_faq" / "platform_faq.jsonl"
OUTPUT = KB_DIR / "team" / "faq" / "faq.md"

# Manual chapter mapping for grouping faq.001-faq.110
CHAPTER_MAP = {
    "manual.1": ("1", "部署与安装", "USP 系统的部署、安装、License 激活及基础配置"),
    "manual.2": ("2", "车辆管理", "车辆上线、机器人类型配置、机器人操作及异常诊断"),
    "manual.3": ("3", "充电管理", "充电桩配置、充电策略及充电验收"),
    "manual.4": ("4", "外设配置", "自动门、输送线、电梯等外设的配置与对接"),
    "manual.5": ("5", "地图编辑", "地图的创建、编辑、预处理及生效"),
    "manual.6": ("6", "库位管理", "库位配置、盲取、库区避障及多层库位"),
    "manual.7": ("7", "载具管理", "载具类型配置及库位载具参数"),
    "manual.8": ("8", "偏移量", "偏移量配置与导入"),
    "manual.9": ("9", "任务管理", "移动/充电/搬运任务下发及任务模拟器"),
    "manual.10": ("10", "流程编排", "流程模板的创建与管理"),
    "manual.11": ("11", "统计与系统", "任务统计、服务器监控、轨迹回放"),
    "manual.appendix": ("附录", "附录", "服务器配置档位参考"),
}

def get_chapter(source_ids):
    """Map source_ids to chapter key."""
    for sid in source_ids:
        for prefix, info in CHAPTER_MAP.items():
            if sid.startswith(prefix):
                return info
    return ("其他", "其他", "未分类")

def load_jsonl(path):
    entries = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries

def clean_answer(text):
    """Clean up answer text for markdown."""
    if not text:
        return ""
    # Remove excessive newlines
    text = re.sub(r'\n{4,}', '\n\n', text)
    # Ensure trailing newline
    text = text.strip()
    return text

def generate():
    entries = load_jsonl(MERGED_JSONL)
    platform_entries = load_jsonl(PLATFORM_JSONL)

    # Split entries by type
    manual_faqs = []   # faq.001-faq.110 (manual-referenced)
    practical_faqs = []  # faq.111-faq.175 (with detailed answers)
    clarify_faqs = []   # faq.clarify.*
    xlsx_faqs = []      # xlsx.*

    for e in entries:
        fid = e.get("faq_id", "")
        if fid.startswith("faq.clarify."):
            clarify_faqs.append(e)
        elif fid.startswith("xlsx."):
            xlsx_faqs.append(e)
        elif fid.startswith("faq."):
            num = int(fid.replace("faq.", ""))
            if num <= 110:
                manual_faqs.append(e)
            else:
                practical_faqs.append(e)

    print(f"Manual FAQs: {len(manual_faqs)}")
    print(f"Practical FAQs: {len(practical_faqs)}")
    print(f"Clarify FAQs: {len(clarify_faqs)}")
    print(f"XLSX FAQs: {len(xlsx_faqs)}")
    print(f"Platform FAQs: {len(platform_entries)}")

    # Build markdown
    lines = []
    lines.append("# USP 调度平台 — 常见问题 FAQ")
    lines.append("")
    lines.append("> 合并来源：faq_index_with_clarification.jsonl + USP FAQ.xlsx + USP FAQ手册.docx + platform_faq.jsonl")
    lines.append("> 归属：👥 团队知识 — USP 调度平台 FAQ")
    lines.append("> 入库时间：2026-07")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 1: 平台 FAQ ──
    lines.append("## 一、平台 FAQ（摇人吧服务号）")
    lines.append("")
    lines.append("> 来源：platform_faq.jsonl")
    lines.append("")

    for e in platform_entries:
        q = e.get("question", "")
        a = clean_answer(e.get("answer", ""))
        lines.append(f"### {q}")
        lines.append("")
        lines.append(a)
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── Section 2: USP 操作 FAQ (by manual chapter) ──
    lines.append("## 二、USP 操作 FAQ（按操作手册章节）")
    lines.append("")
    lines.append("> 来源：faq_index_with_clarification.jsonl（结构化 FAQ，答案详见对应操作手册章节）")
    lines.append("")

    # Group by chapter
    chapter_groups = defaultdict(list)
    for e in manual_faqs:
        source_ids = e.get("source_ids", [])
        ch_num, ch_name, ch_desc = get_chapter(source_ids)
        chapter_groups[(ch_num, ch_name, ch_desc)].append(e)

    # Sort chapters
    def ch_sort_key(item):
        ch_num = item[0][0]
        try:
            return int(ch_num)
        except ValueError:
            return 99

    sorted_chapters = sorted(chapter_groups.items(), key=ch_sort_key)

    for (ch_num, ch_name, ch_desc), faqs in sorted_chapters:
        lines.append(f"### {ch_num}. {ch_name}")
        lines.append("")
        if ch_desc:
            lines.append(f"> {ch_desc}")
            lines.append("")

        for e in faqs:
            q = e.get("question", "")
            source_ids = e.get("source_ids", [])
            aid = e.get("faq_id", "")
            # Show source reference for entries without direct answers
            refs = ", ".join(source_ids) if source_ids else ""
            ref_str = f" 📖 详见：{refs}" if refs else ""
            lines.append(f"- **{q}**{ref_str}")

        lines.append("")

    lines.append("---")
    lines.append("")

    # ── Section 3: Clarify entries (question routing) ──
    lines.append("## 三、USP 场景入口（问题路由）")
    lines.append("")
    lines.append("> 来源：faq_index_with_clarification.jsonl（通用问题 → 具体分类的入口）")
    lines.append("")

    for e in clarify_faqs:
        q = e.get("question", "")
        aid = e.get("faq_id", "").replace("faq.clarify.", "")
        lines.append(f"- **{q}** → 详见对应章节 FAQ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 4: USP 实战 FAQ (with detailed answers) ──
    lines.append("## 四、USP 实战 FAQ（实施与运维）")
    lines.append("")
    lines.append("> 来源：faq_index_with_clarification.jsonl（含详细答案的实战问题）")
    lines.append("")

    for e in practical_faqs:
        q = e.get("question", "")
        a = clean_answer(e.get("answer", ""))
        aid = e.get("faq_id", "")

        lines.append(f"### {q}")
        lines.append("")

        if a:
            lines.append(a)
            lines.append("")
        else:
            source_ids = e.get("source_ids", [])
            if source_ids:
                refs = ", ".join(source_ids)
                lines.append(f"📖 详见操作手册章节：{refs}")
                lines.append("")

    lines.append("---")
    lines.append("")

    # ── Section 5: USP 客户问答 (WeChat FAQ) ──
    lines.append("## 五、USP 客户问答（微信群真实问题）")
    lines.append("")
    lines.append("> 来源：USP FAQ.xlsx（64 条真实客户/实施人员微信群问答）")
    lines.append("")

    def xlsx_sort_key(e):
        fid = e.get("faq_id", "xlsx.999")
        try:
            return int(fid.replace("xlsx.", ""))
        except ValueError:
            return 999

    xlsx_faqs_sorted = sorted(xlsx_faqs, key=xlsx_sort_key)

    for e in xlsx_faqs_sorted:
        q = e.get("question", "")
        a = clean_answer(e.get("answer", ""))
        fid = e.get("faq_id", "")

        lines.append(f"### {q}")
        lines.append("")

        if a:
            # Check if answer looks like chat log
            if "**" in a or len(a) > 200:
                lines.append(a)
            else:
                lines.append(a)
            lines.append("")
        else:
            lines.append("> ⚠️ 待补充：此问题暂无标准答案")
            lines.append("")

    # Write output
    content = "\n".join(lines)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")

    total = len(platform_entries) + len(manual_faqs) + len(clarify_faqs) + len(practical_faqs) + len(xlsx_faqs_sorted)
    print(f"\nTotal entries: {total}")
    print(f"Written to: {OUTPUT}")
    print(f"File size: {OUTPUT.stat().st_size:,} bytes")
    print(f"Lines: {len(lines)}")

if __name__ == "__main__":
    generate()
