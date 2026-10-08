"""@U老师 讨论流程 — 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的 discuss 方法（保持 self.xxx 调用不变，仅拆分文件）。
discuss = 针对性：按 query 关键词触发日志/图片/代码/历史，组合讨论历史回复。
"""

import asyncio
import time
from pathlib import Path

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.prompts import (
    DISCUSS_SYSTEM_PROMPT, DISCUSS_USER_TEMPLATE,
    select_system_prompt as _select_system_prompt,
)
# @# 跨工单引用解析/注入（模块顶层导入，避免运行时静默降级掩盖 import 错误）
from ai.agents.AiTaskPlatform.contexts import (
    extract_referenced_task_ids,
    format_discussion_thread,
    format_referenced_tickets,
    format_quoted_comment_block,
    load_ticket_discussion,
)
from ai.agents.AiTaskPlatform.tracing import nest_progress_todos

logger = get_logger("TASK_AGENT")


async def _client_cancelled(is_cancelled) -> bool:
    """浏览器 AbortController 断开后，服务端应停止写评论、停止拉过程区。"""
    if not is_cancelled:
        return False
    try:
        res = is_cancelled()
        if asyncio.iscoroutine(res):
            res = await res
        return bool(res)
    except Exception:
        return False


async def _emit_discuss_hook(hook, payload) -> None:
    """调用可选 discuss 流式钩子（sync/async 均可）；失败不阻断主流程。"""
    if hook is None:
        return
    try:
        res = hook(payload)
        if asyncio.iscoroutine(res):
            await res
    except Exception as e:
        logger.warning(f"[discuss] 流式钩子失败: {type(e).__name__}: {e}")


# 澄清建议的稳定标记（P4）：追加"建议补充信息"时以该标记开头，
# 后续轮次检测到此标记即视为"已建议过一次"，不再重复建议（只建议一次）。
CLARIFY_SUGGEST_MARKER = "🔄 U老师已建议补充"


async def _append_round_supplements(task_id: str, runtime_ctx: dict | None, prompt: str) -> str:
    """把「插入本轮」邮箱 + 本轮已消费补充拼进最终回复 prompt。"""
    extra: list[str] = []
    try:
        from ai.agents.AiTaskPlatform.runtime.inject_mailbox import drain, format_block
        extra = await drain(task_id)
    except Exception as e:
        logger.warning(f"[discuss] inject drain 失败: {e}")
        return prompt
    bag: list[str] = []
    if runtime_ctx is not None:
        bag = list(runtime_ctx.get("round_supplements") or [])
        bag.extend(extra)
        runtime_ctx["round_supplements"] = bag
    else:
        bag = extra
    try:
        from ai.agents.AiTaskPlatform.runtime.inject_mailbox import format_block
        block = format_block(bag)
    except Exception:
        block = ""
    if not block:
        return prompt
    return f"{prompt}\n\n{block}"


# ── 附件记忆「大脑决策」分类 ─────────────────────────────────────────
# 只维护 attachment_analysis（已解读附件记忆）。每次 discuss 据此决定读哪些附件：
#   - 未解读（object_path 不在记忆）         → 必须读文件分析
#   - 已解读 + 图片                          → 不重读，直接用记忆摘要（截图结论稳定）
#   - 已解读 + 日志/其他文件                 → 摘要仅作历史参考，**不替代真实分析**；
#     用户可能告知"之前的分析是错的"，故日志始终可作为 log_analyze 资源，由 Supervisor
#     决定是否真实重读（复用缓存索引，只重跑推理不重建索引）。
def _classify_attachments(ctx):
    """按记忆 + 扩展名把 ctx.attachments 分为 new（需读）与 known（已解读，含可重读附件）。

    Returns:
        (new_atts, known_map, known_atts, kind_of)
          new_atts:  需要本次真正读文件分析的附件 dict 列表
          known_map: {object_path: {filename, kind, summary}} 已解读摘要（供大脑参考）
          known_atts:已解读附件的完整 dict 列表（kind=non-image 的可在需要重读时取用）
          kind_of:   {object_path: 'image'|'log'|'doc'|'other'}
    """
    memo = getattr(ctx, "attachment_analysis", None) or {}
    new_atts = []
    known_map = {}
    known_atts = []
    kind_of = {}
    for att in ctx.attachments or []:
        try:
            from ai.agents.AiTaskPlatform.attachments.utils import (
                normalize_attachment, attachment_kind,
            )
            att = normalize_attachment(att) or att
        except Exception:
            attachment_kind = None
        if not isinstance(att, dict):
            continue
        obj = att.get("object_path") or att.get("path") or att.get("url") or ""
        fname = att.get("filename") or att.get("name") or ""
        ext = attachment_kind(fname, obj) if attachment_kind else _attachment_kind(fname, obj)
        kind_of[obj] = ext
        mem = memo.get(obj) if obj and isinstance(memo, dict) else None
        if mem and mem.get("analyzed"):
            known_map[obj] = {
                "filename": fname,
                "kind": mem.get("kind") or ext,
                "summary": mem.get("summary", ""),
            }
            known_atts.append(att)
        else:
            new_atts.append(att)  # 未解读 → 本次必读
    return new_atts, known_map, known_atts, kind_of


def _attachment_kind(filename: str, path: str = "") -> str:
    """按扩展名粗略判断附件类型：image / log / doc / other。"""
    try:
        from ai.agents.AiTaskPlatform.attachments.utils import attachment_kind
        return attachment_kind(filename, path)
    except Exception:
        pass
    name = ((filename or path) or "").lower()
    for ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"):
        if name.endswith(ext):
            return "image"
    for ext in (".log", ".txt", ".csv"):
        if name.endswith(ext):
            return "log"
    for ext in (".docx", ".pdf", ".xlsx", ".md", ".xls", ".doc"):
        if name.endswith(ext):
            return "doc"
    return "log" if ("log" in name) else "other"


def _is_chat_record_name(name: str) -> bool:
    n = name or ""
    return "对话记录" in n or "chat_record" in n.lower()


def _attachment_inventory(ctx, kinds: dict, has_log_file: bool) -> str:
    """给调度 LLM / 回复用的附件实情，避免把对话记录.md 当成现场日志。"""
    rows = []
    for att in ctx.attachments or []:
        if not isinstance(att, dict):
            rows.append(f"- {att}")
            continue
        fname = att.get("filename") or att.get("name") or att.get("object_path") or "未命名"
        obj = att.get("object_path") or att.get("path") or ""
        kind = kinds.get(obj) or _attachment_kind(fname, obj)
        extra = "（摇人吧转工单的聊天记录，不是车端/服务端日志）" if _is_chat_record_name(fname) else ""
        rows.append(f"- [{kind}] {fname}{extra}")
    if not rows:
        return "工单附件清单：空（没有可下载的文件）"
    head = f"工单附件清单（共 {len(rows)} 个，已从存储读到）：\n" + "\n".join(rows)
    if has_log_file:
        return head + "\n其中含可分析的日志文件，应派 log_analyze。"
    return head + "\n没有 .log/.zip 等现场日志，不能做日志逐行分析；有文档则应派 attachment_parse 读内容。"


def _build_progress_emitter(task_id, run_id, live_todo: dict):
    """构造 Supervisor 单项进度回调：累积 live_todo 并广播 ai.progress 到后端 WS。

    让前端在执行过程中实时看到"正在做哪一步 / 已完成哪步"。
    """
    def emitter(payload: dict) -> None:
        tid = payload.get("id")
        if tid is not None:
            live_todo[tid] = payload
        # 事件封套 phase 固定为 running：只要 Supervisor 还在派发能力（>0 项尚未收尾），
        # 前端就应保持「正在排查执行」的进行中状态（头部转圈 + 文案）。
        # 单项完成的 done 只体现在该 todo 项自身的 phase/status（图标 ✅），
        # 而**不是**整场执行完成——否则第一项一完成，头部就跳到"排查执行完成"，
        # 但剩下的项还在 ⏳，造成「完成了却还在转」的自相矛盾困惑。
        # 整场收尾的 done 由 _broadcast_ai_progress_await(..., "done") 单独发送。
        phase = "running"
        _broadcast_ai_progress(
            task_id, run_id,
            todos=nest_progress_todos(list(live_todo.values())),
            phase=phase,
        )
    return emitter


def _broadcast_ai_progress(task_id, run_id, todos, phase: str) -> None:
    """跨进程通知后端把 AI 执行进度广播进该工单的 WS 房间（best-effort 不阻塞主流程）。"""
    try:
        from ai.agents.AiTaskPlatform.contexts import notify_backend_ai_progress
        notify_backend_ai_progress(task_id, run_id, todos, phase)
    except Exception as e:
        logger.warning(f"[discuss] ai.progress 广播失败 phase={phase} task={task_id}: {e}")


async def _broadcast_ai_progress_await(task_id, run_id, todos, phase: str) -> None:
    """整体阶段的确定性广播（await 等待发出后返回），用于收尾 done 信号。

    相比 best-effort 的火花线程，这里保证 done 一定送达后端，前端据此收起执行过程。
    """
    try:
        from ai.agents.AiTaskPlatform.contexts import notify_backend_ai_progress_await
        await notify_backend_ai_progress_await(task_id, run_id, todos, phase)
    except Exception as e:
        logger.warning(f"[discuss] ai.progress 收尾广播失败 phase={phase} task={task_id}: {e}")


class DiscussFlow:
    # ============================================================
    # discuss — @U老师 讨论回复
    # ============================================================

    async def discuss(
        self,
        task_id: str,
        query: str,
        context: dict,
        username: str = "",
        is_cancelled=None,
        on_token=None,
        on_rewrite=None,
    ) -> dict:
        """@U老师 讨论：基于讨论历史 + 工单上下文 + 按需附件/历史工单 回复。

        Supervisor 派发能力时的实时进度会通过后端 WS 广播 ai.progress，前端动态
        展示执行过程；最终回复只写纯粹答复（不含过程块）。

        on_token / on_rewrite：可选回调（sync 或 async）。流式出字时逐 token 调用
        on_token；Evaluator/澄清附录等改写全文后调用 on_rewrite(完整正文)。
        """
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        # 0. 用户画像注入（诊断 Agent 复用）
        user_profile_block = ""
        if username:
            try:
                from ai.core.user_profile import resolve_user_profile, format_user_profile_block
                profile = await resolve_user_profile(username)
                user_profile_block = format_user_profile_block(profile)
            except Exception as e:
                logger.warning(f"[discuss] 用户画像解析失败(降级无画像): user={username}, err={e}")

        # 1. 工单上下文
        ctx = await self._load_task_context(task_id)

        # 1b. @# 跨工单引用（L2 注入）：解析用户 query 里 @#编号 引用的历史工单，
        #     预加载其上下文（基本信息 + diagnosis + solution + 讨论评论），
        #     作为"新加入的上下文"注入 prompt。预加载路径（Q3c=B）：不走 Supervisor。
        referenced_tickets = ""
        if query:
            try:
                _ref_ids = extract_referenced_task_ids(query)
                if _ref_ids:
                    referenced_tickets = format_referenced_tickets(_ref_ids)
                    self._add_trace(
                        self.NODE_DISCUSS, "ok",
                        output={"ticket_ref": _ref_ids},
                    )
            except Exception as _ref_e:
                logger.warning(f"[discuss] @# 引用工单注入失败: {_ref_e}")
                referenced_tickets = ""

        # 2. 本工单讨论史：从评论表按时间整段读取，不依赖前端只传最近几条。
        discussion_history = ""
        try:
            discussion_history = load_ticket_discussion(task_id)
        except Exception as _hist_e:
            logger.warning(f"[discuss] 读取工单讨论史失败: {_hist_e}")
        recent = context.get("recent_comments", []) if context else []
        if not discussion_history:
            discussion_history = format_discussion_thread(recent if isinstance(recent, list) else [])
        if not discussion_history:
            discussion_history = "（暂无讨论）"
        discussion_lines = [] if discussion_history == "（暂无讨论）" else [discussion_history]

        # 2b. 讨论区「引用这句话」：用户引用某条评论后再 @U老师。
        #     与 @# 同款预加载，单独成段，避免淹没在最近 10 条历史里。
        quoted_comment = ""
        try:
            quoted_comment = format_quoted_comment_block(context or {})
        except Exception as _q_e:
            logger.warning(f"[discuss] 引用评论注入失败: {_q_e}")
            quoted_comment = ""

        # 2c. 长期记忆：用户明确「记住 XX」则写入；召回注入 prompt（失败不阻断）。
        memory_block = ""
        saved_memory = ""
        try:
            from ai.agents.AiTaskPlatform.memory import prepare_discuss_memory
            memory_block, saved_memory = await prepare_discuss_memory(query or "", task_id)
        except Exception as _m_e:
            logger.warning(f"[discuss] 长期记忆准备失败: {_m_e}")

        # 3. Supervisor 自主调度能力（方案甲全量收敛：图片/日志/代码/历史都由调度 LLM 决定）
        #    替代原先 3a/3b/3c/3d 写死的关键词触发。
        from ai.agents.AiTaskPlatform.capabilities import Supervisor, CapabilityRegistry
        from ai.agents.AiTaskPlatform.contexts import build_img_ctx

        facultative = ""
        early_stopped = False  # 历史方案已验证：跳过 Evaluator 二次改写
        reasoning_trace = {}  # 透明化 planning（G6）：记录 Supervisor 的调度 plan/todo
        progress_done_payload = None  # (run_id, todos)，评论落库后再广播 done
        # 澄清闭环（P4）：当 Supervisor 判定需向用户确认关键信息且无子任务可派时为 True
        is_clarify = False
        clarify_questions: list[str] = []
        available_caps = CapabilityRegistry.list_available()  # 含 log_analyze（本版全量收敛）

        # 3.0 附件记忆「大脑决策」：区分本次需新读的附件与历史已解读摘要
        new_atts, known_map, known_atts, kind_of = _classify_attachments(ctx)

        # 运行时上下文（供能力取资源；attachments 默认只给"本次需新读"的附件）
        # user_query：用户本轮原话；勿用 key=query，避免 Supervisor 用它覆盖 step.goal
        runtime_ctx = {
            "attachments": new_atts,          # 默认只读新附件（能力据此分析）
            "all_attachments": ctx.attachments or [],   # 全量（需要时扩展）
            "attachment_memory": known_map,   # 已解读附件的摘要（能力/LLM 参考，不必重读）
            "retriever": self._retriever,
            "user_query": (query or "").strip(),
            # 当前工单上下文（供 ticket_ref 在"无 @#编号、需大脑按需检索相似工单"时作检索基准）
            "current_task": {
                "task_id": getattr(ctx, "task_id", "") or task_id,
                "title": ctx.title or "",
                "description": ctx.description or "",
                "problem_summary": ctx.problem_summary or "",
                "fault_code": ctx.fault_code or "",
                "robot_type": ctx.robot_type or "",
                "project_id": getattr(ctx, "project_id", "") or "",
            },
            "is_cancelled": is_cancelled,
            "round_supplements": [],
        }
        try:
            from ai.agents.AiTaskPlatform.tracing import TraceBus
            runtime_ctx["trace_bus"] = TraceBus()
        except Exception:
            pass
        # @# 确定性引用已在本函数入口预加载注入（Q3c=B 主路径）→ 让大脑不再派发 ticket_ref，
        # 避免对同一个 @#编号 重复注入。只有当入口 query 没有顶层 @#（没有预加载）时，
        # 才保留 ticket_ref 给大脑"按需检索相似工单"（形态 C 大脑决策版）。
        if referenced_tickets:
            available_caps = [c for c in available_caps if c != "ticket_ref"]
        # memory_store 由入口触发词确定性写入，不让 Supervisor 自行抽取
        available_caps = [c for c in available_caps if c != "memory_store"]
        if ctx.attachments:
            runtime_ctx["img_ctx"] = build_img_ctx(ctx)
            try:
                log_paths, _tmp_dirs = self._extract_log_paths(ctx.attachments, task_id)
                if log_paths:
                    runtime_ctx["log_path"] = log_paths[0]
                    logger.info(f"[discuss] 解析到 {len(log_paths)} 个日志文件")
            except Exception:
                log_paths, _tmp_dirs = [], []
            runtime_ctx.setdefault("_tmp_dirs", _tmp_dirs)

        # 3.0a 解析选中的可达 USP 环境（实际拉取放到过程区启动后，便于前端看见进度）
        usp_env_id = None
        try:
            raw_env = (context or {}).get("usp_env_id")
            if raw_env is not None and str(raw_env).strip() != "":
                usp_env_id = int(raw_env)
        except (TypeError, ValueError):
            usp_env_id = None

        # 3.0b 大脑重读决策（确定性走廊 + 记忆）：
        #   - 新增附件必读（记忆判断，already in new_atts）
        #   - 历史图片：不重读（解读结论相对稳定，用摘要即可，除非用户明确要重看）
        #   - 历史日志/文档：**摘要只是历史参考，不能替代真实分析**。用户可能告诉 AI
        #     "之前的分析是错的"，故日志要始终可作为 log_analyze 资源（是否真正分析由
        #     Supervisor 决定）；用户明确提到日志/分析时无条件纳入重读。
        from ai.agents.AiTaskPlatform.retrieval import rules as _rules
        analyzed_atts = list(runtime_ctx["attachments"])  # 已计划要读的（当前=new_atts）
        # 把历史已解读的日志/文档始终纳入分析资源（供 Supervisor 决定是否真实重读）
        for att in known_atts:
            obj2 = att.get("object_path") or att.get("path") or att.get("url") or ""
            if kind_of.get(obj2) in ("log", "doc", "other"):
                analyzed_atts.append(att)
        if analyzed_atts:
            runtime_ctx["attachments"] = analyzed_atts
            try:
                lpaths, _td = self._extract_log_paths(analyzed_atts, task_id)
                if lpaths and not runtime_ctx.get("log_path"):
                    runtime_ctx["log_path"] = lpaths[0]
                    if _td:
                        runtime_ctx.setdefault("_tmp_dirs", []).extend(_td)
            except Exception:
                pass
        # 用户明确要"重新分析日志/再分析/前面的分析是错的"时，强制重读日志并让
        # Supervisisor 重新走 log_analyze（复用缓存索引，只重跑推理，不重建索引）。
        _ask_reanalyze_log = bool(
            query
            and any(kw in query for kw in ("重新分析", "再分析", "重新看", "再看一下", "分析是错的", "分析错了", "不对吧", "之前分析", "重新查"))
        )

        # 3.1 构造给调度 LLM 看的能力描述清单（只把"当前可用的、有意义的"交给它）
        #   - 确定性程序护栏：能力所需资源缺失时直接从可用清单剔除，避免 LLM 派发必然失败的能力
        #     （如工单没有日志 → 不提供 log_analyze；没有非图片附件 → 不提供 attachment_parse）。
        _cap_att = runtime_ctx.get("attachments") or []
        _cap_all = ctx.attachments or []
        _kinds = set(kind_of.values())
        _has_log = bool(runtime_ctx.get("log_path")) or bool(usp_env_id)
        _has_image = "image" in _kinds
        _has_non_image = bool(_kinds & {"log", "doc", "other"})
        # 附件的 object_path 集合（供 image_analyze 判断是否有可分析的图片）
        _img_paths = [a for a in _cap_all if kind_of.get(a.get("object_path") or a.get("path") or "") == "image"]

        _RESOURCE_GUARD = {
            "log_analyze": _has_log,                       # 需日志路径
            "attachment_parse": _has_non_image,            # 需非图片附件（日志/文档）
            "image_analyze": bool(_has_image or _img_paths),  # 需图片附件
            "project_info": bool(getattr(ctx, "project_id", "")),  # 需工单关联项目
        }
        available_caps = [
            c for c in available_caps
            if _RESOURCE_GUARD.get(c, True)  # 未在守卫表内的能力（历史/代码/排查树等）视为可用
        ]
        if available_caps != CapabilityRegistry.list_available():
            removed = [c for c in CapabilityRegistry.list_available() if c not in available_caps]
            att_names = []
            for a in (ctx.attachments or []):
                if isinstance(a, dict):
                    att_names.append(a.get("filename") or a.get("object_path") or "")
                else:
                    att_names.append(str(a)[:80])
            logger.info(
                f"[discuss] 按资源剔除不可用能力: {removed}（可用: {available_caps}）"
                f" attachments={att_names[:8]} kinds={sorted(_kinds)} log_path={bool(runtime_ctx.get('log_path'))}"
            )

        cap_hint = ", ".join(available_caps) or "（无可用能力）"
        has_att = bool(ctx.attachments)
        has_logs = bool(runtime_ctx.get("log_path"))

        # 已解读摘要的简述（让大脑知道历史结论，不必重读；日志可再读）
        known_lines = []
        for obj, rec in known_map.items():
            kind_label = {"image": "图片", "log": "日志", "doc": "文档"}.get(rec.get("kind"), rec.get("kind"))
            known_lines.append(f"- [{kind_label}] {rec.get('filename') or obj}: {rec.get('summary') or '（已分析）'}")
        known_txt = "\n".join(known_lines) if known_lines else "（无）"
        new_lines = []
        for att in new_atts:
            obj = att.get("object_path") or att.get("path") or ""
            fname = att.get("filename") or att.get("name") or ""
            new_lines.append(f"- {kind_of.get(obj, 'other')}: {fname or obj}")
        new_txt = "\n".join(new_lines) if new_lines else "（无）"
        att_inventory = _attachment_inventory(ctx, kind_of, bool(runtime_ctx.get("log_path")))

        project_catalog = ""
        if getattr(ctx, "project_id", ""):
            try:
                from ai.agents.AiTaskPlatform.capabilities.tools.project_info import safe_catalog
                project_catalog = safe_catalog(ctx.project_id)
            except Exception as _pi_e:
                logger.warning(f"[discuss] 项目信息目录失败: {_pi_e}")
        if not project_catalog:
            project_catalog = "（工单未关联项目，或该项目还没有可引用的现场信息）"

        task_ctx_for_plan = (
            f"工单: {ctx.title or ''}\n"
            f"描述: {(ctx.description or '')[:200]}\n"
            f"假设: {' / '.join(ctx.hypotheses) if ctx.hypotheses else '无'}\n"
            f"用户问题: {query or '（本轮用户仅@U老师未附加文字，请基于下方讨论历史延续解答）'}\n"
            f"{quoted_comment}"
            f"{memory_block}"
            f"本工单讨论历史（按时间，前文结论和追问都要接着用）:\n{discussion_history}\n"
            f"{att_inventory}\n"
            f"本次新增/未解读附件（需重点分析）:\n{new_txt}\n"
            f"历史已解读附件摘要（**仅作历史参考**：图片结论稳定可复述；"
            f"日志/文档摘要不代表已分析完成，若需分析请派 log_analyze 真实重读）:\n{known_txt}\n"
            f"可用能力: {cap_hint}\n"
            f"本项目现场信息目录（只有组名，没有字段值）:\n{project_catalog}\n\n"
            "根据用户问题（或讨论历史中未解决的疑问）和附件情况，决定派哪些能力分析"
            "（知识库检索/图片/日志/历史/代码）。\n"
            "规则：新增附件必分析；历史图片一般用其摘要即可（除非用户明确要重看）；\n"
            "历史日志/文档摘要**不能替代真实分析**：用户可能说\"之前的分析是错的\"，"
            "只要本问题需要从日志取证，就应派 log_analyze 真实分析日志（内部复用缓存索引，快）。\n"
            "若附件只有「对话记录」md：派 attachment_parse 读取聊天内容，"
            "**不要说工单没有任何附件**，但要说清楚这不是车端日志、没法逐行分析 log。\n"
            "若问题属于知识问答（怎么操作/错误码含义/协议标准/产品介绍/排查方法）→ 派 retrieve_kb 查知识库。\n"
            "若用户要看项目信息、现场信息，或问题依赖车型、软件版本、外设、业务系统、人员、风险"
            " → 派 project_info，目标里保留用户原话。"
            "用户明确要「项目信息」时可以展开已填字段；只是闲聊就不要派。\n"
            "若当前轮仅@U老师无新问题，但讨论历史有未决疑问或刚提到需要分析的内容 → 仍应继续深化分析。\n"
            "若确无实质内容可派、只需总结/寒暄，则 complexity=simple 不派生任何能力。"
        )

        # 3.2 是否值得走 Supervisor 调度？
        #    无条件进入：query 非空，**或** 有讨论历史（支持用户上一条说了内容但忘记@U老师、
        #    本条只发 @U老师「补召唤」的场景——此时 query 为空也要基于讨论历史解答）。
        #    由 Supervisor「大脑」自行判断要不要派发能力（知识库 retrieve_kb / 历史 / 日志 /
        #    图片 / 代码）还是 complexity=simple 直接回复。纯闲聊由 3.2b 的 Router
        #    （is_pure_chat）把关，不进 Supervisor。
        has_discussion = bool(discussion_lines)
        need_supervisor = bool(query) or has_discussion or bool(quoted_comment)

        # 3.2b LLM 意图路由（改造点 A / G1）：识别纯闲聊 → 走短 prompt 快路径，
        #      不派生任何工具/子 Agent，省一次 Supervisor 调度 + token。
        is_pure_chat = False
        if query:
            try:
                from ai.agents.AiTaskPlatform.capabilities import Router
                intent = await Router.classify(
                    llm_client=self._llm_client,
                    query=query,
                    has_attachments=has_att,
                    has_logs=has_logs,
                    fallback="general",
                )
                is_pure_chat = (intent == "pure_chat")
                self._add_trace(self.NODE_DISCUSS, "ok", output={"router_intent": intent})
            except Exception:
                is_pure_chat = False

        if need_supervisor and available_caps and not is_pure_chat:
            supervisor = Supervisor(llm_client=self._llm_client)

            # 实时进度流（改造点 G6.5 / 动态执行过程）：
            #  - 每项能力 running/done 时，通过后端 WS 广播 ai.progress，前端边跑边展示；
            #  - 同时把最新状态累积到 reasoning_trace.todo（供最终返回 + 失败保底）。
            run_id = f"{task_id}:{int(time.time() * 1000)}"
            _live_todo: dict[str, dict] = {}
            _emit_progress = _build_progress_emitter(
                task_id=task_id, run_id=run_id, live_todo=_live_todo,
            )

            # 起始 running 信号（确定性广播）：前端只有在收到 phase=running 时才会建立
            # aiRunId 并显示「执行过程区」（DiscussionPanel.showAiProcess）。若首条 running
            # 走 fire-and-forget 而丢失，前端只收到收尾 done 时过程区恒不显示。故这里用
            # await 版本先确保送达一条带占位项的 running；后续逐项 running/done 仍走
            # best-effort（容忍丢失），收尾 done 保持 await 确定性送达。
            await _broadcast_ai_progress_await(
                task_id, run_id,
                todos=[{
                    "id": "planning",
                    "description": "正在分析任务并规划排查步骤",
                    "status": "in_progress",
                    "capability": "",
                    "phase": "running",
                }],
                phase="running",
            )

            # 选中环境且尚无附件日志 → 过程区可见地 SSH 拉 export_logs.sh
            if usp_env_id and not runtime_ctx.get("log_path"):
                _live_todo["usp_pull"] = {
                    "id": "usp_pull",
                    "description": "解析故障发生时间并拉取 USP 算法日志",
                    "status": "in_progress",
                    "capability": "ssh_export_logs",
                    "phase": "running",
                }
                _emit_progress(_live_todo["usp_pull"])
                try:
                    from ai.agents.AiTaskPlatform.server_pull import pull_recent_usp_logs
                    from ai.agents.AiTaskPlatform.server_pull.occurrence_resolve import (
                        resolve_occurrence_time,
                    )
                    # 结构化字段 → 正文正则 → 大模型从工单材料提取（不用 created_at）
                    occurrence, occurrence_src = await resolve_occurrence_time(
                        self._llm_client,
                        collected_info=ctx.collected_info or {},
                        occurrence_time=getattr(ctx, "occurrence_time", "") or "",
                        title=ctx.title or "",
                        description=ctx.description or "",
                        problem_summary=ctx.problem_summary or "",
                        query=query or "",
                        discussion=discussion_history if discussion_lines else "",
                    )
                    if not occurrence:
                        occurrence_src = "now(fallback)"
                    if occurrence:
                        runtime_ctx["occurred_at"] = occurrence
                        # 主路径：故障前 15 分钟；不强制 ±2 分钟（那是可选兜底）
                        runtime_ctx["window_minutes"] = 15
                        runtime_ctx.pop("before_minutes", None)
                        runtime_ctx.pop("after_minutes", None)
                        # 本轮内存补上，供后续 log_analyze / 回复引用（不写回 DB）
                        try:
                            ci = dict(ctx.collected_info or {})
                            if not (ci.get("occurrence_time") or ci.get("发生时间")):
                                ci["occurrence_time"] = occurrence
                                ctx.collected_info = ci
                        except Exception:
                            pass
                    logger.info(
                        f"[discuss] 从 USP 环境 env={usp_env_id} 拉取最近日志 "
                        f"anchor={occurrence_src} raw={occurrence!r}"
                    )
                    _live_todo["usp_pull"] = {
                        "id": "usp_pull",
                        "description": (
                            f"按故障时间拉取 USP 日志（{occurrence or '当前时刻'}，来源 {occurrence_src}）"
                        ),
                        "status": "in_progress",
                        "capability": "ssh_export_logs",
                        "phase": "running",
                    }
                    _emit_progress(_live_todo["usp_pull"])
                    pulled, pull_tmps = await pull_recent_usp_logs(
                        env_id=usp_env_id, occurrence_time=occurrence,
                    )
                    if pulled:
                        from ai.agents.AiTaskPlatform.server_pull.usp_log_puller import (
                            select_log_for_query,
                        )
                        # 按问题选服务日志：「不可达」优先 TMS-MAP → DYNAMIC_MAP，勿先读 TASK-MANAGER
                        pick_hint = " ".join(
                            filter(
                                None,
                                [
                                    query or "",
                                    ctx.title or "",
                                    (ctx.description or "")[:400],
                                    ctx.problem_summary or "",
                                ],
                            )
                        )
                        ordered = select_log_for_query(pulled, pick_hint)
                        chosen = ordered[0]
                        runtime_ctx["log_path"] = chosen
                        runtime_ctx["log_paths"] = ordered
                        runtime_ctx.setdefault("_tmp_dirs", []).extend(pull_tmps or [])
                        runtime_ctx["usp_env_id"] = usp_env_id
                        has_logs = True
                        logger.info(
                            f"[discuss] USP 选用日志 first={Path(chosen).name} "
                            f"all={[Path(p).name for p in ordered[:6]]}"
                        )
                        self._add_trace(
                            self.NODE_DISCUSS, "ok",
                            output={
                                "usp_pull": {
                                    "env_id": usp_env_id,
                                    "logs": len(pulled),
                                    "chosen": Path(chosen).name,
                                }
                            },
                        )
                        _live_todo["usp_pull"] = {
                            "id": "usp_pull",
                            "description": (
                                f"已从 USP 拉取日志（选用 {Path(chosen).name}，共 {len(pulled)} 个"
                                + (f"，锚点 {occurrence}" if occurrence else "")
                                + f"，来源 {occurrence_src}）"
                            ),
                            "status": "completed",
                            "capability": "ssh_export_logs",
                            "phase": "done",
                        }
                    else:
                        _live_todo["usp_pull"] = {
                            "id": "usp_pull",
                            "description": "USP 拉取未得到日志文件（已跳过）",
                            "status": "completed",
                            "capability": "ssh_export_logs",
                            "phase": "done",
                        }
                except Exception as _pull_e:
                    logger.warning(f"[discuss] USP 拉日志降级 env={usp_env_id}: {_pull_e}")
                    _live_todo["usp_pull"] = {
                        "id": "usp_pull",
                        "description": f"USP 拉取失败，已跳过：{type(_pull_e).__name__}",
                        "status": "completed",
                        "capability": "ssh_export_logs",
                        "phase": "done",
                    }
                _emit_progress(_live_todo["usp_pull"])
                if runtime_ctx.get("log_path"):
                    # 拉取成功后补一句，避免规划上下文仍写「没有现场日志」
                    _occ_hint = runtime_ctx.get("occurred_at") or ""
                    task_ctx_for_plan = (
                        task_ctx_for_plan
                        + f"\n已从可达 USP 环境 #{usp_env_id} 拉取到算法日志"
                        + (f"（故障时间锚点 {_occ_hint}）" if _occ_hint else "")
                        + "，应优先派 log_analyze；调用时传入 occurred_at。\n"
                    )

            # 把进度回调注入运行时上下文，供子 Agent（如 LogSubAgent）内部上报子步骤
            runtime_ctx["progress_emitter"] = _emit_progress

            sup_result = await supervisor.run(
                task_context=task_ctx_for_plan,
                available_caps=available_caps,
                runtime_ctx=runtime_ctx,
                on_progress=_emit_progress,
            )
            # 把各能力结果拼进 facultative
            if sup_result.get("results"):
                for cap_name, res in sup_result["results"].items():
                    if isinstance(res, dict) and (res.get("terminate") or (res.get("meta") or {}).get("terminate")):
                        early_stopped = True
                    if isinstance(res, dict) and res.get("text"):
                        label = {
                            "image_analyze": "图片分析",
                            "log_analyze": "日志分析",
                            "code_search": "代码检索",
                            "retrieve_history": "历史相似工单",
                            "retrieve_kb": "知识库参考",
                            "retrieve_troubleshooting": "排查树",
                            "project_info": "项目现场信息",
                        }.get(cap_name, cap_name)
                        facultative += f"\n[{label}]\n{res['text']}\n"
                    elif isinstance(res, dict) and not res.get("ok"):
                        # 能力失败：记录告警（无发生时间的日志提示已内嵌在结果文本里，不再单独阻断）
                        logger.warning(f"[discuss] 能力 {cap_name} 失败: {res.get('error')}")

            # ── 确定性保底（仅当用户明确要求"分析全部/所有附件/图片"时）——强制补做图片分析 ──
            # LLM 调度偶发只派 attachment_parse（解析文本附件）而不派 image_analyze，
            # 导致"分析全部附件"时截图不被识别。但**不做成无条件的**：用户没明确要求全面分析
            # 图片时，是否派 image_analyze 尊重 Supervisor/用户自己的意图（如用户单独说"分析一下图片"）。
            _ask_all_media = any(kw in query for kw in ("全部", "所有", "全部附件", "所有附件", "分析全部"))
            if (
                _ask_all_media
                and "image_analyze" in available_caps
                and _img_paths
                and "image_analyze" not in sup_result.get("results", {})
            ):
                try:
                    from ai.agents.AiTaskPlatform.capabilities import CapabilityRegistry
                    _img_cap = CapabilityRegistry.get("image_analyze")
                    if _img_cap is not None and _img_cap.is_available():
                        # 复用已回退全量的运行时附件（含图片 object_path），走 image_analyze
                        _img_kw = {
                            "query": "分析工单中的全部图片/截图，识别其中的车辆状态、路径与异常信息",
                            "img_ctx": runtime_ctx.get("img_ctx"),
                            "attachments": _img_paths,          # 仅图片附件
                            "all_attachments": _cap_all,        # 全量兜底
                        }
                        _img_res = await _img_cap(**_img_kw)  # 统一入口 __call__（含配额/异常兜底）
                        _res_dict = _img_res.to_dict() if hasattr(_img_res, "to_dict") else _img_res
                        if isinstance(_res_dict, dict) and _res_dict.get("text"):
                            facultative += f"\n[图片分析]\n{_res_dict['text']}\n"
                            sup_result.setdefault("results", {})["image_analyze"] = _res_dict
                            # 同步补一条 todo（进过程区展示）
                            _live_todo_txt = _res_dict["text"]
                            _live_todo.setdefault("image_analyze", {
                                "id": f"img_{len(_live_todo) + 1}",
                                "description": "分析工单中的图片/截图，识别车辆状态与路径信息",
                                "status": "completed",
                                "capability": "image_analyze",
                                "phase": "done",
                                "result_summary": _live_todo_txt[:80],
                            })
                            logger.info("[discuss] 已强制补做 image_analyze（图片保底分析）")
                except Exception as e:
                    logger.warning(f"[discuss] 强制 image_analyze 失败: {e}")

            # 用户点名日志/全面分析，但 Supervisor 没读文档附件（常见：只有对话记录.md）
            _ask_logs = any(kw in (query or "") for kw in ("日志", "附件", "全面分析", "分析一下"))
            if (
                _ask_logs
                and "attachment_parse" in available_caps
                and "attachment_parse" not in (sup_result.get("results") or {})
                and (runtime_ctx.get("attachments") or _cap_all)
            ):
                try:
                    from ai.agents.AiTaskPlatform.capabilities import CapabilityRegistry
                    _parse_cap = CapabilityRegistry.get("attachment_parse")
                    if _parse_cap is not None and _parse_cap.is_available():
                        _parse_res = await _parse_cap(
                            query=query,
                            attachments=runtime_ctx.get("attachments") or _cap_all,
                            all_attachments=_cap_all,
                        )
                        _res_dict = _parse_res.to_dict() if hasattr(_parse_res, "to_dict") else _parse_res
                        if isinstance(_res_dict, dict) and _res_dict.get("text"):
                            facultative += f"\n[附件分析]\n{_res_dict['text']}\n"
                            sup_result.setdefault("results", {})["attachment_parse"] = _res_dict
                            _live_todo.setdefault("attachment_parse", {
                                "id": "attachment_parse",
                                "description": "读取工单已有附件（含对话记录）",
                                "status": "completed",
                                "capability": "attachment_parse",
                                "phase": "done",
                                "result_summary": (_res_dict.get("text") or "")[:80],
                            })
                            logger.info("[discuss] 已强制补做 attachment_parse（文档/对话记录保底）")
                except Exception as e:
                    logger.warning(f"[discuss] 强制 attachment_parse 失败: {e}")
            if ctx.attachments and "log_analyze" not in available_caps:
                facultative += f"\n[附件清单]\n{att_inventory}\n"

            # ── 日志「重新分析」确定性保底 ──
            # 用户明确说"重新分析/再分析/前面的分析是错了/不对"时，必须**真实重跑日志分析**
            # （不是看历史摘要敷衍），复用缓存索引只重跑推理、不重建索引。
            # 若 Supervisor 本轮没派 log_analyze，这里确定性补派。
            if (
                _ask_reanalyze_log
                and "log_analyze" in available_caps
                and runtime_ctx.get("log_path")
                and "log_analyze" not in sup_result.get("results", {})
            ):
                try:
                    from ai.agents.AiTaskPlatform.capabilities import CapabilityRegistry
                    _log_cap = CapabilityRegistry.get("log_analyze")
                    if _log_cap is not None and _log_cap.is_available():
                        _log_kw = {
                            "log_path": runtime_ctx["log_path"],
                            "log_paths": runtime_ctx.get("log_paths") or [],
                            "query": query,
                            "task_context": runtime_ctx.get("current_task", {}),
                            "occurred_at": runtime_ctx.get("occurred_at") or "",
                            "window_minutes": runtime_ctx.get("window_minutes", 15),
                        }
                        # before/after 仅显式兜底时传入，不作为默认
                        if "before_minutes" in runtime_ctx:
                            _log_kw["before_minutes"] = runtime_ctx["before_minutes"]
                        if "after_minutes" in runtime_ctx:
                            _log_kw["after_minutes"] = runtime_ctx["after_minutes"]
                        _log_res = await _log_cap(**_log_kw)
                        _res_dict = _log_res.to_dict() if hasattr(_log_res, "to_dict") else _log_res
                        if isinstance(_res_dict, dict) and _res_dict.get("text"):
                            facultative += f"\n[日志分析]\n{_res_dict['text']}\n"
                            sup_result.setdefault("results", {})["log_analyze"] = _res_dict
                            # 同步补一条 todo（进过程区展示）
                            _live_todo.setdefault("log_analyze", {
                                "id": f"log_re",
                                "description": "重新分析日志（按用户要求，复用缓存索引）",
                                "status": "completed",
                                "capability": "log_analyze",
                                "phase": "done",
                                "result_summary": (_res_dict.get("text") or "")[:80],
                            })
                            logger.info("[discuss] 已强制补做 log_analyze（重新分析日志保底）")
                except Exception as e:
                    logger.warning(f"[discuss] 强制 log_analyze 失败: {e}")

            self._add_trace(self.NODE_ATTACHMENT, "ok",
                            output={"supervisor_caps": list(sup_result.get("results", {}).keys())})

            # 透明化 planning（G6）：把 Supervisor 的调度决策、plan、todo 暴露给前端
            final_todo = sup_result.get("todo", [])
            if _live_todo:
                # 用实时进度流累积的 todo 覆盖（含每项状态，前端能拿到完整过程）
                merged = []
                for item in final_todo:
                    live = _live_todo.get(item.get("id"))
                    merged.append(live if live else item)
                final_todo = merged
            # 补充强制 image_analyze 的 todo（若存在），保证过程区能看到图片分析半步
            _img_todo = _live_todo.get("image_analyze")
            if _img_todo and not any(t.get("capability") == "image_analyze" for t in final_todo):
                final_todo = final_todo + [_img_todo]
            # 补充「重新分析日志」保底补派的 log_analyze todo（若存在）
            _log_re_todo = _live_todo.get("log_analyze")
            if _log_re_todo and not any(t.get("capability") == "log_analyze" for t in final_todo):
                final_todo = final_todo + [_log_re_todo]
            if _live_todo:
                present = {t.get("id") for t in final_todo}
                for kid, payload in _live_todo.items():
                    if kid in present:
                        continue
                    final_todo.append(payload)
            reasoning_trace = {
                "complexity": sup_result.get("complexity"),
                "plan": sup_result.get("plan", []),
                "todo": final_todo,
                "decision": sup_result.get("_decision"),
                "run_id": run_id,
                "spans": sup_result.get("spans") or (
                    runtime_ctx["trace_bus"].tree()
                    if hasattr(runtime_ctx.get("trace_bus"), "tree") else []
                ),
            }

            # ── 澄清闭环（P4）：Supervisor 判定还需向用户确认关键信息（ask_user=true）——
            #    作为「排查优先」的补充而非阻塞：把待确认问题作为可选的"结尾补充提问"注入
            #    facultative，让回复"先给分析、后附问"，绝不因追问卡住排查。
            #    下一轮 discuss 会通过 discussion_history 自动看到"问题 + 用户回答"，
            #    从而自然闭合追问循环（无需额外持久化）。
            clarify_questions = sup_result.get("questions") or []
            if sup_result.get("ask_user") and clarify_questions:
                is_clarify = True
                logger.info(f"[discuss] ask_user 澄清：附带确认 {len(clarify_questions)} 项问题")
                reasoning_trace["clarify"] = True
                reasoning_trace["questions"] = clarify_questions

            # 收尾：整体完成广播（前端据此收起执行过程、仅展示纯回复）。
            # 用 await 版本确保 done 一定送达后端，避免出现"一直转不停"。
            # 兜底：把任何残留 in_progress 项归一化为 completed，保证「完成」封套内的
            # 每一项都是完成态，绝不出现「头部说完成、单项还在转圈」的矛盾。
            _final_todo = []
            for _t in final_todo:
                _t = dict(_t)
                if _t.get("status") == "in_progress":
                    _t["status"] = "completed"
                if _t.get("phase") in ("running", "in_progress"):
                    _t["phase"] = "done"
                _final_todo.append(_t)
            final_todo = nest_progress_todos(_final_todo)
            reasoning_trace["todo"] = final_todo
            # 先不广播 done：浏览器离开/刷新时连接会断，若此处就「完成」前端会收起过程区，
            # 后面再跳过写评论就变成「执行完成但没有回复」。等评论落库后再发 done。
            progress_done_payload = (run_id, final_todo)

            # 清理临时目录（如日志解压）
            for td in runtime_ctx.get("_tmp_dirs", []):
                try:
                    import shutil
                    shutil.rmtree(td, ignore_errors=True)
                except Exception:
                    pass
        else:
            # query 为空（或纯闲聊，由上面 is_pure_chat 判定）→ 不走 Supervisor，
            # 直接走纯知识谈话路径（facultative 为空）。知识库等能力的派发判断
            # 已交给上面无条件进入的 Supervisor 大脑。
            pass
        # 3.9 写回附件记忆：本次实际读取分析的附件标记为已解读，供下次「大脑」判断不再重复
        if facultative or runtime_ctx.get("attachments"):
            _read_atts = runtime_ctx.get("attachments") or []
            if _read_atts:
                _updates = {}
                _summary = (facultative or "").strip()[:800]
                for _att in _read_atts:
                    if not isinstance(_att, dict):
                        continue
                    _obj = _att.get("object_path") or _att.get("path") or _att.get("url") or ""
                    if not _obj:
                        continue
                    _updates[_obj] = {
                        "kind": kind_of.get(_obj, "other"),
                        "summary": _summary or _att.get("filename") or _obj,
                    }
                if _updates:
                    try:
                        from ai.core.task_adapter import update_attachment_analysis
                        update_attachment_analysis(task_id, _updates)
                    except Exception as _e:
                        logger.warning(f"[discuss] 写回附件记忆失败: {_e}")

        # 4. LLM（纯闲聊走 light 短 prompt，省 token）
        #    澄清（P4）不单独走 prompt：一律先生成分析答复，待确认问题在 4.7 作为"补充提问"追加。
        #    若用户 @# 引用了历史工单（referenced_tickets 非空），或本轮引用了某条评论
        #    （quoted_comment 非空），说明在针对具体内容提问，绝非闲聊 —— 强制走完整
        #    DISCUSS 模板（light 模板原先没有这些占位，会把引用丢掉）。
        if is_pure_chat and not referenced_tickets and not quoted_comment and not saved_memory:
            from ai.agents.AiTaskPlatform.prompts import (
                DISCUSS_LIGHT_SYSTEM_PROMPT, DISCUSS_LIGHT_USER_TEMPLATE,
            )
            prompt = DISCUSS_LIGHT_USER_TEMPLATE.format(
                title=ctx.title or "",
                description=(ctx.description or "")[:200],
                discussion_history=discussion_history,
                quoted_comment=quoted_comment or "",
                query=query or "",
            )
            system_prompt = DISCUSS_LIGHT_SYSTEM_PROMPT
            max_tokens = 200
        else:
            if not facultative and query:
                att_mentions = _rules.ATTACHMENT_MENTION_WORDS
                if any(kw in query.lower() for kw in att_mentions):
                    n_att = len(ctx.attachments or [])
                    if n_att:
                        facultative = (
                            f"工单上已有 {n_att} 个附件，但本轮未能解析为可分析的日志/图片。"
                            "不要说用户没上传、不要让用户重新上传已经在工单上的文件；"
                            "说明本轮读附件失败，可请对方确认是 zip/log 而非 rar。"
                        )
                    else:
                        facultative = "当前工单没有日志、图片或任何可解析的附件。请如实告知工程师，不要编造。"

            diag_summary = f"推测: {' / '.join(ctx.hypotheses) if ctx.hypotheses else '无'}"
            prompt = DISCUSS_USER_TEMPLATE.format(
                title=ctx.title or "",
                description=(ctx.description or "")[:200],
                diagnosis_summary=diag_summary,
                discussion_history=discussion_history,
                quoted_comment=quoted_comment or "",
                query=query or "请基于讨论历史和工单信息，给出你的分析和建议。",
                referenced_tickets=referenced_tickets or "",
                facultative_analysis=facultative,
            )
            system_prompt = _select_system_prompt(self._is_platform_ticket(ctx), "discuss")
            max_tokens = 600

        prompt = await _append_round_supplements(task_id, runtime_ctx, prompt)

        # 注入用户画像到 system prompt（有画像时追加，无画像保持原样）
        if user_profile_block:
            system_prompt = f"{system_prompt}\n\n{user_profile_block}"
        if memory_block:
            prompt = f"{prompt}\n\n{memory_block}"

        if await _client_cancelled(is_cancelled):
            logger.info(f"[discuss] 客户端已离开，仍生成并写入回复 task={task_id}")

        t_llm = time.perf_counter()
        draft_parts: list[str] = []
        try:
            async for token in self._llm_client.stream(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=0.4,
            ):
                if not token:
                    continue
                draft_parts.append(token)
                await _emit_discuss_hook(on_token, token)
        except Exception as e:
            # 流式失败时回退一次性 complete，保证仍能出答复
            logger.warning(f"[discuss] stream 失败，回退 complete: {type(e).__name__}: {e}")
            draft_parts = [
                await self._llm_client.complete(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=0.4,
                )
                or ""
            ]
            if draft_parts[0] and on_token is not None:
                await _emit_discuss_hook(on_token, draft_parts[0])
        reply = "".join(draft_parts)
        draft_snapshot = reply
        self._add_trace(self.NODE_LLM, "ok",
                        output={"reply_chars": len(reply), "streamed": True},
                        elapsed_ms=round((time.perf_counter() - t_llm) * 1000))

        # 4.5 Evaluator-optimizer（改造点 C/G4）：仅对"需工具的讨论"启用（成本护栏，纯闲聊不启用）
        # 早停（已验证历史方案）跳过自评改写，避免把可直接采用的结论改偏。
        eval_used = False
        if facultative and reply.strip() and not early_stopped:
            try:
                from ai.agents.AiTaskPlatform.capabilities import Evaluator
                eval_res = await Evaluator.evaluate_and_rewrite(
                    llm_client=self._llm_client,
                    draft=reply,
                    evidence=facultative[:1500],       # 引用的证据（日志/图片/历史片段）
                    context=f"工单: {ctx.title or ''}\n用户问题: {query or ''}",
                )
                if eval_res.get("rewritten") or eval_res.get("eval_failed"):
                    eval_used = True
                if eval_res.get("rewritten"):
                    reply = eval_res["final"]
                    self._add_trace(self.NODE_LLM, "ok",
                                    output={"evaluator": "rewritten", "issues": eval_res.get("eval_notes", []), "reply_chars": len(reply)})
                elif eval_res.get("eval_failed"):
                    self._add_trace(self.NODE_LLM, "ok",
                                    output={"evaluator": "eval_failed", "reply_chars": len(reply)})
            except Exception as e:
                logger.warning(f"[discuss] Evaluator 执行异常，沿用初稿: {e}")

        # 4.7 澄清闭环（P4）「排查优先 + 一次性建议补充」：
        #      - 分析永远是主体（已经在上面的 DISCUSS 路径生成）；
        #      - 若 Supervisor 判定还缺关键信息，才在答复末尾**建议**补充（是建议，不是提问）；
        #      - **只建议一次**：若此前评论里已出现过建议补充的标记（AI 上一轮已建议过、
        #        而用户仍未提供），则本轮不再建议，转而基于现有信息继续给结论/方向；
        #      - 已覆盖的问题自动跳过。
        _raw_disc = (discussion_history or "").lower()
        _already_suggested = CLARIFY_SUGGEST_MARKER.lower() in _raw_disc
        _disc_low = (discussion_history or "").lower()
        _pending_q = [
            q for q in clarify_questions
            if q and q.strip() and q.strip().lower() not in _disc_low
        ]
        if is_clarify and _pending_q and not _already_suggested:
            _appendix = ("\n\n---\n" + CLARIFY_SUGGEST_MARKER + "\n"
                         + "基于现有信息初步判断到这一步。如果能有以下信息，定位会更准：\n"
                         + "\n".join(f"- {q}" for q in _pending_q[:3])
                         + "\n（没有这些信息也能按上面的方向继续排查。）")
            reply = (reply or "").rstrip() + _appendix
            self._add_trace(self.NODE_LLM, "ok",
                            output={"clarify_appendix": len(_pending_q), "reply_chars": len(reply)})

        if saved_memory:
            marker = f"已记住：{saved_memory}"
            if marker not in (reply or ""):
                reply = f"✅ {marker}\n\n{(reply or '').lstrip()}"

        # 流式草稿被 Evaluator / 澄清附录 / 记忆标记改写后，推送完整正文替换前端草稿
        if (reply or "") != (draft_snapshot or ""):
            await _emit_discuss_hook(on_rewrite, reply or "")

        # 5. 回复写入 task_comments
        #    最终评论只写入纯粹答复（不含"分析过程"）——执行过程已通过 ai.progress
        #    WS 事件在前端动态展示，不污染最终回复。
        #    浏览器离开工单/刷新会断开 HTTP，但排查已经做完：仍要写评论，回来能看见。
        if await _client_cancelled(is_cancelled):
            logger.info(f"[discuss] 客户端已离开，仍写入评论 task={task_id}")
        comment_reply = (reply or "").strip()
        if not comment_reply:
            comment_reply = "本轮排查步骤已跑完，但没有生成文字结论。请再 @U老师 一次，或根据过程区步骤补充问题。"
            reply = comment_reply
            await _emit_discuss_hook(on_rewrite, reply)
        try:
            self._add_diagnosis_comment_short(int(task_id), comment_reply)
        except Exception as e:
            logger.warning(f"[discuss] 写评论失败 task={task_id}: {e}")

        if progress_done_payload:
            try:
                await _broadcast_ai_progress_await(
                    task_id, progress_done_payload[0], progress_done_payload[1], "done",
                )
            except Exception as e:
                logger.warning(f"[discuss] 收尾进度广播失败 task={task_id}: {e}")

        total_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "task_id": task_id,
            "reply": reply.strip(),
            "comment_id": None,
            "reasoning_trace": reasoning_trace,  # 透明化 planning（G6）
            "_trace": self._pop_trace(),
            "_total_ms": total_ms,
        }
