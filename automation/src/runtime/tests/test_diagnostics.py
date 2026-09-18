from automation.src.runtime.diagnostics import classify_failure, diagnose_run
from automation.src.runtime.trace_store import TraceStore


def test_classify_dependency_failure():
    status = {"status": "failed", "summary": "pytest exit status: 2"}
    steps = [{"status": "failed", "error": "ModuleNotFoundError: No module named 'tenacity'"}]
    assert classify_failure(status, steps) == "dependency_missing"


def test_classify_environment_failure():
    status = {"status": "failed", "summary": "connection failed"}
    steps = [{"status": "failed", "error": "Connection refused while connecting to API"}]
    assert classify_failure(status, steps) == "environment_unreachable"


def test_classify_assertion_failure():
    status = {"status": "failed", "summary": "1 failed"}
    steps = [{"status": "failed", "error": "AssertionError: expected 200, got 500"}]
    assert classify_failure(status, steps) == "assertion_failure"


def test_diagnose_passed_run_has_no_next_steps(tmp_path):
    store = TraceStore(run_root=tmp_path / "runs")
    from automation.src.runtime import RunContext

    context = RunContext.create(scenario="diagnostic")
    store.start_run(context)
    store.record_step(context.run_id, {"step": "test", "status": "passed"})
    store.finish_run(context.run_id, "passed", exit_code=0)

    result = diagnose_run(context.run_id, store=store)

    assert result["failure_class"] is None
    assert result["next_steps"] == []