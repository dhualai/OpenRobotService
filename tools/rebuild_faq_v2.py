"""
Rebuild faq.md v2 — properly integrate docx Q&A images.

The docx file (USP FAQ手册.docx) contains Q&A pairs with real screenshots
embedded as media/image2.png through media/image23.png. These need to be
integrated into faq.md alongside the JSONL/XLSX data.

Strategy:
1. Parse docx → extract Q&A pairs with their images
2. Match docx Q&A to faq_merged.jsonl entries by question overlap
3. For matched pairs: use docx answer (better formatting) + docx images
4. For unmatched docx: add as new entries
5. For unmatched jsonl: keep as-is
6. Platform FAQ: keep as-is
"""
import json
import re
import subprocess
from pathlib import Path
from collections import defaultdict

DOCS_DIR = Path(r"D:\Code\OpenRobotService_Data\docs")
KB_DIR = Path(r"D:\Code\OpenRobotService_Data\kb")
MERGED_JSONL = DOCS_DIR / "faq_doc" / "faq_merged.jsonl"
PLATFORM_JSONL = DOCS_DIR / "platform_faq" / "platform_faq.jsonl"
DOCX_PATH = DOCS_DIR / "faq_doc" / "USP FAQ手册.docx"
OUTPUT = KB_DIR / "team" / "faq" / "faq.md"

def load_jsonl(path):
    entries = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries

def parse_docx(docx_path):
    """Parse docx → list of {question, answer, images} using pandoc."""
    text = subprocess.run(
        ["pandoc", str(docx_path), "-f", "docx", "-t", "markdown", "--wrap=none"],
        capture_output=True, encoding="utf-8", check=True,
    ).stdout

    # Remove TOC and revision history
    text = re.sub(r'^# \*\*目 录\*\*.*?(?=^# 1\s)', '', text, flags=re.MULTILINE | re.DOTALL)

    # Split by chapter headings
    # Find all **Q... blocks
    qa_blocks = re.split(r'\n(?=\*\*Q[:：])', text)

    entries = []
    for block in qa_blocks:
        if not block.strip():
            continue
        if not re.search(r'\*\*Q[:：]', block):
            continue

        # Extract question
        q_match = re.search(r'\*\*Q[:：]\s*(.+?)\*\*\s*$', block, re.MULTILINE)
        if not q_match:
            continue
        question = q_match.group(1).strip()

        # Extract answer (everything after the Q line, until next Q or end)
        # Split off the Q line
        q_end = q_match.end()
        answer_block = block[q_end:]

        # Extract answer text (skip the **A:** marker)
        a_match = re.search(r'(?:\*\*)?A[:：]\s*\*?\*?(.*)', answer_block, re.DOTALL)
        answer = ''
        if a_match:
            answer = a_match.group(1).strip()

        # Extract images (filter out tiny separator images — image1.png at 0.03in height)
        images = []
        for img_m in re.finditer(
            r'!\[(?:descript)?\]\(media/([^)]+)\)\{[^}]*height="([^"]+)"[^}]*\}', answer_block
        ):
            fname, height_str = img_m.group(1), img_m.group(2)
            try:
                h = float(height_str.replace('in', '').strip())
                if h < 0.1:  # Filter tiny separator lines
                    continue
            except ValueError:
                pass
            if fname not in images:
                images.append(fname)

        # Also catch images without height (like `![](media/image16.png){width="..."}`)
        for img_m in re.finditer(
            r'!\[(?:descript)?\]\(media/([^)]+)\)\{[^}]*width="([^"]+)"[^}]*\}', answer_block
        ):
            fname = img_m.group(1)
            if fname not in images and fname != 'image1.png':  # image1 is always the separator
                # Check if this image also appears with height attribute
                has_height = any(fname == i for i in images)
                if not has_height:
                    images.append(fname)

        # Clean answer: remove image markdown from answer text
        answer_clean = re.sub(r'!\[(?:descript)?\]\(media/[^)]+\)\{[^}]*\}', '', answer).strip()
        answer_clean = re.sub(r'\n{3,}', '\n\n', answer_clean)
        answer_clean = re.sub(r'<!-- -->', '', answer_clean)

        entries.append({
            'question': question,
            'answer': answer_clean,
            'images': images,
            'source': 'docx',
        })

    print(f"  [DOCX] {len(entries)} Q&A pairs extracted")
    with_images = sum(1 for e in entries if e['images'])
    total_images = sum(len(e['images']) for e in entries)
    print(f"  [DOCX] {with_images} entries have images ({total_images} total images)")
    return entries

def tokenize(text):
    clean = re.sub(r'[^\w一-鿿]', '', text)
    tokens = set()
    for n in [2, 3, 4]:
        for i in range(len(clean) - n + 1):
            tokens.add(clean[i:i + n])
    return tokens

def find_match(question, candidates):
    """Find best matching candidate by token overlap. Returns (index, ratio) or (None, 0)."""
    q_tokens = tokenize(question)
    if len(q_tokens) < 3:
        return None, 0

    best_idx, best_ratio = None, 0
    for i, c in enumerate(candidates):
        c_tokens = tokenize(c.get('question', ''))
        overlap = len(q_tokens & c_tokens)
        denom = min(len(q_tokens), len(c_tokens))
        ratio = overlap / denom if denom > 0 else 0
        if ratio > best_ratio and ratio > 0.30:
            best_ratio = ratio
            best_idx = i

    return best_idx, best_ratio

def generate():
    # Load all sources
    merged = load_jsonl(MERGED_JSONL)
    platform = load_jsonl(PLATFORM_JSONL)
    docx_entries = parse_docx(DOCX_PATH)

    # Split merged entries
    manual_faqs = []
    practical_faqs = []
    clarify_faqs = []
    xlsx_faqs = []

    for e in merged:
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

    # Match docx entries against practical FAQs (faq.111-faq.175)
    matched_docx = set()
    matched_practical = set()

    for di, de in enumerate(docx_entries):
        idx, ratio = find_match(de['question'], practical_faqs)
        if idx is not None and ratio > 0.30:
            # Match found — docx answer takes priority, merge images
            matched_docx.add(di)
            matched_practical.add(idx)
            pe = practical_faqs[idx]
            # Use docx answer if it's more complete
            if de['answer'] and len(de['answer']) > len(pe.get('answer', '')):
                pe['answer'] = de['answer']
            # Add docx images
            pe['_docx_images'] = de['images']
            pe['_docx_matched'] = True
        # If no match, this docx entry will be added as new

    # Unmatched docx entries
    new_from_docx = [de for di, de in enumerate(docx_entries) if di not in matched_docx]
    print(f"  [MATCH] {len(matched_docx)} docx entries matched to practical FAQs")
    print(f"  [MATCH] {len(new_from_docx)} new entries from docx")

    # Also try matching unmatched docx against manual_faqs
    matched_manual = set()
    new_docx_final = []
    for de in new_from_docx:
        idx, ratio = find_match(de['question'], manual_faqs)
        if idx is not None and ratio > 0.30:
            matched_manual.add(idx)
            me = manual_faqs[idx]
            me['answer'] = de['answer']  # docx answer
            me['_docx_images'] = de['images']
            me['_docx_matched'] = True
        else:
            new_docx_final.append(de)

    print(f"  [MATCH] {len(matched_manual)} docx entries matched to manual FAQs")
    print(f"  [MATCH] {len(new_docx_final)} truly new entries from docx")

    # ── Build markdown ──
    lines = []
    lines.append("# USP 调度平台 — 常见问题 FAQ")
    lines.append("")
    lines.append("> 合并来源：faq_index_with_clarification.jsonl + USP FAQ.xlsx + USP FAQ手册.docx + platform_faq.jsonl")
    lines.append("> 归属：👥 团队知识 — USP 调度平台 FAQ")
    lines.append("> 入库时间：2026-07")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 1: Platform FAQ ──
    lines.append("## 一、平台 FAQ（摇人吧服务号）")
    lines.append("")
    lines.append("> 来源：platform_faq.jsonl")
    lines.append("")
    for e in platform:
        q = e.get("question", "")
        a = (e.get("answer", "") or "").strip()
        lines.append(f"### {q}")
        lines.append("")
        if a:
            lines.append(a)
            lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 2: Manual FAQ (by chapter) ──
    CHAPTER_MAP = {
        "manual.1": ("1", "部署与安装"),
        "manual.2": ("2", "车辆管理"),
        "manual.3": ("3", "充电管理"),
        "manual.4": ("4", "外设配置"),
        "manual.5": ("5", "地图编辑"),
        "manual.6": ("6", "库位管理"),
        "manual.7": ("7", "载具管理"),
        "manual.8": ("8", "偏移量"),
        "manual.9": ("9", "任务管理"),
        "manual.10": ("10", "流程编排"),
        "manual.11": ("11", "统计与系统"),
        "manual.appendix": ("附录", "附录"),
    }

    def get_ch(source_ids):
        for sid in source_ids:
            for prefix, info in CHAPTER_MAP.items():
                if sid.startswith(prefix):
                    return info
        return ("其他", "其他")

    chapter_groups = defaultdict(list)
    for e in manual_faqs:
        ch = get_ch(e.get("source_ids", []))
        chapter_groups[ch].append(e)

    def ch_sort(item):
        try:
            return int(item[0][0])
        except ValueError:
            return 99

    lines.append("## 二、USP 操作 FAQ（按操作手册章节）")
    lines.append("")
    lines.append("> 来源：faq_index_with_clarification.jsonl（结构化 FAQ，答案详见对应操作手册章节）")
    lines.append("")

    for (ch_num, ch_name), faqs in sorted(chapter_groups.items(), key=ch_sort):
        lines.append(f"### {ch_num}. {ch_name}")
        lines.append("")
        for e in faqs:
            q = e.get("question", "")
            source_ids = e.get("source_ids", [])
            refs = ", ".join(source_ids) if source_ids else ""

            # Check if matched with docx
            if e.get('_docx_matched'):
                a = (e.get("answer", "") or "").strip()
                docx_imgs = e.get('_docx_images', [])
                lines.append(f"#### {q}")
                lines.append("")
                if a:
                    lines.append(a)
                    lines.append("")
                if docx_imgs:
                    for img in docx_imgs:
                        lines.append(f"![{img}](media/{img})")
                        lines.append("")
                if refs:
                    lines.append(f"> 📖 详见操作手册：{refs}")
                    lines.append("")
            else:
                ref_str = f" 📖 详见：{refs}" if refs else ""
                lines.append(f"- **{q}**{ref_str}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── Section 3: Clarify ──
    lines.append("## 三、USP 场景入口（问题路由）")
    lines.append("")
    for e in clarify_faqs:
        q = e.get("question", "")
        lines.append(f"- **{q}** → 详见对应章节 FAQ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 4: Practical FAQ with docx images ──
    lines.append("## 四、USP 实战 FAQ（实施与运维，含操作截图）")
    lines.append("")
    lines.append("> 来源：USP FAQ手册.docx + faq_index_with_clarification.jsonl")
    lines.append("")

    for e in practical_faqs:
        q = e.get("question", "")
        a = (e.get("answer", "") or "").strip()
        docx_imgs = e.get('_docx_images', [])
        source_ids = e.get("source_ids", [])

        lines.append(f"### {q}")
        lines.append("")

        if a:
            lines.append(a)
            lines.append("")

        if docx_imgs:
            for img in docx_imgs:
                lines.append(f"![{img}](media/{img})")
                lines.append("")

        if not a and not docx_imgs and source_ids:
            refs = ", ".join(source_ids)
            lines.append(f"📖 详见操作手册章节：{refs}")
            lines.append("")

    lines.append("---")
    lines.append("")

    # ── Section 5: New DOCX-only entries ──
    if new_docx_final:
        lines.append("## 五、补充 FAQ（来自 USP FAQ 手册）")
        lines.append("")
        lines.append("> 来源：USP FAQ手册.docx（独有问题，未与其他来源重叠）")
        lines.append("")
        for de in new_docx_final:
            lines.append(f"### {de['question']}")
            lines.append("")
            if de['answer']:
                lines.append(de['answer'])
                lines.append("")
            if de['images']:
                for img in de['images']:
                    lines.append(f"![{img}](media/{img})")
                    lines.append("")
    else:
        # No new docx entries
        lines.append("## 五、USP 客户问答（微信群真实问题）")
        lines.append("")
        lines.append("> 来源：USP FAQ.xlsx（真实客户/实施人员微信群问答）")
        lines.append("")

        def xlsx_sort_key(e):
            fid = e.get("faq_id", "xlsx.999")
            try:
                return int(fid.replace("xlsx.", ""))
            except ValueError:
                return 999

        for e in sorted(xlsx_faqs, key=xlsx_sort_key):
            q = e.get("question", "")
            a = (e.get("answer", "") or "").strip()
            lines.append(f"### {q}")
            lines.append("")
            if a:
                lines.append(a)
                lines.append("")
            else:
                lines.append("> ⚠️ 待补充：此问题暂无标准答案")
                lines.append("")

    # If new_docx_final exists, XLSX becomes section 6
    if new_docx_final:
        lines.append("---")
        lines.append("")
        lines.append("## 六、USP 客户问答（微信群真实问题）")
        lines.append("")
        lines.append("> 来源：USP FAQ.xlsx（真实客户/实施人员微信群问答）")
        lines.append("")

        def xlsx_sort_key(e):
            fid = e.get("faq_id", "xlsx.999")
            try:
                return int(fid.replace("xlsx.", ""))
            except ValueError:
                return 999

        for e in sorted(xlsx_faqs, key=xlsx_sort_key):
            q = e.get("question", "")
            a = (e.get("answer", "") or "").strip()
            lines.append(f"### {q}")
            lines.append("")
            if a:
                lines.append(a)
                lines.append("")
            else:
                lines.append("> ⚠️ 待补充：此问题暂无标准答案")
                lines.append("")

    # Write
    content = "\n".join(lines)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")

    print(f"\nTotal lines: {len(lines)}")
    print(f"File size: {OUTPUT.stat().st_size:,} bytes")
    print(f"Written to: {OUTPUT}")

if __name__ == "__main__":
    generate()
