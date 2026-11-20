"""Assigner（智能派单）配置热更新 API。

供后端在保存责任模块树 / 变更用户画像后调用，
让运行中的派单流水线重新加载模块树配置、失效召回与画像缓存。
"""
from fastapi import APIRouter, Body, HTTPException
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER_API")

assigner_router = APIRouter(prefix="/api/ai/assigner", tags=["Assigner配置"])


@assigner_router.post("/reload")
async def reload_config():
    """热更新派单配置：重载模块树 + 失效画像缓存。
    - flow.reload_config()：从 DB 重载模块树（module_tree / classify / keywords / anchors）
      并失效召回缓存；
    - invalidate_personnel_cache()：置空工程师画像缓存，下次派单懒加载时重拉最新画像。
    失败返回 500（而非 200），供后端 _notify_ai_reload 正确感知热更新是否成功。
    """
    try:
        from ai.agents.AiDiagnosisPlatform.assigner import (
            ensure_dispatch_ready,
            invalidate_personnel_cache,
        )
        flow = ensure_dispatch_ready()
        flow.reload_config()
        invalidate_personnel_cache()
        logger.info("Assigner 模块树配置与工程师画像已热更新")
        return {"status": "ok", "message": "assigner 模块树与画像已刷新"}
    except Exception as e:
        logger.exception("Assigner 配置热更新失败: %s", e)
        raise HTTPException(status_code=500, detail=f"热更新失败: {e}")


def _ok(data):
    return {"code": 0, "data": data}


@assigner_router.get("/debug/overview")
async def debug_overview():
    """开发者模式：当前簇缓存 + 历史工单/索引概况。不触发向量化。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import debug_overview as _overview
        return _ok(await _overview())
    except Exception as e:
        logger.exception("派单调试概览失败: %s", e)
        raise HTTPException(status_code=500, detail=f"概览失败: {e}")


@assigner_router.post("/debug/clusters/rebuild")
async def debug_rebuild_clusters():
    """开发者模式：清缓存并重新自动聚簇。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import debug_rebuild_clusters as _rebuild
        return _ok(await _rebuild())
    except Exception as e:
        logger.exception("重建问题簇失败: %s", e)
        raise HTTPException(status_code=500, detail=f"重建簇失败: {e}")


@assigner_router.post("/debug/history/reindex")
async def debug_reindex_history():
    """开发者模式：把已解决/已关闭工单按四栏模板写入 Qdrant。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import debug_reindex as _reindex
        return _ok(await _reindex())
    except Exception as e:
        logger.exception("补索引失败: %s", e)
        raise HTTPException(status_code=500, detail=f"补索引失败: {e}")


@assigner_router.post("/debug/reassign-stats")
async def debug_reassign_stats():
    """开发者模式：按转派弹窗三个固定类型汇总指标（不猜、不进学习）。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import (
            debug_reassign_stats as _stats,
        )
        return _ok(await _stats())
    except Exception as e:
        logger.exception("转派统计失败: %s", e)
        raise HTTPException(status_code=500, detail=f"转派统计失败: {e}")


@assigner_router.post("/debug/reassign-review")
async def debug_reassign_review(payload: dict = Body(...)):
    """开发者模式：人工给未标转派点选类型。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import (
            debug_review_reassign as _review,
        )
        return _ok(_review(payload.get("log_id"), payload.get("kind") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("转派审核失败: %s", e)
        raise HTTPException(status_code=500, detail=f"转派审核失败: {e}")


@assigner_router.post("/debug/clusters/params")
async def debug_cluster_params(payload: dict = Body(...)):
    """开发者模式：保存簇门槛并按新值重建簇。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import (
            debug_save_cluster_params as _save,
        )
        return _ok(await _save(payload or {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("保存簇门槛失败: %s", e)
        raise HTTPException(status_code=500, detail=f"保存簇门槛失败: {e}")


@assigner_router.get("/debug/archive-reviewers")
async def debug_archive_reviewers_get():
    """开发者模式：读取归档审核人（问题领域 → 多人）。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import (
            debug_get_archive_reviewers as _get,
        )
        return _ok(_get())
    except Exception as e:
        logger.exception("读取归档审核人失败: %s", e)
        raise HTTPException(status_code=500, detail=f"读取归档审核人失败: {e}")


@assigner_router.put("/debug/archive-reviewers")
async def debug_archive_reviewers_put(payload: dict = Body(...)):
    """开发者模式：保存归档审核人到 runtime_overrides。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import (
            debug_save_archive_reviewers as _save,
        )
        return _ok(_save(payload or {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("保存归档审核人失败: %s", e)
        raise HTTPException(status_code=500, detail=f"保存归档审核人失败: {e}")


@assigner_router.post("/debug/ui-atlas/scan")
async def debug_ui_atlas_scan(payload: dict = Body(...)):
    """开发者模式：对标准界面图只标难懂控件并提问（可带已有框做增量）。"""
    image_url = (payload or {}).get("image_url") or ""
    existing = (payload or {}).get("existing_regions") or []
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.ui_atlas_scan import (
            scan_ui_atlas_image,
        )
        return _ok(await scan_ui_atlas_image(str(image_url), existing_regions=existing))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("界面图鉴扫图失败: %s", e)
        raise HTTPException(status_code=500, detail=f"扫图失败: {e}")


@assigner_router.post("/debug/ui-atlas/explain")
async def debug_ui_atlas_explain(payload: dict = Body(...)):
    """开发者模式：解释手动画框区域含义。"""
    image_url = (payload or {}).get("image_url") or ""
    box = (payload or {}).get("box") or {}
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.ui_atlas_scan import (
            explain_ui_atlas_region,
        )
        return _ok(await explain_ui_atlas_region(str(image_url), box))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("界面图鉴解释失败: %s", e)
        raise HTTPException(status_code=500, detail=f"解释失败: {e}")


@assigner_router.post("/debug/ui-atlas/compare-chat-vlm")
async def debug_ui_atlas_compare_chat_vlm(payload: dict = Body(...)):
    """对照：同一张相似图，用正式对话上传看图 prompt 跑「不带图鉴 / 带图鉴」。"""
    image_url = (payload or {}).get("image_url") or ""
    try:
        from ai.core.vision_chat import (
            compare_chat_vlm_with_atlas,
        )
        return _ok(await compare_chat_vlm_with_atlas(
            str(image_url),
            product=str((payload or {}).get("product") or ""),
            iface_name=str((payload or {}).get("iface_name") or ""),
            page_caption=(payload or {}).get("page_caption"),
            regions=(payload or {}).get("regions") or [],
            dialog_context=str((payload or {}).get("dialog_context") or ""),
            image_name=str((payload or {}).get("image_name") or "similar_shot.png"),
            standard_image_url=str((payload or {}).get("standard_image_url") or ""),
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("界面图鉴对照试读失败: %s", e)
        raise HTTPException(status_code=500, detail=f"对照试读失败: {e}")


# ── 派单测试（模拟提单 + 分支覆盖）──

# 内存缓存：最近一次 pytest 跑完的 (passed, failed, timestamp)；避免每次刷新都重跑。
_pytest_cache: dict = {"passed": set(), "failed": set(), "ran_at": None}
_pytest_cache_ttl_seconds = 300  # 5 分钟内走缓存；超过则下次 refresh 重跑

# 会话级：记录模拟跑单覆盖过的分支 ID
_covered_by_run: set[str] = set()

# 分支树一旦定义就是固定的，所有可能的派单分支都在这里写出来，
# 每条分支都对应一个或多个 pytest 测试名（tests 字段）。
# 新增分支时务必同时给出对应的测试用例，不要只加分支不写测试。
_BRANCH_TREE = [
    {
        "id": "step0",
        "step": "Step 0",
        "title": "提单人指定",
        "desc": "用户在描述中写「指定处理人：张三」「转给张三」 → 直接指派",
        "children": [
            {"id": "step0-hit", "title": "命中指定人", "desc": "匹配到候选人 → 直接返回", "tests": ["test_step0"]},
            {"id": "step0-miss", "title": "未命中指定人", "desc": "写 tip 后进入后续流程", "tests": ["test_step0"]},
        ],
    },
    {
        "id": "preferred",
        "step": "倾向人",
        "title": "用户倾向处理人",
        "desc": "连续两次同一倾向人 ID → 无条件直派；首次画像完整才准入",
        "children": [
            {"id": "preferred-twice", "title": "连续两次确认直派", "desc": "同一 preferred_id 出现两次", "tests": ["test_pref_incomplete_guard"]},
            {"id": "pref-incomplete", "title": "画像不完整首次护栏", "desc": "不准入 + tip", "tests": ["test_pref_incomplete_guard"]},
        ],
    },
    {
        "id": "step1",
        "step": "Step 1",
        "title": "候选收紧",
        "desc": "部门路由(hard/soft/no_filter) → 产品收紧",
        "children": [
            {"id": "step1-hard", "title": "部门 hard_filter", "desc": "强部门信号 → 硬过滤", "tests": ["test_step1"]},
            {"id": "step1-soft", "title": "部门 soft_prior", "desc": "弱部门信号 → 软加权", "tests": ["test_step1"]},
            {"id": "step1-no-filter", "title": "部门 no_filter", "desc": "无部门信号 → 不过滤", "tests": ["test_step1"]},
            {"id": "step1-empty", "title": "收紧后无候选 → 回退全量", "desc": "收紧把人滤完了", "tests": ["test_step1"]},
        ],
    },
    {
        "id": "step2",
        "step": "Step 2",
        "title": "打标 + 模糊截断",
        "desc": "提单人/对接人/倾向人/原接单人只打标；dispatch_hint=severe → 跳 Step7",
        "children": [
            {"id": "step2-severe", "title": "severe 信号跳 Step7", "desc": "dispatch_hint=severe", "tests": ["test_step2", "test_e2e_flow"]},
            {"id": "step2-normal", "title": "正常流程继续", "desc": "无 severe 信号", "tests": ["test_step2"]},
        ],
    },
    {
        "id": "step3",
        "step": "Step 3",
        "title": "三路召回",
        "desc": "画像召回 + 相似工单召回 + 问题簇召回",
        "children": [
            {"id": "step3-llm", "title": "画像召回有命中", "desc": "LLM 看职责卡片推断", "tests": ["test_step3"]},
            {"id": "step3-similar", "title": "相似工单召回", "desc": "近邻旧单处理人（可空）", "tests": ["test_step3"]},
            {"id": "step3-cluster", "title": "问题簇召回", "desc": "问题堆里常客（可空）", "tests": ["test_step3"]},
            {"id": "step3-empty", "title": "三路全空", "desc": "无召回命中", "tests": ["test_step3"]},
        ],
    },
    {
        "id": "step4",
        "step": "Step 4",
        "title": "精排 + 职级折扣",
        "desc": "三路绝对 0~1 取最高；职级折扣；倾向人保底；对接人只打标",
        "children": [
            {"id": "step4-rank", "title": "正常精排", "desc": "按总分排序", "tests": ["test_step4"]},
            {"id": "step4-preferred-floor", "title": "倾向人保底分", "desc": "max(分, preferred_floor)", "tests": ["test_step4"]},
            {"id": "step4-union", "title": "三路并集补入", "desc": "历史捞回但不在收紧名单", "tests": ["test_step4"]},
        ],
    },
    {
        "id": "step6",
        "step": "Step 6",
        "title": "LLM 综合决策",
        "desc": "铁律 + 产品附录；失败/很难/名单外 → None",
        "children": [
            {"id": "step6-success", "title": "LLM 决策成功", "desc": "返回 AssignmentResult", "tests": ["test_step6", "test_e2e_flow"]},
            {"id": "step6-fail", "title": "LLM 返回 None", "desc": "交不出人 → Step7", "tests": ["test_step6", "test_e2e_flow"]},
            {"id": "step6-exception", "title": "LLM 异常", "desc": "抛异常 → Step7", "tests": ["test_step6"]},
        ],
    },
    {
        "id": "step7",
        "step": "Step 7",
        "title": "兜底",
        "desc": "对接人 → 本单项目经理 → 配置项目经理；都空则未指派 + tip",
        "children": [
            {"id": "step7-contact", "title": "派对接人", "desc": "工单有对接人", "tests": ["test_step7", "test_e2e_flow"]},
            {"id": "step7-project-pm", "title": "派本单项目经理", "desc": "项目配置了 PM", "tests": ["test_step7"]},
            {"id": "step7-config-pm", "title": "派配置项目经理", "desc": "全局 fallback PM", "tests": ["test_step7"]},
            {"id": "step7-unassignable", "title": "无法指派", "desc": "都没配 → 写 tip 不派人", "tests": ["test_step7"]},
        ],
    },
]


@assigner_router.get("/debug/test-branches")
async def debug_test_branches():
    """开发者模式：返回派单所有分支树 + 当前缓存的 pytest 覆盖状态。

    性能：默认秒级返回，不触发任何 pytest 进程。
    pytest 跑过的结果从内存缓存读（最近一次 refresh 写入）；
    缓存为空时所有分支显示为 untested，由前端点「刷新测试结果」触发跑测试。
    """
    try:
        _covered_by_run_count = len(_covered_by_run)
        branches, passed, failed = _build_branches_with_status(
            _pytest_cache["passed"], _pytest_cache["failed"], _covered_by_run,
        )
        return _ok({
            "branches": branches,
            "total_passed": len(passed),
            "total_failed": len(failed),
            "covered_by_run": _covered_by_run_count,
            "pytest_ran_at": _pytest_cache["ran_at"],
            "pytest_cache_hit": _pytest_cache["ran_at"] is not None,
        })
    except Exception as e:
        logger.exception("获取测试分支失败: %s", e)
        raise HTTPException(status_code=500, detail=f"获取测试分支失败: {e}")


@assigner_router.post("/debug/test-branches/refresh")
async def debug_test_branches_refresh():
    """开发者模式：手动触发 pytest 全量跑一次，覆盖状态写回内存缓存。

    缓存有效期默认 5 分钟；TTL 内重复触发会直接返回上次数据，并标注 cache_hit=true。
    """
    try:
        import subprocess, sys, time as _time
        from ai.core.logging import get_logger as _gl
        _log = _gl("ASSIGNER_API")

        # TTL 判断：缓存还在有效期内就不重跑，直接复用
        ran_at = _pytest_cache.get("ran_at")
        if ran_at is not None:
            age = _time.time() - float(ran_at)
            if age < _pytest_cache_ttl_seconds:
                _log.info("pytest 缓存命中 (age=%.1fs)，跳过重跑", age)
                branches, passed, failed = _build_branches_with_status(
                    _pytest_cache["passed"], _pytest_cache["failed"], _covered_by_run,
                )
                return _ok({
                    "branches": branches,
                    "total_passed": len(passed),
                    "total_failed": len(failed),
                    "covered_by_run": len(_covered_by_run),
                    "pytest_ran_at": _pytest_cache["ran_at"],
                    "pytest_cache_hit": True,
                    "cache_age_seconds": round(age, 1),
                    "ttl_seconds": _pytest_cache_ttl_seconds,
                })

        repo_root = _find_repo_root()
        venv_py = repo_root / ".venv" / "Scripts" / "python.exe"
        py_exe = str(venv_py) if venv_py.exists() else sys.executable
        test_dir = "ai/agents/AiDiagnosisPlatform/assigner/tests"
        proc = subprocess.run(
            [py_exe, "-m", "pytest", test_dir, "--tb=no", "-vv", "--no-header"],
            capture_output=True, text=True, timeout=120, cwd=str(repo_root),
        )
        passed: set = set()
        failed: set = set()
        for line in (proc.stdout + proc.stderr).splitlines():
            line = line.strip()
            # pytest 输出格式: "test_step0.py::TestX::test_y PASSED"
            if "::" in line and (" PASSED" in line or " FAILED" in line or " ERROR" in line):
                name = line.split(" ")[0]
                if " PASSED" in line:
                    passed.add(name)
                else:
                    failed.add(name)

        # 写回内存缓存
        _pytest_cache["passed"] = passed
        _pytest_cache["failed"] = failed
        _pytest_cache["ran_at"] = _time.time()

        branches, passed2, failed2 = _build_branches_with_status(
            passed, failed, _covered_by_run,
        )
        _log.info(
            "pytest 跑完：passed=%d failed=%d covered_by_run=%d",
            len(passed), len(failed), len(_covered_by_run),
        )
        return _ok({
            "branches": branches,
            "total_passed": len(passed2),
            "total_failed": len(failed2),
            "covered_by_run": len(_covered_by_run),
            "pytest_ran_at": _pytest_cache["ran_at"],
            "pytest_cache_hit": False,
            "cache_age_seconds": 0,
            "ttl_seconds": _pytest_cache_ttl_seconds,
        })
    except Exception as e:
        logger.exception("pytest 刷新失败: %s", e)
        raise HTTPException(status_code=500, detail=f"刷新测试分支失败: {e}")


def _build_branches_with_status(passed: set, failed: set, covered_by_run: set):
    """根据 passed/failed/covered_by_run 标记每一节点的 test_status，返回 (branches, passed, failed)。

    规则：
    - pytest 失败 → failed
    - pytest 通过 / 模拟跑单命中 → passed
    - 都没有 → untested
    """
    from ai.agents.AiDiagnosisPlatform.assigner.debug_scenarios import (
        build_real_profile_examples,
    )

    examples = build_real_profile_examples()
    for branch in _BRANCH_TREE:
        for child in branch.get("children", []):
            test_ids = child.get("tests", [])
            child_id = child.get("id", "")
            pytest_status = _compute_branch_status(test_ids, passed, failed)
            child["example"] = examples.get(child_id)
            if pytest_status == "untested" and child_id in covered_by_run:
                child["test_status"] = "passed"
            else:
                child["test_status"] = pytest_status
    return _BRANCH_TREE, passed, failed


def _compute_branch_status(test_ids, passed, failed):
    """根据关联的测试名计算分支状态。"""
    has_pass = False
    has_fail = False
    for tid in test_ids:
        for full_name in passed:
            if tid in full_name:
                has_pass = True
                break
        for full_name in failed:
            if tid in full_name:
                has_fail = True
                break
    if has_fail:
        return "failed"
    if has_pass:
        return "passed"
    return "untested"


def _find_repo_root():
    """从当前文件位置向上找到仓库根（含 .git 或 pyproject.toml）。"""
    from pathlib import Path
    p = Path(__file__).resolve()
    for _ in range(10):
        if (p / ".git").exists() or (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path.cwd()


@assigner_router.post("/debug/test-run")
async def debug_test_run(payload: dict = Body(...)):
    """开发者模式：模拟提单跑一次派单，返回结果 + 命中的分支。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner import (
            ensure_dispatch_ready, load_engineers,
        )
        from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
        import time

        # 构造工单上下文
        ticket_id = f"test_{int(time.time())}"
        title = (payload.get("title") or "").strip() or "测试工单"
        desc = (payload.get("problem_description") or "").strip() or "测试描述"
        if not title and not desc:
            raise ValueError("标题和描述不能都为空")

        remark = (payload.get("preferred_assignee_remark") or "").strip()
        if remark:
            desc = desc + f"\n【重新派单备注】{remark}"

        ctx = TicketContext(
            id=ticket_id,
            title=title,
            problem_description=desc,
            status=payload.get("status") or "new",
            priority=payload.get("priority"),
            ticket_type=payload.get("ticket_type"),
            robot_type=payload.get("robot_type") or None,
            fault_code=payload.get("fault_code") or None,
            dispatch_hint=payload.get("dispatch_hint") or None,
            preferred_assignee=payload.get("preferred_assignee") or None,
            preferred_assignee_remark=remark or None,
            prev_assignee=payload.get("prev_assignee") or None,
            project_name=payload.get("project_name") or None,
            project_id=payload.get("project_id") or None,
            contact=payload.get("contact") or None,
            creator=payload.get("creator") or None,
            location=payload.get("location") or None,
        )

        engineers = load_engineers()
        if not engineers:
            raise ValueError("工程师画像为空，请检查 users 表人员数据是否就绪")

        flow = ensure_dispatch_ready()
        result = await flow.aassign(ctx, engineers)

        # 推断命中的分支路径
        hit_path = _infer_hit_path(ctx, result, flow)

        # 记录命中分支到会话级覆盖集合
        for node in hit_path:
            nid = node.get("id") or ""
            if nid and nid != "done":
                _covered_by_run.add(nid)

        return _ok({
            "result": {
                "engineer_id": result.engineer_id,
                "engineer_name": result.engineer_name,
                "confidence_score": result.confidence_score,
                "decision_type": result.decision_type,
                "reasoning": result.reasoning,
                "preferred_id": result.preferred_id,
                "matched_pref": result.matched_pref,
                "profile": result.profile,
                "candidates": result.candidates,
            },
            "hit_path": hit_path,
            "candidate_count": len(engineers),
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("模拟派单失败: %s", e)
        raise HTTPException(status_code=500, detail=f"模拟派单失败: {e}")


def _infer_hit_path(ctx, result, flow) -> list:
    """根据结果反推命中的分支路径。"""
    path = []
    dt = result.decision_type or ""

    # Step 0
    if dt == "specified" or result.matched_pref and ctx.title and "指定" in ctx.title:
        path.append({"step": "Step 0", "branch": "命中指定人", "id": "step0-hit"})
        path.append({"step": "结果", "branch": f"直接指派 {result.engineer_name}", "id": "done"})
        return path
    path.append({"step": "Step 0", "branch": "未指定/未命中", "id": "step0-miss"})

    # 倾向人连续两次
    if dt == "preferred_confirm":
        path.append({"step": "倾向人", "branch": "连续两次确认直派", "id": "preferred-twice"})
        path.append({"step": "结果", "branch": f"直派 {result.engineer_name}", "id": "done"})
        return path

    # Step 1
    tighten = getattr(flow, "last_tighten", None)
    if tighten:
        dept_mode = tighten.dept.mode if tighten.dept else "no_filter"
        if dept_mode == "hard_filter":
            path.append({"step": "Step 1", "branch": "部门 hard_filter", "id": "step1-hard"})
        elif dept_mode == "soft_prior":
            path.append({"step": "Step 1", "branch": "部门 soft_prior", "id": "step1-soft"})
        else:
            path.append({"step": "Step 1", "branch": "部门 no_filter", "id": "step1-no-filter"})
        if tighten.after_count == 0:
            path.append({"step": "Step 1", "branch": "收紧后无候选 → 回退全量", "id": "step1-empty"})
    else:
        path.append({"step": "Step 1", "branch": "候选收紧", "id": "step1"})

    # Step 2
    if ctx.dispatch_hint == "severe":
        path.append({"step": "Step 2", "branch": "severe 信号跳 Step7", "id": "step2-severe"})
    else:
        path.append({"step": "Step 2", "branch": "正常流程继续", "id": "step2-normal"})

    # 如果 severe 跳了 Step7，后面不走了
    if ctx.dispatch_hint == "severe":
        path.append({"step": "Step 7", "branch": _step7_branch(result), "id": "step7"})
        path.append({"step": "结果", "branch": f"兜底 {result.engineer_name or '未指派'}", "id": "done"})
        return path

    # Step 3-6 (无法从结果精确推断每步，但可以推断最终决策来源)
    if dt == "auto" or dt == "recommend":
        path.append({"step": "Step 3", "branch": "三路召回", "id": "step3"})
        path.append({"step": "Step 4", "branch": "精排", "id": "step4"})
        path.append({"step": "Step 6", "branch": "LLM 决策成功", "id": "step6-success"})
    elif dt == "fallback":
        path.append({"step": "Step 3", "branch": "三路召回", "id": "step3"})
        path.append({"step": "Step 4", "branch": "精排", "id": "step4"})
        path.append({"step": "Step 6", "branch": "LLM 返回 None", "id": "step6-fail"})
        path.append({"step": "Step 7", "branch": _step7_branch(result), "id": "step7"})

    path.append({"step": "结果", "branch": f"{'指派' if result.engineer_id else '未指派'} {result.engineer_name or ''}", "id": "done"})
    return path


def _step7_branch(result) -> str:
    """推断 Step7 命中哪个兜底。"""
    reasoning = result.reasoning or ""
    if "对接人" in reasoning or "contact" in reasoning.lower():
        return "派对接人"
    if "项目经理" in reasoning:
        if "配置" in reasoning or "全局" in reasoning:
            return "派配置项目经理"
        return "派本单项目经理"
    if "无法" in reasoning or "暂时" in reasoning:
        return "无法指派"
    return "兜底"

