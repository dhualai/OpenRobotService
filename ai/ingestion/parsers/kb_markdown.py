"""
统一 Markdown 知识库入库器 — KBDomainIngester

按 domain 读取 kb/{domain}/ 下所有 .md 文件，按 ## 标题切片入库。
每个 chunk 的 payload 携带 domain / sub_domain / title / source_file 字段，
检索时可通过 sub_domain 做 Qdrant payload filter 精准过滤。

切分策略（按 sub_domain）：
  - faq           → 按 ## 分大节 → 按 ### 切 QA（一 QA 一 chunk）
  - usp_faq       → 同 faq 策略（Q&A 细粒度切分）
  - usp_manual    → 按 ## 切块（保留标题行），提取图片，§2.1 按 ### 细切
  - cheduan_errors → 按表格行切分错误码
  - 其他          → 按 ## 切块，超长段落（>3000 字符）按 ### 细切

sub_domain 自动推断规则：
    kb/team/faq/faq.md          → sub_domain = "faq"
    kb/team/usp_faq/xxx.md      → sub_domain = "usp_faq"
    kb/team/usp_manual/xxx.md   → sub_domain = "usp_manual"
    kb/company/cheduan_errors/  → sub_domain = "cheduan_errors"
"""
import re
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field

from ai.config import _KB_DIR, write_active_collection_for, get_active_collection_for
from ai.ingestion.base import BaseIngester, Chunk


@dataclass
class KBEntry:
    """一条解析后的知识条目"""
    title: str
    content: str
    sub_domain: str
    source_file: str
    order: int
    images: List[str] = field(default_factory=list)
    # 仅 usp_manual 使用
    section: str = ""
    chapter: str = ""
    # 仅 cheduan_errors 使用
    error_code: str = ""
    category: str = ""
    level: str = ""
    description_cn: str = ""
    description_en: str = ""
    solution_cn: str = ""
    # 仅诊断知识卡片（usp_cards）使用
    card_id: str = ""
    card_domain: str = ""
    severity: str = ""
    symptom_terms: List[str] = field(default_factory=list)
    related_cards: List[str] = field(default_factory=list)
    anchor_error_codes: List[str] = field(default_factory=list)
    embed_text: str = ""


class KBDomainIngester(BaseIngester[KBEntry]):
    """
    按 domain 读取 kb/{domain}/ 下所有 .md 文件，按 ## 切片入库。

    根据 sub_domain 自动选择切分策略：
      - faq: 一 QA 一 chunk
      - usp_manual: 保留标题行 + 提取图片 + §2.1 细切
      - 其他: 通用 ## 切分
    """

    def __init__(self, domain: str, kb_root: Optional[Path] = None):
        if domain not in ("industry", "company", "team", "project", "personal"):
            raise ValueError(f"Unknown domain: {domain}")

        self._domain = domain
        self._kb_root = (kb_root or _KB_DIR).resolve()
        self._domain_dir = self._kb_root / domain

        if self._domain_dir.is_dir():
            self.source_paths = sorted(self._domain_dir.rglob("*.md"))
        else:
            self.source_paths = []

        self.collection_prefix = domain
        self.collection_type = domain
        self.rebuild = True
        self.verbose = True

        self.pointer_reader = lambda: get_active_collection_for(domain)
        self.pointer_writer = lambda name: write_active_collection_for(domain, name)

    # ═══════════════════════════════════════════════════════════
    # 解析入口
    # ═══════════════════════════════════════════════════════════

    def parse(self) -> List[KBEntry]:
        entries: List[KBEntry] = []

        for md_file in self.source_paths:
            if not md_file.is_file():
                continue

            rel = md_file.relative_to(self._domain_dir)
            # 与 source_file 一致统一正斜杠：Windows 下 Path 给反斜杠，会导致
            # sub_domain 与消费端（pipeline._sub_labels 等）的键匹配错位
            sub_domain = str(rel.parent).replace("\\", "/") if str(rel.parent) != "." else ""
            source_file = str(rel).replace("\\", "/")

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception as e:
                self._log(f"[WARN] 无法读取 {md_file}: {e}")
                continue

            # 按文件名模式选择切分策略（兼容新旧目录结构）
            _rel = source_file.lower()
            if re.match(r"^kb-[a-z]+-\d+\.md$", Path(source_file).name.lower()):
                file_entries = self._split_kb_cards(content, source_file)
            elif "faq" in _rel:
                file_entries = self._split_faq(content, sub_domain, source_file)
            elif "manual" in _rel:
                file_entries = self._split_manual(content, sub_domain, source_file)
            elif sub_domain in ("cheduan_errors",) or "cheduan_errors" in _rel:
                file_entries = self._split_cheduan_errors(content, sub_domain, source_file)
            else:
                file_entries = self._split_generic(content, sub_domain, source_file)

            entries.extend(self._split_oversize(file_entries))

        # 兜底切分会产生同 order 子块，统一按最终顺序重排（order 只用于
        # chunk_id 唯一性与展示排序，重排零风险；每次入库都是新集合）
        for i, e in enumerate(entries):
            e.order = i
        return entries

    # 超长块兜底阈值：超过则按行切分（翻译巨表 17-79KB 实测向量检索等于抽签）
    _OVERSIZE_CHARS = 3000

    # 整节/整文档阈值：≤2000 字不细切。手册章节实测尺寸：6.5 多层库位 1673、
    # 9.5 任务操作 1354——细切会把「前提/步骤」切成 1-2 行碎片 chunk，正文与
    # 标题分离，召回后拼不回完整流程；2.3 机器人操作 5192、4.3 输送线 4425
    # 这类大章节仍需细切。
    _SECTION_WHOLE_CHARS = 2000

    def _split_oversize(self, file_entries: List[KBEntry]) -> List[KBEntry]:
        """所有策略的最后防线：超长 chunk 按行切，防止嵌入信号被稀释。

        - 表格感知：块开头有表头（|---| 分隔行）时，表头复制到每个子块，
          避免切出无头表格
        - 围栏感知：切点只落在 ``` 围栏外，代码块不拦腰截断
        - 标题带序号（· 2/5），检索展示可辨识同组子块
        """
        out: List[KBEntry] = []
        for e in file_entries:
            if len(e.content) <= self._OVERSIZE_CHARS:
                out.append(e)
                continue
            lines = e.content.splitlines()
            # 表头：块前 6 行内找 |--- 分隔行，表头 = 首个非空行到该行
            header: list[str] = []
            for i, ln in enumerate(lines[:6]):
                if re.match(r"^\s*\|[\s:|-]+\|\s*$", ln):
                    # 从第一行（## 标题）到分隔行整体作为表头前缀
                    start = 0
                    for j in range(i, -1, -1):
                        if lines[j].strip().startswith("|"):
                            start = j
                        else:
                            break
                    header = lines[start:i + 1]
                    break
            # 行数切分：每子块 ≈ _OVERSIZE_CHARS 字
            per = max(20, self._OVERSIZE_CHARS // 40)  # 估算行数上限（中文行较短）
            parts: list[list[str]] = []
            cur: list[str] = []
            cur_chars = 0
            in_fence = False
            for ln in lines:
                if ln.strip().startswith("```"):
                    in_fence = not in_fence
                cur.append(ln)
                cur_chars += len(ln) + 1
                if cur_chars >= self._OVERSIZE_CHARS and not in_fence and len(cur) >= per // 4:
                    parts.append(cur)
                    cur, cur_chars = [], 0
            if cur:
                parts.append(cur)
            n = len(parts)
            if n <= 1:
                out.append(e)
                continue
            for i, p in enumerate(parts, 1):
                body = list(p)
                if i > 1 and header:
                    body = header + [""] + body
                out.append(KBEntry(
                    title=f"{e.title} · {i}/{n}" if n > 1 else e.title,
                    content="\n".join(body).strip(),
                    sub_domain=e.sub_domain, source_file=e.source_file,
                    order=e.order + i - 1,
                    images=e.images, section=e.section, chapter=e.chapter,
                    error_code=e.error_code, category=e.category, level=e.level,
                    description_cn=e.description_cn, solution_cn=e.solution_cn,
                    card_id=e.card_id, card_domain=e.card_domain,
                    severity=e.severity, symptom_terms=e.symptom_terms,
                    related_cards=e.related_cards, anchor_error_codes=e.anchor_error_codes,
                    # 切开后嵌入文本必须跟着变小，否则向量仍是稀释的整块全文
                    embed_text=None,
                ))
        return out

    # ═══════════════════════════════════════════════════════════
    # 策略 1: FAQ — 按 ### 切 QA（一 QA 一 chunk）
    # ═══════════════════════════════════════════════════════════

    def _split_faq(self, content: str, sub_domain: str, source_file: str) -> List[KBEntry]:
        """FAQ：先按 ## 分大节，每节内按 ### 切 QA（一 QA 一 chunk）

        如果全文没有 ##，则退化为直接按 ### 切分。
        每个 ### QA 块内如果包含多个 #### 子标题且内容较长，按 #### 进一步拆分。
        """
        h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        doc_title = h1_match.group(1).strip() if h1_match else ""

        # 清理 frontmatter / metadata
        content = re.sub(r'^---.*?---\s*', '', content, flags=re.DOTALL)

        # 检测是否有 ## 标题
        has_h2 = bool(re.search(r'\n## ', content))

        entries: List[KBEntry] = []
        order = 0

        if not has_h2:
            # ── 无 ##：直接按 ### 切分 ──
            # 跳过 H1 之后、第一个 ### 之前的 lead-in 文本
            first_h3 = re.search(r'\n### ', content)
            if first_h3:
                content = content[first_h3.start():]
            qa_sections = re.split(r'\n(?=### )', content)
            for qa in qa_sections:
                h3_match = re.match(r'^###\s+(.+)$', qa, re.MULTILINE)
                if not h3_match:
                    continue
                q_title = h3_match.group(1).strip()
                # 清理 markdown bold 标记 **...**
                q_title_clean = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', q_title)
                q_body = re.sub(r'^###\s+.+\n', '', qa, count=1).strip()
                if not q_body:
                    continue
                # 合并连续的 #### 子节（如 #### 1.1 / #### 1.2）
                full_title = f"{doc_title} / {q_title_clean}"
                # 如果 body 有多层 ####，按 h4 拆分
                sub_entries = self._maybe_split_h4(q_body, full_title,
                                                   sub_domain, source_file, order)
                if sub_entries:
                    entries.extend(sub_entries)
                    order += len(sub_entries)
                else:
                    entries.append(KBEntry(
                        title=full_title,
                        content=q_body,
                        sub_domain=sub_domain, source_file=source_file, order=order,
                    ))
                    order += 1
            return entries

        # ── 有 ##：两层切分 ──
        sections = re.split(r'\n(?=## )', content)
        lead_in = ""
        for section in sections:
            h2_match = re.match(r'^##\s+(.+)$', section, re.MULTILINE)
            if not h2_match:
                # 第一个 ## 之前的内容（H1 标题 + 简介 + 图片等）
                # 去掉 H1 标题行，保留图片和正文，附加到首个 chunk
                lead_in = re.sub(r'^#\s+.+\n', '', section, count=1).strip()
                continue

            section_title = h2_match.group(1).strip()
            body = re.sub(r'^##\s+.+\n', '', section, count=1).strip()

            if not body:
                continue

            # 将 lead-in 内容（图片、简介等）附加到第一个 chunk 开头
            if lead_in:
                body = lead_in + "\n\n" + body
                lead_in = ""

            # 按 ### 切 QA
            qa_sections = re.split(r'\n(?=### )', body)
            if len(qa_sections) == 1:
                # 没有 ### 子标题，整节作为一个 chunk
                entries.append(KBEntry(
                    title=f"{doc_title} / {section_title}",
                    content=body,
                    sub_domain=sub_domain, source_file=source_file, order=order,
                ))
                order += 1
                continue

            for qa in qa_sections:
                h3_match = re.match(r'^###\s+(.+)$', qa, re.MULTILINE)
                if h3_match:
                    q_title = h3_match.group(1).strip()
                    q_body = re.sub(r'^###\s+.+\n', '', qa, count=1).strip()
                else:
                    # 第一个 ### 之前的 lead-in 文本——通常很短（如 "> 来源：..."），跳过
                    q_body = qa.strip()
                    if len(q_body) < 50:
                        continue
                    q_title = section_title

                if not q_body:
                    continue

                full_title = f"{doc_title} / {section_title} / {q_title}"

                # 如果 q_body 有多层 ####，按 h4 拆分
                sub_entries = self._maybe_split_h4(q_body, full_title,
                                                   sub_domain, source_file, order)
                if sub_entries:
                    entries.extend(sub_entries)
                    order += len(sub_entries)
                else:
                    entries.append(KBEntry(
                        title=full_title,
                        content=q_body,
                        sub_domain=sub_domain, source_file=source_file, order=order,
                    ))
                    order += 1

        return entries

    def _maybe_split_h4(self, body: str, title_prefix: str, sub_domain: str,
                        source_file: str, order: int) -> List:
        """如果 body 中包含多个 #### 子标题且内容较长（>=200 字符），按 #### 拆分为多个 chunk。

        否则返回空列表（调用方继续用原来的整块 chunk）。
        """
        # 检测是否有 #### 子标题
        h4_sections = re.split(r'\n(?=#### )', body)
        if len(h4_sections) <= 1:
            return []

        # 判断是否值得拆分：至少有一个 #### 块内容 >= 200 字符
        worthwhile = False
        for sec in h4_sections:
            if not re.match(r'^#### ', sec):
                continue
            sec_body = re.sub(r'^####\s+.+\n', '', sec, count=1).strip()
            if len(sec_body) >= 200:
                worthwhile = True
                break
        if not worthwhile:
            return []

        entries = []
        for sec in h4_sections:
            h4_match = re.match(r'^####\s+(.+)$', sec, re.MULTILINE)
            if not h4_match:
                # h4 之前的 lead-in（如大段说明文字），跳过
                continue
            h4_title = h4_match.group(1).strip()
            h4_body = re.sub(r'^####\s+.+\n', '', sec, count=1).strip()
            if not h4_body:
                continue
            title = f"{title_prefix} / {h4_title}"
            entries.append(KBEntry(
                title=title, content=h4_body,
                sub_domain=sub_domain, source_file=source_file, order=order,
            ))
            order += 1
        return entries

    # ═══════════════════════════════════════════════════════════
    # 策略 2: USP 实施手册 — 保留标题行，提取图片，§2.1 细切
    # ═══════════════════════════════════════════════════════════

    # 正文开始标记（跳过封面/TOC）
    _MANUAL_START_MARKER = re.compile(r'^## \*\*0\.')

    def _split_manual(self, content: str, sub_domain: str, source_file: str) -> List[KBEntry]:
        """USP 实施手册：保留 ## 标题行在 content 中，提取图片，解析章节号"""

        # 跳过封面 + TOC，从第一个正文 ## 开始
        m = self._MANUAL_START_MARKER.search(content, re.MULTILINE)
        if m:
            content = content[m.start():]

        # 按 ## 切分
        sections = re.split(r'\n(?=## )', content)
        entries: List[KBEntry] = []
        order = 0

        for section in sections:
            h2_match = re.match(r'^##\s+(.+)$', section, re.MULTILINE)
            if not h2_match:
                continue

            raw_title = h2_match.group(1).strip()
            # 去掉 ** ** 加粗标记
            clean_title = raw_title.strip('*').strip()
            if not clean_title:
                continue

            # content 保留完整 ## 标题行（与旧 parser 行为一致）
            body = section.strip()
            if not body:
                continue

            # ── 章节按子项细切：平铺手册（如「2.3 机器人操作」「9.5 任务操作」）
            # 一个 ## 章节塞七八个操作项，整章一个 chunk 时 embedding/reranker
            # 都无法把「解除车辆」这类子项查询对上——子项信号被整章稀释。
            # 切成「章节 > 子项」小块后子项可被直接命中。内部 ≥3 子项才切,
            # 短章节/无子项结构的自然退化为整章一个 chunk。
            # 提取图片
            images = _extract_images(body)

            # 解析章节号
            sec_match = re.match(r'([\d.]+)\s', clean_title)
            section_num = sec_match.group(1) if sec_match else clean_title[:20]
            chapter_num = section_num.split('.')[0] if '.' in section_num else section_num

            # ── §2.1 特殊处理：按 ### 细切（自研车/睿芯行/科钛车差异大）──
            if section_num == "2.1":
                sub_entries = self._split_manual_h3(
                    body, clean_title, section_num, chapter_num,
                    sub_domain, source_file, order,
                )
                entries.extend(sub_entries)
                order += len(sub_entries)
                continue

            sub_entries = self._split_manual_bullets(
                body, clean_title, section_num, chapter_num,
                sub_domain, source_file, order,
            )
            if sub_entries:
                entries.extend(sub_entries)
                order += len(sub_entries)
                continue

            entries.append(KBEntry(
                title=clean_title,
                content=body,
                sub_domain=sub_domain, source_file=source_file, order=order,
                images=images, section=section_num, chapter=chapter_num,
            ))
            order += 1

        return entries

    def _split_manual_h3(
        self, content: str, parent_title: str, parent_section: str, chapter: str,
        sub_domain: str, source_file: str, start_order: int,
    ) -> List[KBEntry]:
        """拆分 §2.1 的 ### 子节（自研车/睿芯行/科钛车差异大）"""
        h3_sections = re.split(r'\n(?=### )', content)
        if len(h3_sections) <= 1:
            return [KBEntry(
                title=parent_title, content=content,
                sub_domain=sub_domain, source_file=source_file, order=start_order,
                images=_extract_images(content), section=parent_section, chapter=chapter,
            )]

        entries = []
        sub_idx = 0
        for sub in h3_sections:
            h3_match = re.match(r'^###\s+(.+)$', sub, re.MULTILINE)
            if not h3_match:
                continue
            raw_h3 = h3_match.group(1).strip()
            h3_title = raw_h3.strip('*').strip()
            if not h3_title:
                continue

            sub_idx += 1
            body = sub.strip()
            images = _extract_images(body)
            full_title = f"{parent_title} > {h3_title}"
            sub_section = f"{parent_section}.{sub_idx}"

            entries.append(KBEntry(
                title=full_title,
                content=body,
                # 0 基占位 start_order..start_order+n-1：调用方 order += n 正确衔接，
                # 1 基会与下一章节首块撞 order（chunk_id 同 → upsert 覆盖，静默丢块）
                sub_domain=sub_domain, source_file=source_file, order=start_order + sub_idx - 1,
                images=images, section=sub_section, chapter=chapter,
            ))

        return entries

    def _split_manual_bullets(
        self, content: str, parent_title: str, parent_section: str, chapter: str,
        sub_domain: str, source_file: str, start_order: int,
    ) -> List[KBEntry]:
        """长章节按子项细切（如「2.3 机器人操作」下的「- 解除车辆」「- 禁用车辆」）。

        平铺手册里一个 ## 章节常含七八个操作项，整章一个 chunk 时子项查询
        （「解除车辆在什么场景下使用？」）的信号被稀释，embedding/reranker 都对不上。
        切成「章节 > 子项」小块后，子项名直接进标题，可被关键词/向量精确命中。

        子项起始行两类：
        - 「- 名字」一级 bullet（2.3 机器人操作）
        - 独立短行标题（9.5 任务操作里的「强制完成任务」「重发任务」等：
          2-25 字、无结尾标点、不以 #/-/数字/图/括号开头）
        规则：切不出至少 3 个子项 → 返回空列表（调用方退化为整章一个 chunk）；
        章节引言并进第一个子项，不丢内容。
        """
        # 短章节不细切（阈值见 _SECTION_WHOLE_CHARS）：细切是为 5000+ 字的
        # 平铺大章节设计的，短章节细切产生 1-2 行碎片，检索召回后拼不出完整步骤
        if len(content) <= self._SECTION_WHOLE_CHARS:
            return []

        def _is_subheader(line: str) -> bool:
            s = line.strip()
            if not s or len(s) < 2 or len(s) > 25:
                return False
            if s[0] in '#-0123456789![（(*':
                return False
            if s[-1] in '。！？：:；':
                return False
            return not any(ch in s for ch in '。！？：:；')

        # 标题行（## x.y 章节名）单独提出,子块 content 里保留标题行供上下文
        h2_match = re.match(r'^##[^\n]*\n?', content)
        _head = h2_match.group(0) if h2_match else ""
        body = content[len(_head):]

        # 逐行切段：bullet 行或独立短行标题都作为子项起始。
        # ``` 围栏内（手册代码块，如 `ip a`）不参与子项识别——短命令会被
        # _is_subheader 误判成标题、把代码块拆成假子项 chunk。
        segments: list[list[str]] = []
        cur: list[str] = []
        _in_fence = False
        for ln in body.splitlines():
            if ln.strip().startswith("```"):
                _in_fence = not _in_fence
                cur.append(ln)
            elif _in_fence:
                cur.append(ln)
            elif ln.startswith('- ') or _is_subheader(ln):
                if cur:
                    segments.append(cur)
                cur = [ln]
            else:
                cur.append(ln)
        if cur:
            segments.append(cur)

        if len(segments) < 3:
            return []

        lead = segments[0]
        item_segments = segments[1:]

        named = []
        for seg in item_segments:
            first = seg[0].strip()
            m = re.match(r'-\s+(\S[^\n]*)', first)
            if m:
                name = m.group(1)
            else:
                name = first
            name = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', name).strip()
            if not name:
                continue
            named.append((name, seg))

        # 空壳合并：子项标题行后面紧跟的内容以 bullet 起头时，该子项会被切成
        # 只有标题没有正文的空壳 chunk（实测 9.5「取消任务/取消充电」）。
        # 正文 <30 字且无图的子项连同标题行并入下一个子项。
        _merged: list = []
        _pending: Optional[tuple] = None
        for name, seg in named:
            _body = "\n".join(seg[1:]).strip()
            if len(_body) < 30 and "](./media/" not in "\n".join(seg) and "](media/" not in "\n".join(seg):
                _pending = (name, seg) if _pending is None else (_pending[0], _pending[1] + seg)
                continue
            if _pending is not None:
                # 合并后正文以空壳的标题行开头，条目名沿用空壳的子项名
                seg = _pending[1] + seg
                name = _pending[0]
                _pending = None
            _merged.append((name, seg))
        if _pending is not None:
            if _merged:
                _pn, _ps = _merged[-1]
                _merged[-1] = (_pn, _ps + _pending[1])
            else:
                _merged.append(_pending)
        named = _merged

        if len(named) < 3:
            return []

        entries = []
        sub_idx = 0
        _lead_text = '\n'.join(lead).strip()
        for name, seg in named:
            sub_idx += 1
            p_body = '\n'.join(seg).strip()
            # 引言并进第一个子项
            _content = f"{_head}{_lead_text}\n\n{p_body}".strip() if sub_idx == 1 else f"{_head}{p_body}".strip()
            entries.append(KBEntry(
                title=f"{parent_title} > {name}",
                content=_content,
                # 0 基占位（同 _split_manual_h3 的说明：1 基会撞下一章节首块）
                sub_domain=sub_domain, source_file=source_file,
                order=start_order + sub_idx - 1,
                images=_extract_images(p_body),
                section=f"{parent_section}.{sub_idx}", chapter=chapter,
            ))
        return entries

    # ═══════════════════════════════════════════════════════════
    # 策略 3: 车端错误码 — 按表格行切，每行一个 chunk，提取 error_code
    # ═══════════════════════════════════════════════════════════

    def _split_cheduan_errors(self, content: str, sub_domain: str, source_file: str) -> List[KBEntry]:
        """车端错误码：逐行解析表格，一行一个 chunk，提取 error_code 等字段到 payload

        两个表格列结构不同：
          - 软件错误码（3位）：code | level | desc_cn | level_en | desc_en（5列）
          - 硬件/系统错误码（4-5位）：code | category | level | desc_cn | desc_en | solution（6列）
        """
        h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        doc_title = h1_match.group(1).strip() if h1_match else "车端错误码"

        sections = re.split(r'\n(?=## )', content)
        entries: List[KBEntry] = []
        order = 0

        for section in sections:
            h2_match = re.match(r'^##\s+(.+)$', section, re.MULTILINE)
            section_title = h2_match.group(1).strip() if h2_match else doc_title

            # 判断表格格式：3位表 vs 4-5位表
            is_3bit = "3位" in section_title

            for line in section.splitlines():
                line = line.strip()
                if not line.startswith("|") or not line.endswith("|"):
                    continue

                cells = [c.strip() for c in line.split("|")]
                if cells and cells[0] == "":
                    cells.pop(0)
                if cells and cells[-1] == "":
                    cells.pop()

                if not cells:
                    continue

                code = cells[0]
                if not code.isdigit():
                    continue

                if is_3bit:
                    # 3位表：code | level(警告/故障/提示) | desc_cn | level_en | desc_en
                    level = cells[1] if len(cells) > 1 else ""
                    desc_cn = cells[2] if len(cells) > 2 else ""
                    desc_en = cells[4] if len(cells) > 4 else ""
                    cat = ""
                    solution = ""
                    # cells[3] 是英文等级（Fault/Warning/Prompt），合并到内容
                    level_en = cells[3] if len(cells) > 3 else ""
                else:
                    # 4-5位表：code | category | level | desc_cn | desc_en | solution
                    cat = cells[1] if len(cells) > 1 else ""
                    level = cells[2] if len(cells) > 2 else ""
                    desc_cn = cells[3] if len(cells) > 3 else ""
                    desc_en = cells[4] if len(cells) > 4 else ""
                    solution = cells[5] if len(cells) > 5 else ""
                    level_en = ""

                # 构建 content（嵌入用文本）
                parts = [f"错误码：{code}"]
                if cat:
                    parts.append(f"类别：{cat}")
                if level:
                    parts.append(f"等级：{level}")
                if level_en:
                    parts.append(f"Level: {level_en}")
                if desc_cn:
                    parts.append(f"描述：{desc_cn}")
                if desc_en:
                    parts.append(f"English: {desc_en}")
                if solution:
                    parts.append(f"方案：{solution}")

                entries.append(KBEntry(
                    title=f"{doc_title} / {section_title} / 错误码 {code}",
                    content="\n".join(parts),
                    sub_domain=sub_domain, source_file=source_file, order=order,
                    error_code=code,
                    category=cat,
                    level=level,
                    description_cn=desc_cn,
                    description_en=desc_en,
                    solution_cn=solution,
                ))
                order += 1

        return entries

    # ═══════════════════════════════════════════════════════════
    # 策略 4: 通用 — 按 ## 切，超长按 ### 细切
    # ═══════════════════════════════════════════════════════════

    def _split_generic(self, content: str, sub_domain: str, source_file: str) -> List[KBEntry]:
        """通用切分：按 ## 切块，超长段落（>3000 字符）按 ### 细切

        交叉引用段落（## 你可能还需要查）不独立入库——它只是"问题→模块"索引，
        单独成 chunk 会以高向量分抢占 top-k 却无排查内容。
        改为合并到前一个真实 chunk 的末尾，附加 ## 相关模块 标记，保留跨模块指引。
        """
        h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        doc_title = h1_match.group(1).strip() if h1_match else ""

        content = re.sub(r'^---.*?---\s*', '', content, flags=re.DOTALL)
        content = re.sub(r'<!--.*?-->\s*', '', content, flags=re.DOTALL)
        content = content.strip()

        # 短文档不切（阈值见 _SECTION_WHOLE_CHARS）：产品页这类 ≤2000 字的小文档
        # 按 ## 切成 3-4 个小 chunk 后，带产品图的 intro chunk 会在排序中输给其他
        # 产品的参数表 chunk（实测 XP3201 没图）。整文档一个 chunk 保证图文同在。
        if len(content) <= self._SECTION_WHOLE_CHARS:
            return [KBEntry(
                title=doc_title or source_file, content=content,
                sub_domain=sub_domain, source_file=source_file, order=0,
            )]

        sections = re.split(r'\n(?=## )', content)
        entries: List[KBEntry] = []
        order = 0

        for section in sections:
            h2_match = re.match(r'^##\s+(.+)$', section, re.MULTILINE)
            section_title = h2_match.group(1).strip() if h2_match else doc_title

            if doc_title and section_title != doc_title:
                full_title = f"{doc_title} / {section_title}"
            else:
                full_title = section_title

            # 去掉 ## 标题行
            if h2_match:
                body = re.sub(r'^##\s+.+\n', '', section, count=1).strip()
            else:
                body = section.strip()

            if not body:
                continue

            # ── 交叉引用段落：合并到前一个 chunk，不独立入库 ──
            if section_title.startswith("你可能还需要查"):
                if entries:
                    prev = entries[-1]
                    prev.content = (prev.content.rstrip()
                                    + "\n\n## 相关模块\n"
                                    + body)
                continue

            # 超长段落按 ### 进一步切分
            if len(body) > 3000:
                sub_entries = self._split_generic_h3(
                    body, full_title, sub_domain, source_file, order,
                )
                entries.extend(sub_entries)
                order += len(sub_entries)
            else:
                entries.append(KBEntry(
                    title=full_title, content=body,
                    sub_domain=sub_domain, source_file=source_file, order=order,
                ))
                order += 1

        return entries

    def _split_generic_h3(
        self, content: str, parent_title: str,
        sub_domain: str, source_file: str, start_order: int,
    ) -> List[KBEntry]:
        """通用：按 ### 细切"""
        sub_sections = re.split(r'\n(?=### )', content)
        if len(sub_sections) <= 1:
            return [KBEntry(
                title=parent_title, content=content,
                sub_domain=sub_domain, source_file=source_file, order=start_order,
            )]

        entries = []
        # 自增计数而非 start_order+i：跳过空 body 的子节时 i 会留 gap，
        # 调用方 order += len(entries) 仍会与最后保留块撞号（upsert 覆盖丢块）
        for sub in sub_sections:
            h3_match = re.match(r'^###\s+(.+)$', sub, re.MULTILINE)
            if h3_match:
                title = f"{parent_title} / {h3_match.group(1).strip()}"
                body = re.sub(r'^###\s+.+\n', '', sub, count=1).strip()
            else:
                title = parent_title
                body = sub.strip()

            if body:
                entries.append(KBEntry(
                    title=title, content=body,
                    sub_domain=sub_domain, source_file=source_file, order=start_order,
                ))
                start_order += 1
        return entries

    # ═══════════════════════════════════════════════════════════
    # 策略 5: 诊断知识卡片 — 一卡一 chunk（KB-{DOMAIN}-{NNN}.md）
    # ═══════════════════════════════════════════════════════════

    def _split_kb_cards(self, content: str, source_file: str) -> List[KBEntry]:
        """USP 诊断知识卡片（diagnostic-kb）：一张卡片 = 一个故障现象的完整
        排查闭环（现象/影响/原因/排查/处理/售后），整卡一个 chunk——按 ## 拆开
        会把「现象」和「排查步骤」切进不同向量，检索到现象却带不出处理方法。

        - sub_domain 固定 "usp_cards"（忽略 robot/task 等子目录，检索过滤与
          标签统一）
        - symptom_terms（用户口语同义词）并入嵌入文本：「车掉线」这类口语与
          卡片正文措辞差异大，靠它桥接语义检索
        - frontmatter 锚点（error_codes 等）进 payload，供未来错误码精确匹配
          过滤（卡片库 SCHEMA 的「精确优先」分层）
        """
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", content, re.DOTALL)
        if not m:
            self._log(f"[WARN] 卡片缺 frontmatter: {source_file}")
            body = content
            meta = {}
        else:
            fm_text, body = m.group(1), content[m.end():]
            meta = {}
            for key in ("id", "title", "domain", "severity"):
                mm = re.search(rf"^{key}:\s*(.+)$", fm_text, re.MULTILINE)
                if mm:
                    meta[key] = mm.group(1).strip().strip("\"'")
            meta["symptom_terms"] = self._fm_inline_list(fm_text, "symptom_terms")
            meta["related_cards"] = self._fm_inline_list(fm_text, "related_cards")
            # anchors.error_codes：行内 [...] 或块状 - "X:1" 两种写法
            codes = self._fm_inline_list(fm_text, "error_codes")
            if not codes:
                mm = re.search(
                    r"^\s*error_codes:\s*\n((?:\s*-\s*.+\n?)+)", fm_text, re.MULTILINE)
                if mm:
                    codes = [ln.strip().lstrip("-").strip().strip("\"'")
                             for ln in mm.group(1).strip().splitlines() if ln.strip()]
            meta["error_codes"] = codes

        title = meta.get("title") or Path(source_file).stem
        symptom = meta.get("symptom_terms") or []
        embed_text = title
        if symptom:
            embed_text += "\n用户口语说法：" + "、".join(symptom)
        embed_text += "\n" + body.strip()

        return [KBEntry(
            title=title,
            content=body.strip(),
            sub_domain="usp_cards",
            source_file=source_file,
            order=0,
            card_id=meta.get("id", ""),
            card_domain=meta.get("domain", ""),
            severity=meta.get("severity", ""),
            symptom_terms=symptom,
            related_cards=meta.get("related_cards") or [],
            anchor_error_codes=meta.get("error_codes") or [],
            embed_text=embed_text,
        )]

    @staticmethod
    def _fm_inline_list(fm_text: str, key: str) -> List[str]:
        """frontmatter 行内列表：key: [a, b, "c"] → [a, b, c]"""
        mm = re.search(rf"^{key}:\s*\[(.*?)\]\s*$", fm_text, re.MULTILINE)
        if not mm:
            return []
        return [x.strip().strip("\"'") for x in mm.group(1).split(",") if x.strip()]

    # ═══════════════════════════════════════════════════════════
    # Chunk 构建
    # ═══════════════════════════════════════════════════════════

    def to_chunk(self, entry: KBEntry) -> Chunk:
        text = entry.embed_text or f"{entry.title}\n{entry.content}"

        chunk_id = self.stable_id(
            self._domain,
            entry.sub_domain,
            entry.source_file,
            str(entry.order),
        )

        payload = {
            "domain": self._domain,
            "sub_domain": entry.sub_domain,
            "title": entry.title,
            "content": entry.content,
            "source_file": str(entry.source_file),
            "order": entry.order,
        }
        if entry.images:
            payload["images"] = entry.images
        if entry.section:
            payload["section"] = entry.section
        if entry.chapter:
            payload["chapter"] = entry.chapter
        # 车端错误码专用字段
        if entry.error_code:
            payload["error_code"] = entry.error_code
        if entry.category:
            payload["category"] = entry.category
        if entry.level:
            payload["level"] = entry.level
        if entry.description_cn:
            payload["description_cn"] = entry.description_cn
        if entry.solution_cn:
            payload["solution_cn"] = entry.solution_cn
        # 诊断知识卡片专用字段（error_codes 供未来精确匹配 payload filter）
        if entry.card_id:
            payload["card_id"] = entry.card_id
            payload["card_domain"] = entry.card_domain
            if entry.severity:
                payload["severity"] = entry.severity
            if entry.symptom_terms:
                payload["symptom_terms"] = entry.symptom_terms
            if entry.related_cards:
                payload["related_cards"] = entry.related_cards
            if entry.anchor_error_codes:
                payload["error_codes"] = entry.anchor_error_codes

        return Chunk(id=chunk_id, text=text, payload=payload)

    def get_source_label(self) -> str:
        n = len(self.source_paths)
        return f"kb/{self._domain} ({n} files)"


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _extract_images(md_text: str) -> List[str]:
    """提取 markdown 中的图片引用（media/xxx.png）"""
    return [
        m.group(1)
        for m in re.finditer(r'!\[[^\]]*\]\((?:\./)?(media/[^)]+)\)', md_text)
    ]


def create_domain_ingester(domain: str, kb_root: Optional[Path] = None) -> KBDomainIngester:
    return KBDomainIngester(domain=domain, kb_root=kb_root)
