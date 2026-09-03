"""Data-driven AI eval runner.

Loads golden cases from JSON (testdata/ai/) and runs them against the
real AI service via HTTP, evaluating L1 deterministic checks.

Usage:
    from automation.tests.ai.runner import load_ai_cases, run_ai_case
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import allure

from automation.src.ai_metrics import (
    check_schema,
    hit_ratio,
    recall_score,
    collection_hit,
    LLMJudgeClient,
    JudgeUnavailableError,
    judge_faithfulness,
    judge_rubric,
)

from automation.config.paths import FIXTURES_DIR

_DATA_DIR = FIXTURES_DIR / "ai"
_CACHE: Dict[str, List[dict]] = {}

# Collection name -> retrieval method on RetrievalService. "retrieve" is the
# mixed (RRF) route and returns a tuple; others return plain lists.
_COLLECTION_METHODS: Dict[str, str] = {
    "retrieve": "retrieve",
    "retrieve_faq": "retrieve_faq",
    "retrieve_platform_faq": "retrieve_platform_faq",
    "retrieve_troubleshooting": "retrieve_troubleshooting",
    "retrieve_cheduan": "retrieve_cheduan",
    "retrieve_translation": "retrieve_translation",
    "retrieve_task_resolutions": "retrieve_task_resolutions",
}


@dataclass
class EvalResult:
    """Result of evaluating one golden case."""
    case_id: str
    passed: bool = False
    checks: List[dict] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)
    responses: List[dict] = field(default_factory=list)

    @property
    def summary(self) -> str:
        failed = [c for c in self.checks if not c["passed"]]
        if not failed:
            return "all L1 checks passed"
        return "; ".join(f"{c['metric']}: {c['detail']}" for c in failed)


def load_ai_cases(suite: str) -> List[dict]:
    """Load golden cases for a suite from fixtures/ai/{suite}.json."""
    if suite in _CACHE:
        return _CACHE[suite]
    path = _DATA_DIR / f"{suite}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    _CACHE[suite] = data.get("cases", [])
    return _CACHE[suite]


def _add_check(result: EvalResult, metric: str, passed: bool, detail: str) -> None:
    result.checks.append({"layer": "l1", "metric": metric, "passed": passed, "detail": detail})


def _eval_l1(result: EvalResult, data: dict, expect: dict, case: dict) -> None:
    """L1 deterministic checks: code / action / phase / schema / keywords."""
    if "code" in expect:
        _add_check(result, "code", data.get("code") == expect["code"],
                   f"code={data.get('code')}, expected {expect['code']}")

    actions = expect.get("actions")
    if actions:
        actual = data.get("action")
        _add_check(result, "action", actual in actions,
                   f"action={actual!r}, allowed {actions}")

    phase = expect.get("phase")
    if phase:
        actual = (data.get("agent_state") or {}).get("phase")
        allowed = phase if isinstance(phase, list) else [phase]
        _add_check(result, "phase", actual in allowed,
                   f"phase={actual!r}, allowed {allowed}")

    rounds_min = expect.get("rounds_min")
    if rounds_min:
        actual = (data.get("agent_state") or {}).get("diagnosis_rounds", 0)
        _add_check(result, "rounds_min", actual >= rounds_min,
                   f"diagnosis_rounds={actual}, expected >= {rounds_min}")

    summary = expect.get("summary_contains")
    if summary:
        text = (data.get("agent_state") or {}).get("problem_summary", "")
        terms = [summary] if isinstance(summary, str) else summary
        ratio = hit_ratio(text, terms)
        _add_check(result, "summary_contains", ratio >= 1.0,
                   f"problem_summary={text[:60]!r}, keyword hit {ratio:.0%}")

    key_terms = expect.get("key_terms")
    if key_terms:
        message = data.get("message", "")
        ratio = hit_ratio(message, key_terms)
        threshold = expect.get("key_terms_threshold", 0.8)
        _add_check(result, "key_terms", ratio >= threshold,
                   f"keyword hit {ratio:.0%}, threshold {threshold:.0%}")

    message_contains = expect.get("message_contains")
    if message_contains:
        message = data.get("message", "")
        missing = [t for t in message_contains if t not in message]
        _add_check(result, "message_contains", not missing,
                   f"missing terms {missing}")

    schema = expect.get("schema")
    if schema:
        violations = check_schema(data, schema)
        _add_check(result, "schema", not violations,
                   f"violations: {violations or 'none'}")


async def run_ai_case(client, case: dict) -> EvalResult:
    """Execute a single golden case against the AI service.

    Runs all turns sequentially with a shared session_id; evaluates L1
    checks against the final response, then L2 (faithfulness) and L3
    (rubric) when the golden case requests them and a judge LLM is
    available (L2/L3 checks are skipped otherwise).

    Args:
        client: httpx.AsyncClient pointed at the AI service base URL.
        case: golden case dict (id / mode / turns / expect).
    """
    result = EvalResult(case_id=case["id"])
    session_id = case.get("session_id") or f"eval-{case['id']}"
    skip_retrieval = case.get("mode") == "skip_retrieval"

    for i, query in enumerate(case["turns"]):
        body: Dict[str, Any] = {"session_id": session_id, "query": query}
        if skip_retrieval:
            body["skip_retrieval"] = True
        r = await client.post("/api/ai/qa/ask", json=body)
        r.raise_for_status()
        data = r.json()
        result.responses.append(data)
        allure.attach(
            json.dumps({"turn": i + 1, "query": query, "response": data},
                       indent=2, ensure_ascii=False),
            name=f"turn-{i + 1}",
            attachment_type=allure.attachment_type.JSON,
        )

    if result.responses:
        expect = case.get("expect", {})
        _eval_l1(result, result.responses[-1], expect.get("l1", {}), case)
        judge = _get_judge()
        await _eval_l2(result, result.responses[-1], expect.get("l2", {}), judge)
        await _eval_l3(result, result.responses[-1], expect.get("l3", {}), judge)

    result.passed = all(c["passed"] for c in result.checks)
    return result


_judge_cache: Optional[LLMJudgeClient] = None
_judge_error: Optional[str] = None


def _get_judge() -> Optional[LLMJudgeClient]:
    """Lazily create the shared judge client (cached per session)."""
    global _judge_cache, _judge_error
    if _judge_cache is not None or _judge_error is not None:
        return _judge_cache
    try:
        project_root = str(Path(__file__).parents[3])
        _judge_cache = LLMJudgeClient.from_env(project_root=project_root)
    except JudgeUnavailableError as e:
        _judge_error = str(e)
        _judge_cache = None
    return _judge_cache


async def _eval_l2(result: EvalResult, data: dict, expect: dict, judge: Optional[LLMJudgeClient]) -> None:
    """L2 faithfulness: answer grounded in golden reference docs."""
    docs = expect.get("reference_docs")
    if not docs:
        return
    min_score = expect.get("faithfulness_min", 0.7)
    if judge is None:
        result.skipped.append({"layer": "l2", "metric": "faithfulness",
                               "detail": f"judge unavailable ({_judge_error or 'unknown'})"})
        return
    message = data.get("message", "")
    query = (data.get("agent_state") or {}).get("original_query") or ""
    out = await judge_faithfulness(query, message, docs, judge)
    passed = out["score"] >= min_score
    result.checks.append({"layer": "l2", "metric": "faithfulness", "passed": passed,
                          "detail": f"score={out['score']:.2f}, min {min_score}: {out['reason']}"})


async def _eval_l3(result: EvalResult, data: dict, expect: dict, judge: Optional[LLMJudgeClient]) -> None:
    """L3 rubric scoring: judge the answer quality 1-5."""
    rubric = expect.get("rubric")
    if not rubric:
        return
    min_score = expect.get("min_score", 4)
    if judge is None:
        result.skipped.append({"layer": "l3", "metric": "rubric",
                               "detail": f"judge unavailable ({_judge_error or 'unknown'})"})
        return
    message = data.get("message", "")
    query = (data.get("agent_state") or {}).get("original_query") or ""
    out = await judge_rubric(query, message, rubric, judge)
    passed = out["score"] >= min_score
    result.checks.append({"layer": "l3", "metric": "rubric", "passed": passed,
                          "detail": f"score={out['score']:.1f}/5, min {min_score}: {out['reason']}"})


def _load_retrieval_service():
    """Lazily import and construct the AI RetrievalService.

    Returns None when the AI runtime dependencies are not installed
    (ImportError), so callers can skip gracefully.
    """
    import os
    import sys

    project_root = Path(__file__).parents[3]
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    os.environ.setdefault("DOCS_PATH", "")

    try:
        from dotenv import load_dotenv

        load_dotenv(project_root / "ai" / ".env")
        from ai.core.retrieval import get_retrieval_service
    except ImportError as e:
        return None, f"AI runtime deps unavailable: {e}"
    return get_retrieval_service, None


async def run_rag_case(case: dict) -> dict:
    """Run a RAG retrieval recall case.

    Returns {"passed", "checks", "skipped_all", "detail"}. A collection is
    marked skipped (not failed) when retrieval returns empty or raises
    (collection not ingested / Qdrant down), so recall is only scored
    against collections that actually served results.
    """
    loader, err = _load_retrieval_service()
    if loader is None:
        return {"passed": False, "skipped_all": True, "checks": [],
                "detail": err or "ai.core unavailable"}

    try:
        service = await loader()
    except Exception as e:  # pragma: no cover - env dependent
        return {"passed": False, "skipped_all": True, "checks": [],
                "detail": f"RetrievalService init failed: {e}"}

    expect_hits: Dict[str, List[str]] = case.get("expect_hits", {})
    hits: Dict[str, Any] = {}
    details: List[str] = []

    for query in case["queries"]:
        for collection, terms in expect_hits.items():
            method = _COLLECTION_METHODS.get(collection)
            if method is None:
                hits.setdefault(collection, None)
                details.append(f"{collection}: unknown collection")
                continue
            try:
                fn = getattr(service, method)
                result = await fn(query, top_k=3) if method != "retrieve" else (await fn(query, top_k=3, check_confidence=False))
                results, _ = result if method == "retrieve" else (result, None)
            except Exception as e:
                hits.setdefault(collection, None)
                details.append(f"{collection}: {type(e).__name__} ({e})")
                continue
            if not results:
                hits.setdefault(collection, None)
                details.append(f"{collection}: empty (未入库或 Qdrant 不可用)")
                continue
            ok = collection_hit(results, terms)
            hits[collection] = ok
            details.append(f"{collection}: {'HIT' if ok else 'MISS'} ({len(results)} docs)")

    score = recall_score(hits, expect_hits)
    checks = []
    for c in score["missed"]:
        checks.append({"layer": "l2", "metric": f"recall:{c}", "passed": False,
                       "detail": f"expected terms not found: {expect_hits[c]}"})
    for c in score["skipped"]:
        checks.append({"layer": "l2", "metric": f"recall:{c}", "passed": True,
                       "detail": "skipped (unavailable/empty)"})
    if score["hit"]:
        checks.append({"layer": "l2", "metric": "recall", "passed": True,
                       "detail": f"hit {score['hit']}, recall={score['recall']:.0%}"})

    passed = not score["missed"] and any(c["passed"] for c in checks)
    detail = "; ".join(details)
    allure.attach(json.dumps({"id": case["id"], "hits": hits, "score": score,
                              "details": details}, indent=2, ensure_ascii=False),
                  name="retrieval-recall", attachment_type=allure.attachment_type.JSON)
    return {"passed": passed, "skipped_all": False, "checks": checks, "detail": detail}


def _load_assigner():
    """Lazily import the assign_ticket entry point.

    Returns (callable, None) or (None, error) when AI runtime deps missing.
    """
    import os
    import sys

    project_root = Path(__file__).parents[3]
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    try:
        from dotenv import load_dotenv

        load_dotenv(project_root / "ai" / ".env")
        from ai.agents.AiDiagnosisPlatform.assigner import assign_ticket
    except ImportError as e:
        return None, f"AI runtime deps unavailable: {e}"
    return assign_ticket, None


async def run_assigner_case(case: dict) -> dict:
    """Run an assigner golden case (L1 checks on AssignmentResult)."""
    assign_ticket, err = _load_assigner()
    if assign_ticket is None:
        return {"passed": False, "skipped_all": True, "checks": [],
                "detail": err or "ai.core unavailable"}

    try:
        result = await assign_ticket(
            title=case["title"],
            problem_description=case["problem_description"],
            ticket_id=f"eval-{case['id']}",
            session_id=f"eval-session-{case['id']}",
        )
    except Exception as e:  # pragma: no cover - env dependent
        return {"passed": False, "skipped_all": True, "checks": [],
                "detail": f"assign_ticket failed: {type(e).__name__} ({e})"}

    expect = case.get("expect", {}).get("l1", {})
    data = {
        "engineer_name": result.engineer_name,
        "engineer_id": result.engineer_id,
        "confidence_score": result.confidence_score,
        "decision_type": result.decision_type,
        "reasoning": result.reasoning,
    }
    checks = []

    schema = expect.get("schema")
    if schema:
        violations = check_schema(data, schema)
        checks.append({"layer": "l1", "metric": "schema", "passed": not violations,
                       "detail": f"violations: {violations or 'none'}"})

    allowed = expect.get("decision_types")
    if allowed:
        checks.append({"layer": "l1", "metric": "decision_type",
                       "passed": data["decision_type"] in allowed,
                       "detail": f"decision_type={data['decision_type']!r}, allowed {allowed}"})

    confidence_min = expect.get("confidence_min")
    if confidence_min is not None:
        checks.append({"layer": "l1", "metric": "confidence_min",
                       "passed": data["confidence_score"] >= confidence_min,
                       "detail": f"confidence={data['confidence_score']}, min {confidence_min}"})

    passed = all(c["passed"] for c in checks)
    allure.attach(json.dumps({"id": case["id"], "result": data, "checks": checks},
                             indent=2, ensure_ascii=False),
                  name="assigner-result", attachment_type=allure.attachment_type.JSON)
    return {"passed": passed, "skipped_all": False, "checks": checks,
            "detail": "; ".join(c["detail"] for c in checks)}


async def run_analysis_case(client, case: dict) -> dict:
    """Run a data-analysis golden case via POST /api/ai/analysis/analyze.

    Evaluates L1 (schema / analysis_type / keywords) and L3 (rubric,
    skipped when the judge LLM is unavailable).
    """
    body = {
        "data": case["data"],
        "data_source": case.get("data_source", "json"),
        "analysis_type": case.get("analysis_type", "general"),
        "question": case.get("question"),
    }
    r = await client.post("/api/ai/analysis/analyze", json=body)
    r.raise_for_status()
    data = r.json()

    expect = case.get("expect", {})
    checks = []

    l1 = expect.get("l1", {})
    analysis_type = l1.get("analysis_type")
    if analysis_type:
        checks.append({"layer": "l1", "metric": "analysis_type",
                       "passed": data.get("analysis_type") == analysis_type,
                       "detail": f"analysis_type={data.get('analysis_type')!r}, expected {analysis_type!r}"})

    key_terms = l1.get("key_terms")
    if key_terms:
        ratio = hit_ratio(data.get("summary", ""), key_terms)
        checks.append({"layer": "l1", "metric": "key_terms",
                       "passed": ratio >= 1.0,
                       "detail": f"summary keyword hit {ratio:.0%}: {data.get('summary', '')[:60]}"})

    schema = l1.get("schema")
    if schema:
        violations = check_schema(data, schema)
        checks.append({"layer": "l1", "metric": "schema", "passed": not violations,
                       "detail": f"violations: {violations or 'none'}"})

    judge = _get_judge()
    l3 = expect.get("l3", {})
    rubric = l3.get("rubric")
    if rubric:
        min_score = l3.get("min_score", 3)
        if judge is None:
            checks.append({"layer": "l3", "metric": "rubric", "passed": True,
                           "detail": f"skipped: judge unavailable ({_judge_error or 'unknown'})"})
        else:
            question = case.get("question") or "请分析"
            summary = data.get("summary", "")
            insights = "；".join(i.get("content", "") for i in data.get("insights", []))
            out = await judge_rubric(question, f"{summary}\n{insights}", rubric, judge)
            checks.append({"layer": "l3", "metric": "rubric",
                           "passed": out["score"] >= min_score,
                           "detail": f"score={out['score']:.1f}/5, min {min_score}: {out['reason']}"})

    passed = all(c["passed"] for c in checks)
    allure.attach(json.dumps({"id": case["id"], "request": body, "response": data,
                              "checks": checks}, indent=2, ensure_ascii=False),
                  name="analysis-result", attachment_type=allure.attachment_type.JSON)
    return {"passed": passed, "skipped_all": False, "checks": checks,
            "detail": "; ".join(c["detail"] for c in checks)}


async def run_analysis_chat_case(client, case: dict) -> dict:
    """Run an analysis-chat golden case via POST /api/ai/analysis/chat."""
    body = case["request"]
    r = await client.post("/api/ai/analysis/chat", json=body)
    r.raise_for_status()
    data = r.json()

    expect = case.get("expect", {})
    checks = []

    l1 = expect.get("l1", {})
    mode = l1.get("mode")
    if mode:
        checks.append({
            "layer": "l1",
            "metric": "mode",
            "passed": data.get("mode") == mode,
            "detail": f"mode={data.get('mode')!r}, expected {mode!r}",
        })

    analysis_required = l1.get("analysis_required")
    if analysis_required is not None:
        actual_has_analysis = isinstance(data.get("analysis"), dict)
        checks.append({
            "layer": "l1",
            "metric": "analysis_presence",
            "passed": actual_has_analysis == analysis_required,
            "detail": (
                f"analysis present={actual_has_analysis}, "
                f"expected {analysis_required}"
            ),
        })

    analysis_type = l1.get("analysis_type")
    if analysis_type:
        actual_type = (data.get("analysis") or {}).get("analysis_type")
        checks.append({
            "layer": "l1",
            "metric": "analysis_type",
            "passed": actual_type == analysis_type,
            "detail": (
                f"analysis_type={actual_type!r}, expected {analysis_type!r}"
            ),
        })

    answer_terms = l1.get("answer_terms")
    if answer_terms:
        ratio = hit_ratio(data.get("answer", ""), answer_terms)
        checks.append({
            "layer": "l1",
            "metric": "answer_terms",
            "passed": ratio >= 1.0,
            "detail": (
                f"answer keyword hit {ratio:.0%}: "
                f"{data.get('answer', '')[:80]}"
            ),
        })

    summary_terms = l1.get("summary_terms")
    if summary_terms:
        summary = (data.get("analysis") or {}).get("summary", "")
        ratio = hit_ratio(summary, summary_terms)
        checks.append({
            "layer": "l1",
            "metric": "summary_terms",
            "passed": ratio >= 1.0,
            "detail": f"summary keyword hit {ratio:.0%}: {summary[:80]}",
        })

    schema = l1.get("schema")
    if schema:
        violations = check_schema(data, schema)
        checks.append({
            "layer": "l1",
            "metric": "schema",
            "passed": not violations,
            "detail": f"violations: {violations or 'none'}",
        })

    judge = _get_judge()
    l3 = expect.get("l3", {})
    rubric = l3.get("rubric")
    if rubric:
        min_score = l3.get("min_score", 3)
        if judge is None:
            checks.append({
                "layer": "l3",
                "metric": "rubric",
                "passed": True,
                "detail": (
                    f"skipped: judge unavailable ({_judge_error or 'unknown'})"
                ),
            })
        else:
            question = body.get("question") or "请分析"
            answer = data.get("answer", "")
            out = await judge_rubric(question, answer, rubric, judge)
            checks.append({
                "layer": "l3",
                "metric": "rubric",
                "passed": out["score"] >= min_score,
                "detail": (
                    f"score={out['score']:.1f}/5, min {min_score}: "
                    f"{out['reason']}"
                ),
            })

    passed = all(c["passed"] for c in checks)
    allure.attach(
        json.dumps(
            {"id": case["id"], "request": body, "response": data, "checks": checks},
            indent=2,
            ensure_ascii=False,
        ),
        name="analysis-chat-result",
        attachment_type=allure.attachment_type.JSON,
    )
    return {
        "passed": passed,
        "skipped_all": False,
        "checks": checks,
        "detail": "; ".join(c["detail"] for c in checks),
    }
