"""U老师长期记忆：Markdown 真源 + 本地召回，Qdrant personal 域 best-effort 双写。

首版只写 kind=directive（用户 @U老师 明确要记）。失败不阻断讨论/诊断主流程。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

_MEMORY_SAVE_RE = re.compile(
    r"(?:记住|记一下|帮我记|以后都用|以后就用|以后都按|记住这条|记下|别忘了?|"
    r"统一(?:按|用|走)|约定(?:是|为)?)",
    re.IGNORECASE,
)
_PREFIX_STRIP = re.compile(
    r"^(?:请)?(?:帮我)?(?:记住|记一下|记下|别忘了?)(?:这条|一下)?(?:[:：,\s]+)?"
    r"|^(?:以后都用|以后就用|以后都按|统一按|统一用|统一走)\s*",
    re.IGNORECASE,
)
_NEG_RE = re.compile(r"不是这个|别记|不要记|不用记|别记住")
_HTML_META = re.compile(
    r"<!--\s*id:\s*(?P<id>\S+)\s*"
    r"kind:\s*(?P<kind>\S+)\s*"
    r"importance:\s*(?P<importance>\S+)\s*"
    r"source:\s*(?P<source>\S*)\s*"
    r"source_id:\s*\"?(?P<source_id>[^\s\"]*)\"?\s*"
    r"created_at:\s*(?P<created_at>[^\n]+)\s*"
    r"status:\s*(?P<status>\S+)\s*"
    r"-->",
    re.DOTALL,
)
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}")

_DEFAULT_DIR = Path(__file__).resolve().parent / "data"
_SERVICE: Optional["AgentMemoryService"] = None


def extract_directive_content(query: str) -> str:
    """从用户话里抽出要记住的内容。未命中或像否定句时返回空串。"""
    q = (query or "").strip()
    if not q or not _MEMORY_SAVE_RE.search(q):
        return ""
    if _NEG_RE.search(q):
        return ""
    content = q
    for _ in range(3):
        nxt = _PREFIX_STRIP.sub("", content, count=1).strip(" ：:，,。.;；")
        if nxt == content:
            break
        content = nxt
    if len(content) < 4:
        content = _MEMORY_SAVE_RE.sub("", q, count=1).strip(" ：:，,。.;；")
    if len(content) < 4:
        return ""
    return content[:500]


_EXTRACT_SYSTEM = (
    "你是记忆写入门禁。判断用户是否真的要 U老师 长期记住某条约定/结论。"
    "只输出一行 JSON，不要其它文字。"
)
_EXTRACT_PROMPT = """用户原话：
{query}

正则初抽内容：
{candidate}

判断规则：
- 用户明确要求记住/记一下/以后都用 → should_save=true，content 写精炼后的记忆正文（去掉「记住」等指令前缀）
- 「记住，不是这个 / 别记 / 不要记」或只是在讨论、没有要沉淀的约定 → should_save=false，content 空
- content 不超过 200 字，一句话写清约定本身

输出格式（严格）：
{{"should_save": true或false, "content": "..."}}
"""


def _parse_extract_json(raw: str) -> tuple[Optional[bool], str]:
    """从 LLM 输出里抠 should_save / content。解析失败返回 (None, "")。"""
    import json

    text = (raw or "").strip()
    if not text:
        return None, ""
    # 允许包在代码块或前后废话里
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return None, ""
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None, ""
    if not isinstance(obj, dict):
        return None, ""
    flag = obj.get("should_save")
    if isinstance(flag, str):
        flag = flag.strip().lower() in ("1", "true", "yes", "y")
    elif not isinstance(flag, bool):
        return None, ""
    content = str(obj.get("content") or "").strip()[:500]
    return bool(flag), content


async def confirm_directive_with_llm(query: str, candidate: str) -> tuple[bool, str]:
    """LLM 二次确认是否写入记忆。

    Returns:
        (should_save, content)。LLM 失败时 fail-open：信任正则 candidate。
    """
    cand = (candidate or "").strip()
    if not cand:
        return False, ""
    try:
        from ai.core import get_llm_client

        llm = await get_llm_client()
        raw = await llm.complete(
            prompt=_EXTRACT_PROMPT.format(query=(query or "")[:800], candidate=cand[:500]),
            system_prompt=_EXTRACT_SYSTEM,
            max_tokens=200,
            temperature=0.0,
        )
        flag, content = _parse_extract_json(raw or "")
        if flag is None:
            logger.warning("[memory] extract 解析失败，回退正则内容")
            return True, cand
        if not flag:
            return False, ""
        return True, (content or cand)[:500]
    except Exception as e:
        logger.warning(f"[memory] extract LLM 失败，回退正则: {e}")
        return True, cand


def format_memory_block(entries: list[dict]) -> str:
    """把召回条目做成独立 prompt 段；空列表返回空串。"""
    lines = []
    for e in entries or []:
        content = str(e.get("content") or "").strip()
        if not content:
            continue
        kind = e.get("kind") or "directive"
        lines.append(f"- [{kind}] {content}")
    if not lines:
        return ""
    return (
        "## U老师此前记忆（仅供参考）\n"
        + "\n".join(lines)
        + "\n"
    )


def _tokens(text: str) -> set[str]:
    s = text or ""
    out = {t.lower() for t in _TOKEN_RE.findall(s)}
    chars = [c for c in s if "\u4e00" <= c <= "\u9fff"]
    for i in range(len(chars) - 1):
        out.add(chars[i] + chars[i + 1])
    return out


def _overlap_score(query: str, content: str) -> float:
    q, c = (query or "").strip(), (content or "").strip()
    if not q or not c:
        return 0.0
    if q in c or c in q:
        return 1.0
    qt, ct = _tokens(q), _tokens(c)
    if not qt or not ct:
        return 0.0
    inter = len(qt & ct)
    return max(inter / len(qt), inter / len(ct))


class AgentMemoryService:
    def __init__(self, memory_dir: Optional[Path] = None):
        self.memory_dir = Path(memory_dir or _DEFAULT_DIR)
        self.entries_dir = self.memory_dir / "entries"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.entries_dir.mkdir(parents=True, exist_ok=True)

    def _entry_path(self, mem_id: str) -> Path:
        return self.entries_dir / f"mem_{mem_id}.md"

    def _render_entry(self, rec: dict) -> str:
        return (
            f"<!-- id: {rec['id']}\n"
            f"     kind: {rec.get('kind') or 'directive'}\n"
            f"     importance: {rec.get('importance', 0.8)}\n"
            f"     source: {rec.get('source') or ''}\n"
            f"     source_id: \"{rec.get('source_id') or ''}\"\n"
            f"     created_at: {rec.get('created_at') or ''}\n"
            f"     status: {rec.get('status') or 'active'}\n"
            f"-->\n"
            f"内容：{rec.get('content') or ''}\n"
            f"出处：{rec.get('source') or ''}#{rec.get('source_id') or ''}\n"
        )

    def _parse_entry(self, text: str) -> Optional[dict]:
        m = _HTML_META.search(text or "")
        if not m:
            return None
        rec = m.groupdict()
        rec["importance"] = float(rec.get("importance") or 0.8)
        rec["content"] = ""
        cm = re.search(r"内容：(.+)", text)
        if cm:
            rec["content"] = cm.group(1).strip()
        rec["created_at"] = (rec.get("created_at") or "").strip()
        return rec

    @staticmethod
    def public_entry(rec: dict) -> dict:
        """给管理界面看的字段，不含文件路径。"""
        return {
            "id": rec.get("id") or "",
            "kind": rec.get("kind") or "directive",
            "content": rec.get("content") or "",
            "created_at": rec.get("created_at") or "",
            "source": rec.get("source") or "",
            "source_id": rec.get("source_id") or "",
        }

    def list_entries(self, kind: Optional[str] = None, status: str = "active") -> list[dict]:
        out = []
        if not self.entries_dir.exists():
            return out
        for p in sorted(self.entries_dir.glob("mem_*.md")):
            try:
                rec = self._parse_entry(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not rec:
                continue
            if status and rec.get("status") != status:
                continue
            if kind and rec.get("kind") != kind:
                continue
            out.append(rec)
        return out

    def _write_entry(self, rec: dict) -> None:
        self._entry_path(rec["id"]).write_text(self._render_entry(rec), encoding="utf-8")

    def _append_daily(self, rec: dict) -> None:
        day = (rec.get("created_at") or datetime.now().strftime("%Y-%m-%d"))[:10]
        path = self.memory_dir / f"{day}.md"
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + self._render_entry(rec))

    def _rebuild_memory_md(self) -> None:
        actives = self.list_entries(status="active")
        chunks = ["# U老师长期记忆（active）\n"]
        for rec in actives:
            chunks.append(self._render_entry(rec))
        (self.memory_dir / "MEMORY.md").write_text("\n".join(chunks), encoding="utf-8")

    async def store(
        self,
        content: str,
        kind: str = "directive",
        importance: float = 0.8,
        source: str = "",
        source_id: str = "",
    ) -> str:
        content = (content or "").strip()
        if not content:
            return ""
        # 近似查重：高度相似的 active directive 就地 supersede
        for old in self.list_entries(kind=kind, status="active"):
            if _overlap_score(content, old.get("content") or "") >= 0.6:
                await self.supersede(old["id"])
        mem_id = str(uuid.uuid4())
        rec = {
            "id": mem_id,
            "kind": kind or "directive",
            "importance": float(importance or 0.8),
            "source": source or "",
            "source_id": str(source_id or ""),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "active",
            "content": content,
        }
        try:
            self._write_entry(rec)
            self._append_daily(rec)
            self._rebuild_memory_md()
        except Exception as e:
            logger.warning(f"[memory] 写 Markdown 失败: {e}")
            return ""
        try:
            await self._qdrant_upsert(rec)
        except Exception as e:
            logger.warning(f"[memory] Qdrant 双写失败（已落 Markdown）: {e}")
        logger.info(f"[memory] stored id={mem_id} kind={kind} source={source}#{source_id}")
        return mem_id

    async def update_content(self, mem_id: str, content: str) -> Optional[dict]:
        """改一条仍生效的记忆正文，并重写向量。不存在或已作废时返回 None。"""
        content = (content or "").strip()[:500]
        if not content:
            return None
        path = self._entry_path(mem_id)
        if not path.exists():
            return None
        rec = self._parse_entry(path.read_text(encoding="utf-8"))
        if not rec or rec.get("status") != "active":
            return None
        rec["content"] = content
        self._write_entry(rec)
        self._rebuild_memory_md()
        try:
            await self._qdrant_upsert(rec)
        except Exception as e:
            logger.warning(f"[memory] 更新后 Qdrant 双写失败（Markdown 已改）: {e}")
        return self.public_entry(rec)

    async def recall(self, query: str, top_k: int = 3, kinds: Optional[list[str]] = None) -> list[dict]:
        query = (query or "").strip()
        hits: list[dict] = []
        try:
            hits = await self._qdrant_search(query, top_k=top_k)
        except Exception as e:
            logger.warning(f"[memory] Qdrant 召回失败，改本地: {e}")
        if hits:
            if kinds:
                hits = [h for h in hits if h.get("kind") in kinds]
            return hits[:top_k]
        scored = []
        for rec in self.list_entries(status="active"):
            if kinds and rec.get("kind") not in kinds:
                continue
            scored.append((_overlap_score(query, rec.get("content") or ""), rec))
        scored.sort(key=lambda x: (-x[0], -(x[1].get("importance") or 0)))
        return [rec for score, rec in scored if score > 0][:top_k]

    async def supersede(self, mem_id: str) -> None:
        path = self._entry_path(mem_id)
        if not path.exists():
            return
        rec = self._parse_entry(path.read_text(encoding="utf-8"))
        if not rec:
            return
        rec["status"] = "superseded"
        self._write_entry(rec)
        try:
            self._rebuild_memory_md()
        except Exception:
            pass

    async def delete(self, mem_id: str) -> bool:
        """删除一条记忆。文件不存在时返回 False。向量库删除失败只记日志。"""
        path = self._entry_path(mem_id)
        existed = path.exists()
        if existed:
            path.unlink()
            try:
                self._rebuild_memory_md()
            except Exception:
                pass
        try:
            await self._qdrant_delete(mem_id)
        except Exception as e:
            logger.warning(f"[memory] Qdrant 删除失败（Markdown 已处理）: {e}")
        return existed

    async def _qdrant_upsert(self, rec: dict) -> None:
        from ai.config import get_active_collection_for
        col = get_active_collection_for("personal")
        if not col:
            return
        from ai.core import get_retrieval_service
        retriever = await get_retrieval_service()
        embed_client = getattr(retriever, "_embed_client", None)
        if embed_client is None and hasattr(retriever, "_ensure_embed_client"):
            await retriever._ensure_embed_client()
            embed_client = getattr(retriever, "_embed_client", None)
        if embed_client is None:
            return
        vec = await embed_client.embed(rec["content"])
        qdrant = getattr(retriever, "_qdrant", None)
        if qdrant is None:
            return
        payload = {
            "mem_id": rec["id"],
            "kind": rec.get("kind"),
            "content": rec.get("content"),
            "importance": rec.get("importance"),
            "source_task": rec.get("source_id"),
            "created_at": rec.get("created_at"),
            "status": "active",
            "domain": "personal",
        }
        await qdrant.upsert_to_collection(
            collection_name=col,
            vectors=[vec.tolist() if hasattr(vec, "tolist") else list(vec)],
            ids=[rec["id"]],
            payloads=[payload],
        )

    async def _qdrant_delete(self, mem_id: str) -> None:
        if not mem_id:
            return
        from ai.config import get_active_collection_for
        col = get_active_collection_for("personal")
        if not col:
            return
        from ai.core import get_retrieval_service
        retriever = await get_retrieval_service()
        qdrant = getattr(retriever, "_qdrant", None)
        if qdrant is None:
            return
        client = await qdrant._ensure_client()
        client.delete(collection_name=col, points_selector=[mem_id])

    async def _qdrant_search(self, query: str, top_k: int = 3) -> list[dict]:
        if not query:
            return []
        from ai.config import get_active_collection_for
        col = get_active_collection_for("personal")
        if not col:
            return []
        from ai.core import get_retrieval_service
        retriever = await get_retrieval_service()
        embed_client = getattr(retriever, "_embed_client", None)
        qdrant = getattr(retriever, "_qdrant", None)
        if embed_client is None or qdrant is None:
            return []
        vec = await embed_client.embed(query)
        hits = await qdrant.search_dense(
            vector=vec.tolist() if hasattr(vec, "tolist") else list(vec),
            top_k=top_k * 2,
            collection_name=col,
        )
        out = []
        for h in hits or []:
            payload = getattr(h, "payload", None) or {}
            if payload.get("status") and payload.get("status") != "active":
                continue
            content = payload.get("content") or ""
            if not content:
                continue
            out.append({
                "id": payload.get("mem_id") or "",
                "kind": payload.get("kind") or "directive",
                "content": content,
                "importance": payload.get("importance") or 0.8,
                "status": payload.get("status") or "active",
            })
        return out[:top_k]


def get_agent_memory_service(memory_dir: Optional[Path] = None) -> AgentMemoryService:
    global _SERVICE
    if memory_dir is not None:
        return AgentMemoryService(memory_dir)
    if _SERVICE is None:
        _SERVICE = AgentMemoryService()
    return _SERVICE


async def prepare_discuss_memory(query: str, task_id: str = "") -> tuple[str, str]:
    """讨论入口：命中「记住」则经 LLM 门禁后写入；始终按问题召回。失败返回空，不抛。

    Returns:
        (memory_block, saved_content)  saved_content 非空表示本轮刚记住。
    """
    saved = ""
    block = ""
    try:
        svc = get_agent_memory_service()
        candidate = extract_directive_content(query)
        if candidate:
            should_save, directive = await confirm_directive_with_llm(query or "", candidate)
            if should_save and directive:
                mem_id = await svc.store(
                    content=directive, kind="directive", source="task", source_id=str(task_id or ""),
                )
                if mem_id:
                    saved = directive
            elif candidate and not should_save:
                logger.info("[memory] extract 判定不写入（should_save=false）")
        hits = await svc.recall(query or saved, top_k=3)
        block = format_memory_block(hits)
    except Exception as e:
        logger.warning(f"[memory] discuss 准备失败: {e}")
    return block, saved


async def prepare_diagnose_memory(query: str) -> str:
    """诊断入口：只召回，不写入。"""
    try:
        hits = await get_agent_memory_service().recall(query, top_k=3)
        return format_memory_block(hits)
    except Exception as e:
        logger.warning(f"[memory] diagnose 召回失败: {e}")
        return ""
