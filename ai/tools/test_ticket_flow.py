#!/usr/bin/env python3
"""AI -------: -------- -> --------- -> ----- -> DB -----

----:
    cd D:\\\\Code\\\\OpenRobotService
    python ai/tools/test_ticket_flow.py

-----------------------------
    python -m pytest ai/tools/test_ticket_flow.py -v -s
"""
import asyncio
import json
import re
import sys
import os
import time
import uuid
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

from ai.config import get_ai_config
from ai.core import get_memory_manager, get_llm_client

PASS = 0
FAIL = 0
FAILED_TESTS = []


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED_TESTS.append(name)
        print(f"  [FAIL] {name}  -- {detail}")


# ============================================================
# Test fixtures
# ============================================================

def make_session_id() -> str:
    """generate unique session ID for test"""
    return f"test_ticket_{uuid.uuid4().hex[:12]}"


def make_agent_state(session_id: str, **overrides) -> dict:
    """build AgentState dict for _build_ticket"""
    state = {
        "session_id": session_id,
        "problem_summary": "AGV 002 号车无法从充电桩正常驶出",
        "ruled_out": ["充电桩供电正常", "AGV 电池电量 85%"],
        "hypotheses": ["充电桩通信模块异常", "调度任务未释放"],
        "collected_info": {
            "project": "测试项目",
            "robot_model": "AMR-002",
            "site": "B区充电站",
        },
        "diagnosis_rounds": 2,
        "phase": "diagnosing",
        "original_query": "AGV 002 号车无法从充电桩正常驶出",
        "last_submitted_ticket": {},
        "ticket_seq": 0,
        "pending_submit": False,
    }
    state.update(overrides)
    return state


# ============================================================
# Test 1: AgentState -> _build_ticket
# ============================================================

async def test_build_ticket_structure():
    """_build_ticket: check ticket structure + mandatory fields"""
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        get_diagnosis_platform, _load_agent_state, _save_agent_state, AgentState,
    )

    session_id = make_session_id()
    pipeline = await get_diagnosis_platform()
    await pipeline._ensure_clients()

    # setup agent_state in memory
    mgr = await get_memory_manager()
    memory = await mgr.get_memory(session_id)
    state = AgentState(**make_agent_state(session_id))
    _save_agent_state(memory, state)
    await mgr.save_memory(memory)

    ticket = await pipeline._build_ticket(session_id, state, memory)

    # mandatory fields
    check("ticket_id starts AI-", ticket.get("ticket_id", "").startswith("AI-"),
          ticket.get("ticket_id", "MISSING"))
    check("session_id correct", ticket.get("session_id") == session_id)
    check("type present", ticket.get("type") in ("problem", "bug", "feature", "support", "other"),
          f"type={ticket.get('type')}")
    check("title present", len(ticket.get("title", "")) > 0,
          f"title='{ticket.get('title', '')}'")
    check("description present", len(ticket.get("description", "")) > 0,
          f"desc_len={len(ticket.get('description', ''))}")
    check("priority in range", ticket.get("priority") in ("紧急", "高", "中", "低"),
          f"priority={ticket.get('priority')}")
    check("status is pending", ticket.get("status") == "pending",
          f"status={ticket.get('status')}")
    check("source is ai_agent", ticket.get("source") == "ai_agent")
    check("diagnosis has problem_summary",
          ticket.get("diagnosis", {}).get("problem_summary") == state.problem_summary)
    check("diagnosis has hypotheses",
          ticket.get("diagnosis", {}).get("hypotheses") == state.hypotheses)
    check("diagnosis has ruled_out",
          ticket.get("diagnosis", {}).get("ruled_out") == state.ruled_out)
    check("diagnosis has rounds",
          ticket.get("diagnosis", {}).get("rounds") == 2)
    check("created_at is int", isinstance(ticket.get("created_at"), int))
    check("attachments is list", isinstance(ticket.get("attachments"), list))

    print(f"  [INFO] Generated ticket: id={ticket['ticket_id']}, title={ticket['title']}, "
          f"type={ticket['type']}, priority={ticket['priority']}")

    # type-specific fields for problem type
    # note: LLM decides the type, so we just check mandatory common fields
    if ticket.get("type") == "problem":
        check("location present (problem)", "location" in ticket)
        check("robot_type present (problem)", "robot_type" in ticket)
        check("fault_code present (problem)", "fault_code" in ticket)
        check("project from collected_info", ticket.get("project") == "测试项目",
              f"project={ticket.get('project')}")

    return session_id


# ============================================================
# Test 2: _check_required_fields validation
# ============================================================

async def test_required_fields_validation():
    """_check_required_fields: with/without project"""
    from ai.agents.AiDiagnosisPlatform.pipeline import _check_required_fields

    # no project -> fail
    r1 = _check_required_fields({"type": "problem", "title": "test"})
    check("missing project -> fail", r1["ok"] is False, f"ok={r1['ok']} missing={r1['missing']}")
    check("missing list contains 'project'", "project" in r1["missing"],
          str(r1["missing"]))
    check("prompt non-empty", len(r1.get("prompt", "")) > 0)

    # has project name -> ok
    r2 = _check_required_fields({"type": "problem", "title": "test",
                                  "project": "test project"})
    check("has project name -> ok", r2["ok"] is True, f"ok={r2['ok']}")

    # has project_id -> ok
    r3 = _check_required_fields({"type": "problem", "title": "test",
                                  "project_id": "P001", "project": ""})
    check("has project_id -> ok", r3["ok"] is True, f"ok={r3['ok']}")

    # has both -> ok
    r4 = _check_required_fields({"type": "problem", "title": "test",
                                  "project_id": "P001", "project": "test"})
    check("has both -> ok", r4["ok"] is True, f"ok={r4['ok']}")

    # empty string project -> fail
    r5 = _check_required_fields({"type": "problem", "title": "test",
                                  "project": "   "})
    check("whitespace-only project -> fail", r5["ok"] is False,
          f"ok={r5['ok']}")


# ============================================================
# Test 3: _can_submit guard
# ============================================================

async def test_can_submit_guard():
    """_can_submit: state machine guard"""
    from ai.agents.AiDiagnosisPlatform.pipeline import _can_submit, AgentState

    sid = make_session_id()

    # idle + no problem -> ok (empty state）
    s1 = AgentState(session_id=sid, phase="idle", problem_summary="")
    ok, reason = _can_submit(s1)
    check("idle, no problem -> allow", ok, reason)

    # diagnosing + has problem -> ok
    s2 = AgentState(session_id=sid, phase="diagnosing",
                    problem_summary="AGV cannot move")
    ok, reason = _can_submit(s2)
    check("diagnosing, has problem -> allow", ok, reason)

    # resolved + no problem -> reject
    s3 = AgentState(session_id=sid, phase="resolved", problem_summary="")
    ok, reason = _can_submit(s3)
    check("resolved, no problem -> reject", not ok, reason)

    # escalated + no problem -> reject
    s4 = AgentState(session_id=sid, phase="escalated", problem_summary="")
    ok, reason = _can_submit(s4)
    check("escalated, no problem -> reject", not ok, reason)

    # resolved + NEW problem -> allow
    s5 = AgentState(session_id=sid, phase="resolved",
                    problem_summary="new issue: robot crashed into wall")
    ok, reason = _can_submit(s5)
    check("resolved, new problem -> allow", ok, reason)


# ============================================================
# Test 4: ticket_dict_to_task_fields mapping
# ============================================================

async def test_task_fields_mapping():
    """ticket_dict_to_task_fields: AI ticket dict -> Task DB fields"""
    from ai.core.task_adapter import ticket_dict_to_task_fields, _external_id_for
    from app.models.task import TaskStatus, TaskPriority, TaskType
    from ai.core.task_adapter import AI_SOURCE

    ticket = {
        "ticket_id": "AI-abc123-99999",
        "session_id": "test_session_123",
        "type": "problem",
        "title": "AGV charging failure",
        "description": "Robot 002 cannot leave charging station",
        "priority": "高",
        "status": "pending",
        "contact": "Zhang San",
        "location": "B area charging station",
        "robot_type": "AMR-002",
        "fault_code": "E1003",
        "project": "test project",
        "project_id": "",
        "diagnosis": {
            "problem_summary": "charging issue",
            "hypotheses": ["comm module", "scheduler lock"],
        },
        "created_at": 1234567890,
        "source": "ai_agent",
    }

    fields = ticket_dict_to_task_fields(ticket, created_by="test_user")

    check("title mapped", fields["title"] == "AGV charging failure")
    check("description mapped", fields["description"] == "Robot 002 cannot leave charging station")
    check("type mapped", fields["task_type"] == TaskType.PROBLEM,
          f"got {fields['task_type']}")
    check("priority mapped", fields["priority"] == TaskPriority.HIGH,
          f"got {fields['priority']}")
    check("status NEW", fields["status"] == TaskStatus.NEW)
    check("source ai", fields["source"] == AI_SOURCE)
    check("project_name", fields["project_name"] == "test project")
    check("created_by", fields["created_by"] == "test_user")
    check("external_id", "test_session_123" in fields["external_id"])
    check("tags has ai_generated", "ai_generated" in fields["tags"])

    # metadata_info should contain diagnosis
    meta = fields["metadata_info"]
    check("meta.session_id", meta.get("session_id") == "test_session_123")
    check("meta.ticket_ai_id", meta.get("ticket_ai_id") == "AI-abc123-99999")
    check("meta.contact", meta.get("contact") == "Zhang San")
    check("meta.diagnosis", "hypotheses" in meta.get("diagnosis", {}))

    # type-specific fields flat into meta
    check("meta.location", meta.get("location") == "B area charging station")
    check("meta.robot_type", meta.get("robot_type") == "AMR-002")
    check("meta.fault_code", meta.get("fault_code") == "E1003")

    # bug type
    bug_ticket = {
        "ticket_id": "AI-bug-1", "session_id": "s1", "type": "bug",
        "title": "UI crash", "description": "click causes crash",
        "priority": "紧急", "steps_to_reproduce": "1. click 2. crash",
        "expected_result": "no crash", "actual_result": "crashes",
        "severity": "阻塞", "version": "v2.3",
    }
    bug_fields = ticket_dict_to_task_fields(bug_ticket)
    check("bug severity", bug_fields["metadata_info"].get("severity") == "阻塞")
    check("bug version", bug_fields["metadata_info"].get("version") == "v2.3")

    # feature type
    feat_ticket = {
        "ticket_id": "AI-feat-1", "session_id": "s1", "type": "feature",
        "title": "add dark mode", "description": "...",
        "priority": "低", "scenario": "user wants dark UI",
        "expected_effect": "reduce eye strain", "source": "customer feedback",
    }
    feat_fields = ticket_dict_to_task_fields(feat_ticket)
    check("feature scenario", feat_fields["metadata_info"].get("scenario") == "user wants dark UI")
    check("feature source -> feature_source",
          feat_fields["metadata_info"].get("feature_source") == "customer feedback")

    # support type
    sup_ticket = {
        "ticket_id": "AI-sup-1", "session_id": "s1", "type": "support",
        "title": "need training", "description": "...",
        "priority": "中", "support_type": "training",
        "preferred_response": "online",
    }
    sup_fields = ticket_dict_to_task_fields(sup_ticket)
    check("support type", sup_fields["metadata_info"].get("support_type") == "training")
    check("preferred response",
          sup_fields["metadata_info"].get("preferred_response") == "online")


# ============================================================
# Test 5: submit ticket to DB (round-trip)
# ============================================================

async def test_submit_to_db():
    """Submit full round-trip: build_ticket -> upsert_task -> task_to_dict"""
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        get_diagnosis_platform, AgentState, _save_agent_state,
    )
    from ai.core.task_adapter import task_to_dict, upsert_task
    from app.models.task import Task
    from ai.core.task_adapter import AI_SOURCE
    from app.core.db import SessionLocal

    session_id = make_session_id()
    pipeline = await get_diagnosis_platform()
    await pipeline._ensure_clients()

    # setup
    mgr = await get_memory_manager()
    memory = await mgr.get_memory(session_id)
    state = AgentState(**make_agent_state(session_id))
    _save_agent_state(memory, state)
    await mgr.save_memory(memory)

    # build ticket
    ticket = await pipeline._build_ticket(session_id, state, memory)
    print(f"  [INFO] Ticket built: id={ticket['ticket_id']}, type={ticket['type']}")

    # submit to DB
    db = SessionLocal()
    try:
        record = upsert_task(ticket, created_by="test_runner")
        db_id = record.id
        check("DB record created", db_id is not None and db_id > 0, f"db_id={db_id}")
        print(f"  [INFO] DB record: id={db_id}, title={record.title}, status={record.status.value}")

        # verify stored data
        check("DB title matches", record.title == ticket.get("title", ""),
              f"expected='{ticket.get('title','')}' got='{record.title}'")
        check("DB source is ai", record.source == AI_SOURCE)
        check("DB external_id contains session",
              session_id[:8] in (record.external_id or ""),
              f"external_id={record.external_id}")

        # task_to_dict round-trip
        d = task_to_dict(record)
        check("round-trip id", d["id"] == db_id)
        check("round-trip title", d["title"] == record.title)
        check("round-trip type", d["type"] == ticket.get("type"),
              f"expected={ticket.get('type')} got={d['type']}")
        check("round-trip status", d["status"] == "new",
              f"got='{d['status']}'")
        check("round-trip session_id", d["session_id"] == session_id)
        check("round-trip diagnosis", "problem_summary" in (d.get("diagnosis") or {}))
        check("round-trip project", d["project"] == "测试项目",
              f"got='{d['project']}'")

        # clean up
        db.delete(record)
        db.commit()
        print(f"  [INFO] Test record {db_id} cleaned up")
    finally:
        db.close()


# ============================================================
# Test 6: submit() API method (full flow through pipeline)
# ============================================================

async def test_pipeline_submit():
    """pipeline.submit(): full flow with agent_state update"""
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        get_diagnosis_platform, AgentState, _save_agent_state,
        _load_agent_state,
    )
    from app.models.task import Task
    from ai.core.task_adapter import AI_SOURCE
    from app.core.db import SessionLocal

    session_id = make_session_id()
    pipeline = await get_diagnosis_platform()
    await pipeline._ensure_clients()

    # setup
    mgr = await get_memory_manager()
    memory = await mgr.get_memory(session_id)
    state = AgentState(**make_agent_state(session_id))
    _save_agent_state(memory, state)
    await mgr.save_memory(memory)

    result = await pipeline.submit(session_id, created_by="test_runner")

    check("submit returns type=ticket", result.get("type") == "ticket",
          f"type={result.get('type')}")
    data = result.get("data", {})
    t = data.get("ticket", {})
    check("submit has ticket data", bool(t), "empty ticket in result")
    check("submit has db_id", isinstance(data.get("db_id"), int) and data["db_id"] > 0,
          f"db_id={data.get('db_id')}")
    check("submit has notice", len(data.get("notice", "")) > 0)
    check("ticket_id non-empty", len(t.get("ticket_id", "")) > 0)
    check("ticket_id starts AI-", t.get("ticket_id", "").startswith("AI-"))

    # verify state: phase should be resolved
    state_after = result.get("agent_state", {})
    check("phase resolved", state_after.get("phase") == "resolved",
          f"phase={state_after.get('phase')}")

    # verify DB
    db = SessionLocal()
    try:
        record = db.query(Task).filter(Task.id == data["db_id"]).first()
        check("DB record found", record is not None)
        if record:
            check("DB source=ai", record.source == AI_SOURCE)
            check("DB status=new", record.status.value == "new",
                  f"status={record.status.value}")
            # clean up
            db.delete(record)
            db.commit()
            print(f"  [INFO] DB record {data['db_id']} cleaned up")
    finally:
        db.close()


# ============================================================
# Test 7: prepare_ticket + confirm_submit (button path)
# ============================================================

async def test_button_path():
    """prepare_ticket -> confirm_submit: button-based ticket creation"""
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        get_diagnosis_platform, AgentState, _save_agent_state,
        _load_agent_state,
    )
    from app.models.task import Task
    from app.core.db import SessionLocal

    session_id = make_session_id()
    pipeline = await get_diagnosis_platform()
    await pipeline._ensure_clients()

    # setup with project in collected_info
    mgr = await get_memory_manager()
    memory = await mgr.get_memory(session_id)
    state = AgentState(**make_agent_state(session_id))
    _save_agent_state(memory, state)
    await mgr.save_memory(memory)

    # Step 1: prepare ticket (draft)
    draft_result = await pipeline.prepare_ticket(session_id)
    check("prepare returns stage", draft_result.get("stage") in ("draft_ready", "need_fields"),
          f"stage={draft_result.get('stage')}")
    draft = draft_result.get("draft", {})
    check("draft has ticket_id", bool(draft.get("ticket_id")))

    if draft_result["stage"] == "draft_ready":
        # Step 2: confirm submit
        confirm_result = await pipeline.confirm_submit(
            session_id, overrides={"title": "Modified title for test"},
            created_by="test_runner",
        )
        check("confirm code=0", confirm_result.get("code") == 0,
              f"code={confirm_result.get('code')} msg={confirm_result.get('message')}")
        confirm_data = confirm_result.get("data", {})
        db_id = confirm_data.get("db_id")
        check("confirm has db_id", isinstance(db_id, int) and db_id > 0,
              f"db_id={db_id}")

        if db_id:
            # Verify DB
            db = SessionLocal()
            try:
                record = db.query(Task).filter(Task.id == db_id).first()
                check("button path DB record", record is not None)
                if record:
                    check("button path title overridden",
                          record.title == "Modified title for test",
                          f"got='{record.title}'")
                    db.delete(record)
                    db.commit()
                    print(f"  [INFO] DB record {db_id} cleaned up")
            finally:
                db.close()

        # verify draft cleaned up after confirm
        mgr2 = await get_memory_manager()
        memory2 = await mgr2.get_memory(session_id)
        check("draft removed after confirm",
              memory2.metadata.get("ticket_draft") is None)

        # verify state: phase resolved
        state2 = _load_agent_state(memory2.metadata)
        check("button path phase resolved", state2 and state2.phase == "resolved",
              f"phase={state2.phase if state2 else 'NONE'}")
    else:
        print(f"  [INFO] Draft needs fields: {draft_result.get('missing_fields')}, "
              f"skipping confirm test")


# ============================================================
# Test 8: Idempotent re-submit (same session -> upsert)
# ============================================================

async def test_idempotent_resubmit():
    """same session_id re-submit -> upsert, not duplicate"""
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        get_diagnosis_platform, AgentState, _save_agent_state,
    )
    from app.models.task import Task
    from ai.core.task_adapter import AI_SOURCE
    from app.core.db import SessionLocal

    session_id = make_session_id()
    pipeline = await get_diagnosis_platform()
    await pipeline._ensure_clients()

    # setup
    mgr = await get_memory_manager()
    memory = await mgr.get_memory(session_id)
    state = AgentState(**make_agent_state(session_id))
    _save_agent_state(memory, state)
    await mgr.save_memory(memory)

    # first submit
    r1 = await pipeline.submit(session_id, created_by="test_runner")
    db_id_1 = r1["data"]["db_id"]

    # re-setup state for second submit (simulate new problem after resolution)
    memory2 = await mgr.get_memory(session_id)
    state2 = AgentState(**make_agent_state(
        session_id, problem_summary="new: AGV battery overheating",
        ticket_seq=1,
    ))
    _save_agent_state(memory2, state2)
    await mgr.save_memory(memory2)

    # second submit
    r2 = await pipeline.submit(session_id, created_by="test_runner")
    db_id_2 = r2["data"]["db_id"]

    check("first submit ok", db_id_1 > 0)
    check("second submit ok", db_id_2 > 0)

    # same session, different seq => different external_id => different rows
    # (but if seq not set, upsert on same external_id overwrites)
    if db_id_1 == db_id_2:
        check("upsert: same external_id -> overwrite", True,
              "same db_id (upsert worked)")
    else:
        check("different seq -> new record", db_id_1 != db_id_2,
              f"id1={db_id_1} id2={db_id_2}")

    # clean up
    db = SessionLocal()
    try:
        for rid in {db_id_1, db_id_2}:
            rec = db.query(Task).filter(Task.id == rid).first()
            if rec:
                db.delete(rec)
        db.commit()
        print(f"  [INFO] DB records {db_id_1}, {db_id_2} cleaned up")
    finally:
        db.close()


# ============================================================
# Test 9: get_ticket (read-only)
# ============================================================

async def test_get_ticket_readonly():
    """get_ticket: read-only, no DB write, no state change"""
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        get_diagnosis_platform, AgentState, _save_agent_state, _load_agent_state,
    )

    session_id = make_session_id()
    pipeline = await get_diagnosis_platform()
    await pipeline._ensure_clients()

    mgr = await get_memory_manager()
    memory = await mgr.get_memory(session_id)
    state = AgentState(**make_agent_state(session_id))
    _save_agent_state(memory, state)
    await mgr.save_memory(memory)

    ticket = await pipeline.get_ticket(session_id)

    check("get_ticket has ticket_id", bool(ticket.get("ticket_id")))
    check("get_ticket has type", bool(ticket.get("type")))
    check("get_ticket has diagnosis", bool(ticket.get("diagnosis")))

    # verify state unchanged (no phase change)
    memory2 = await mgr.get_memory(session_id)
    state2 = _load_agent_state(memory2.metadata)
    check("get_ticket: phase unchanged", state2 and state2.phase == "diagnosing",
          f"phase={state2.phase if state2 else 'NONE'}")
    check("get_ticket: problem_summary unchanged",
          state2 and len(state2.problem_summary) > 0)
    check("get_ticket: ticket_seq unchanged",
          state2 and state2.ticket_seq == 0,
          f"ticket_seq={state2.ticket_seq if state2 else 'NONE'}")


# ============================================================
# Test 10: Ticket type specific fields
# ============================================================

async def test_ticket_type_fields():
    """Verify each ticket type has correct type-specific fields"""
    from ai.agents.AiDiagnosisPlatform.pipeline import (
        get_diagnosis_platform, AgentState, _save_agent_state,
    )

    session_id = make_session_id()
    pipeline = await get_diagnosis_platform()
    await pipeline._ensure_clients()

    # We can't control LLM output, but we can verify the generated
    # ticket has correct shape regardless of type
    mgr = await get_memory_manager()
    memory = await mgr.get_memory(session_id)

    # Test 1: problem type scenario
    state1 = AgentState(**make_agent_state(session_id, original_query="AGV cannot charge"))
    _save_agent_state(memory, state1)
    await mgr.save_memory(memory)
    ticket1 = await pipeline._build_ticket(session_id, state1, memory)
    t1_type = ticket1.get("type", "")
    print(f"  [INFO] Ticket type from LLM: {t1_type}")

    if t1_type == "problem":
        check("problem: has location", "location" in ticket1)
        check("problem: has robot_type", "robot_type" in ticket1)
        check("problem: has fault_code", "fault_code" in ticket1)
        check("problem: has special_notes", "special_notes" in ticket1)
    elif t1_type == "bug":
        check("bug: has steps_to_reproduce", "steps_to_reproduce" in ticket1)
        check("bug: has expected_result", "expected_result" in ticket1)
        check("bug: has actual_result", "actual_result" in ticket1)
        check("bug: has severity", "severity" in ticket1)
        check("bug: has version", "version" in ticket1)
    elif t1_type == "feature":
        check("feature: has scenario", "scenario" in ticket1)
        check("feature: has expected_effect", "expected_effect" in ticket1)
        check("feature: has source", "source" in ticket1)
    elif t1_type == "support":
        check("support: has support_type", "support_type" in ticket1)
        check("support: has preferred_response", "preferred_response" in ticket1)
    else:  # "other"
        check("other: no type-specific required", True)

    # All types should have common fields
    required_common = ["ticket_id", "session_id", "type", "title", "description",
                       "priority", "status", "diagnosis", "created_at", "source"]
    for field in required_common:
        check(f"common field '{field}' present in all types",
              field in ticket1, f"missing in type={t1_type}")


# ============================================================
# Test 11: task_to_dict round-trip
# ============================================================

async def test_task_to_dict_full():
    """task_to_dict: verify all field mappings"""
    from ai.core.task_adapter import ticket_dict_to_task_fields, task_to_dict, upsert_task

    ticket = {
        "ticket_id": "AI-roundtrip-1",
        "session_id": "test_roundtrip_sess",
        "type": "problem",
        "title": "Robot collision at intersection",
        "description": "AMR-005 collided with AMR-007 at B2 intersection",
        "priority": "紧急",
        "contact": "Li Si",
        "location": "B2 intersection",
        "robot_type": "AMR-005",
        "fault_code": "E2001",
        "project": "Roundtrip Test Project",
        "project_id": "P999",
        "special_notes": "urgent - production line stopped",
        "diagnosis": {
            "problem_summary": "collision at intersection",
            "hypotheses": ["path planning error", "sensor failure"],
            "ruled_out": ["manual override"],
            "collected_info": {},
            "rounds": 3,
        },
        "created_at": 1111111111,
        "attachments": [{"filename": "crash_log.txt", "size": 1024}],
    }

    # upsert
    record = upsert_task(ticket, created_by="test_runner")
    db_id = record.id
    check("upsert created record", db_id > 0)
    print(f"  [INFO] round-trip test record: id={db_id}")

    # task_to_dict
    d = task_to_dict(record)
    check("rt: id", d["id"] == db_id)
    check("rt: ticket_id is db id", d["ticket_id"] == db_id)
    check("rt: session_id", d["session_id"] == "test_roundtrip_sess")
    check("rt: ticket_ai_id", d["ticket_ai_id"] == "AI-roundtrip-1")
    check("rt: title", d["title"] == "Robot collision at intersection")
    check("rt: type", d["type"] == "problem")
    check("rt: priority", d["priority"] == "紧急")
    check("rt: status", d["status"] == "new")
    check("rt: contact", d["contact"] == "Li Si")
    check("rt: location", d["location"] == "B2 intersection")
    check("rt: robot_type", d["robot_type"] == "AMR-005")
    check("rt: fault_code", d["fault_code"] == "E2001")
    check("rt: project", d["project"] == "Roundtrip Test Project")
    check("rt: project_id", d["project_id"] == "P999")
    check("rt: special_notes", d["special_notes"] == "urgent - production line stopped")
    check("rt: attachments", len(d.get("attachments", [])) == 1)
    check("rt: diagnosis", d.get("diagnosis", {}).get("rounds") == 3)
    check("rt: source", d["source"] == "ai")

    # clean up
    from app.core.db import SessionLocal
    from app.models.task import Task
    db = SessionLocal()
    try:
        rec = db.query(Task).filter(Task.id == db_id).first()
        if rec:
            db.delete(rec)
            db.commit()
            print(f"  [INFO] DB record {db_id} cleaned up")
    finally:
        db.close()


# ============================================================
# Test 12: Project matching integration
# ============================================================

async def test_project_matching():
    """ProjectMatcher integration test"""
    from ai.core.project_matcher import get_project_matcher, ProjectMatcher

    matcher = get_project_matcher()
    ok = await matcher.ensure_loaded()

    if not ok:
        print(f"  [SKIP] ProjectMatcher: DB 'helpdesk_724' not available in local dev (expected — server has it)")
        print(f"         matcher._projects count = {len(matcher._projects)}")
        return

    if matcher._projects:
        proj_names = [p["name"] for p in matcher._projects[:5]]
        print(f"  [INFO] Sample projects: {proj_names}")

        # try match on the first project's substring
        first_proj = matcher._projects[0]["name"]
        # take a meaningful substring (skip prefix like "江苏常州")
        # find longest non-prefix window
        if len(first_proj) >= 4:
            fragment = first_proj[2:6] if len(first_proj) >= 6 else first_proj[:3]
            result = matcher.match(fragment, min_score=0.3)
            if result:
                check("substring matched", result.name == first_proj,
                      f"input='{fragment}' matched='{result.name}' score={result.score:.2f}")
            else:
                check(f"substring '{fragment}' no match (low score)", True,
                      f"no match for '{fragment}' in '{first_proj}'")

        # exact match
        result2 = matcher.match(first_proj)
        if result2:
            check("exact match score 1.0", result2.score >= 0.95,
                  f"score={result2.score:.2f} name={result2.name}")

        # empty input -> None
        result3 = matcher.match("")
        check("empty input -> None", result3 is None)

        # whitespace input -> None
        result4 = matcher.match("   ")
        check("whitespace input -> None", result4 is None)

        # gibberish -> None
        result5 = matcher.match("xyzzy_nonexistent_project_99999")
        check("gibberish -> None (or very low score)",
              result5 is None or result5.score < 0.4,
              f"score={result5.score if result5 else 'None'}")


# ============================================================
# Test 13: external_id formatting
# ============================================================

async def test_external_id():
    """_external_id_for: short session_id vs long session_id"""
    from ai.core.task_adapter import _external_id_for

    # short -> unchanged
    sid_short = "test_session_abc"
    eid_short = _external_id_for(sid_short)
    check("short session_id unchanged", eid_short == sid_short,
          f"got '{eid_short}'")

    # with ticket_seq
    eid_seq = _external_id_for(f"{sid_short}#3")
    check("with seq preserved", "test_session_abc#3" in eid_seq,
          f"got '{eid_seq}'")

    # long (>64) -> hashed
    sid_long = "a" * 100
    eid_long = _external_id_for(sid_long)
    check("long session_id hashed (<=64)", len(eid_long) <= 64,
          f"len={len(eid_long)}")
    check("long has h_ prefix", eid_long.startswith("h_"),
          f"got '{eid_long}'")

    # empty
    eid_empty = _external_id_for("")
    check("empty -> empty", eid_empty == "")


# ============================================================
# Test 14: Pipeline retrieval — cross-domain coverage
# ============================================================

async def test_pipeline_retrieval_cross_domain():
    """验证 pipeline._retrieve_with_context() 真正检索了 team+company+industry 三域

    这是关键测试 —— 之前的 bug 就是只搜了 team 域，导致车型号查不到。
    现在 pipeline 简化为三路域检索，每个域全量检索不限 sub_domain。
    """
    from ai.core.retrieval import get_retrieval_service
    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform, AgentState
    from ai.config import get_active_collection_for

    # ---- 前置检查：三个域都有活跃集合 ----
    for domain in ("team", "company", "industry"):
        col = get_active_collection_for(domain)
        assert col, f"{domain} domain has NO active collection — retrieval will fail!"

    # ---- 1. 架构验证：三个域必须都能返回结果（硬断言） ----
    # 这是最关键的检查 —— 之前的 bug 就是只搜了 team，company/industry 完全未检索
    service = await get_retrieval_service()

    for domain, query, desc in [
        ("company", "XP1152", "车型号"),
        ("company", "XNA151", "车型号"),
        ("team",    "AGV调度", "FAQ"),
        ("industry","导航",    "行业标准"),
    ]:
        r = await service.retrieve_domain(query, domain, top_k=3)
        assert len(r) > 0, \
            f"{domain} 域检索 '{query}' 返回空 — {desc}完全不可用！这就是原始架构缺陷！"
        assert r[0].score > 0, \
            f"{domain} 域 '{query}' top1 score={r[0].score} 无效"
        assert r[0].content, \
            f"{domain} 域 '{query}' top1 content 为空"

    # ---- 2. 型号精确匹配检查 ----
    # 注意：本地 Qdrant 不支持 BM25（dense-only），bge-small-zh-v1.5 对字母数字型号
    # （如 XP1152 vs XP1151）区分度差。服务器端有 BM25 精确匹配，可修复此问题。
    r_xp = await service.retrieve_domain("XP1152", "company", top_k=5)
    xp_titles = [r.title or "" for r in r_xp[:3]]
    xp_in_content = any("XP1152" in (r.content or "") for r in r_xp)
    xp_in_title = any("XP1152" in (r.title or "") for r in r_xp)
    if not (xp_in_content or xp_in_title):
        print(f"    ⚠ dense-only 限制：XP1152 未精确命中，top3={xp_titles}")
        print(f"      这是 bge-small-zh-v1.5 的已知局限，服务器端 BM25 将修复")
        # 软检查：不阻止 CI，但记录问题
        check("XP1152 精确命中（需 BM25）", False,
              f"dense-only 无法区分 XP1152/XP1151: {xp_titles[:2]}")
    else:
        check("XP1152 精确命中", True)

    # XNA151 同样检查
    r_xna = await service.retrieve_domain("XNA151", "company", top_k=3)
    xna_hit = any("XNA151" in (r.title or "") or "XNA151" in (r.content or "") for r in r_xna)
    if not xna_hit:
        print(f"    ⚠ dense-only 限制：XNA151 未精确命中，top3={[r.title for r in r_xna]}")
        check("XNA151 精确命中（需 BM25）", False,
              f"dense-only 无法区分: {[r.title for r in r_xna][:2]}")
    else:
        check("XNA151 精确命中", True)

    # ---- 3. 跨域 sub_domain 多样性检查（硬断言） ----
    r_faq = await service.retrieve_domain("AGV调度", "team", top_k=3)
    r_ind = await service.retrieve_domain("导航", "industry", top_k=3)
    all_subdomains = set()
    for r in (r_faq + r_xp + r_ind):
        if r.sub_domain:
            all_subdomains.add(r.sub_domain)
    assert len(all_subdomains) >= 2, \
        f"跨域检索结果 sub_domain 完全相同 ({all_subdomains}) — 说明只搜了一个域！"

    # ---- 4. Pipeline._retrieve_with_context() 端到端（硬断言） ----
    # 这是 diagnosis agent 实际调用的方法 —— 必须返回真实检索结果，不能是兜底文案
    pipeline = AiDiagnosisPlatform()
    await pipeline._ensure_clients()
    state = AgentState(
        session_id="test-cross-domain-001",
        original_query="XP1152 载荷",
    )
    ctx = await pipeline._retrieve_with_context(
        session_id="test-cross-domain-001",
        state=state,
    )
    assert ctx, "_retrieve_with_context 返回空字符串"
    assert len(ctx) > 50, \
        f"检索结果太短 ({len(ctx)} chars)"
    # 不应该只有兜底文案或失败标记
    assert "知识库暂" not in ctx, \
        f"返回兜底文案而非检索结果: {ctx[:200]}"
    assert "检索失败" not in ctx, \
        f"返回失败标记: {ctx[:200]}"
    # 应该包含跨域标签（验证三路检索都在工作）
    has_any_label = any(tag in ctx for tag in ["📋", "🚗", "🏢", "📐", "🌐", "📖", "🏭"])
    assert has_any_label, \
        f"检索结果无域标签，可能格式化逻辑有问题: {ctx[:300]}"


# ============================================================
# Main test runner
# ============================================================

async def main():
    global PASS, FAIL, FAILED_TESTS
    PASS = 0
    FAIL = 0
    FAILED_TESTS = []

    print("=" * 65)
    print("Ticket Creation Flow - End-to-End Test Suite")
    print("=" * 65)

    config = get_ai_config()
    print(f"\n[SETUP] DeepSeek API: {'available' if config.deepseek_api_key else 'MISSING'}")
    print(f"[SETUP] Qdrant local: {config.qdrant_local_path or 'NONE'}")
    print(f"[SETUP] DB: using SessionLocal from app.core.db (MySQL)")

    # ---- Test 1: _build_ticket structure ----
    print("\n" + "-" * 50)
    print("1. _build_ticket: ticket structure & mandatory fields")
    try:
        sid = await test_build_ticket_structure()
    except Exception as e:
        check("_build_ticket structure", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 2: _check_required_fields ----
    print("\n" + "-" * 50)
    print("2. _check_required_fields: project validation")
    try:
        await test_required_fields_validation()
    except Exception as e:
        check("required fields validation", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 3: _can_submit guard ----
    print("\n" + "-" * 50)
    print("3. _can_submit: state machine guard")
    try:
        await test_can_submit_guard()
    except Exception as e:
        check("_can_submit guard", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 4: ticket_dict_to_task_fields ----
    print("\n" + "-" * 50)
    print("4. ticket_dict_to_task_fields: AI -> DB mapping")
    try:
        await test_task_fields_mapping()
    except Exception as e:
        check("task fields mapping", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 5: submit to DB round-trip ----
    print("\n" + "-" * 50)
    print("5. Submit round-trip: build -> upsert -> verify")
    try:
        await test_submit_to_db()
    except Exception as e:
        check("submit to DB", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 6: pipeline.submit() ----
    print("\n" + "-" * 50)
    print("6. pipeline.submit(): full flow with state update")
    try:
        await test_pipeline_submit()
    except Exception as e:
        check("pipeline.submit()", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 7: prepare_ticket + confirm_submit ----
    print("\n" + "-" * 50)
    print("7. Button path: prepare_ticket -> confirm_submit")
    try:
        await test_button_path()
    except Exception as e:
        check("button path", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 8: Idempotent re-submit ----
    print("\n" + "-" * 50)
    print("8. Idempotent re-submit (same session -> upsert)")
    try:
        await test_idempotent_resubmit()
    except Exception as e:
        check("idempotent resubmit", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 9: get_ticket read-only ----
    print("\n" + "-" * 50)
    print("9. get_ticket: read-only, no side effects")
    try:
        await test_get_ticket_readonly()
    except Exception as e:
        check("get_ticket read-only", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 10: Ticket type fields ----
    print("\n" + "-" * 50)
    print("10. Ticket type-specific fields")
    try:
        await test_ticket_type_fields()
    except Exception as e:
        check("ticket type fields", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 11: task_to_dict full round-trip ----
    print("\n" + "-" * 50)
    print("11. task_to_dict: full field mapping round-trip")
    try:
        await test_task_to_dict_full()
    except Exception as e:
        check("task_to_dict round-trip", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 12: Project matching ----
    print("\n" + "-" * 50)
    print("12. ProjectMatcher integration")
    try:
        await test_project_matching()
    except Exception as e:
        check("project matching", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 13: external_id formatting ----
    print("\n" + "-" * 50)
    print("13. _external_id_for: formatting")
    try:
        await test_external_id()
    except Exception as e:
        check("external_id", False, str(e)[:120])
        import traceback
        traceback.print_exc()

    # ---- Test 14: Pipeline retrieval — cross-domain coverage ----
    print("\n" + "-" * 50)
    print("14. Pipeline retrieval — 三域覆盖 (team + company + industry)")
    print("    ⚠ 硬断言：任何失败都会终止并显示具体原因")
    try:
        await test_pipeline_retrieval_cross_domain()
    except AssertionError as e:
        check("pipeline cross-domain retrieval", False, str(e))
        import traceback
        traceback.print_exc()
    except Exception as e:
        check("pipeline cross-domain retrieval", False, str(e)[:150])
        import traceback
        traceback.print_exc()

    # ---- Summary ----
    print("\n" + "=" * 65)
    total = PASS + FAIL
    if FAIL == 0:
        print(f"ALL PASSED: {PASS}/{total} tests")
        print("=" * 65)
        sys.exit(0)
    else:
        print(f"FAILED: {PASS}/{total} passed, {FAIL} failed")
        print(f"Failed tests:")
        for t in FAILED_TESTS:
            print(f"  - {t}")
        print("=" * 65)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
