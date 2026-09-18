from automation.src.runtime import RunContext, TraceStore


def test_trace_store_lifecycle(tmp_path):
    store = TraceStore(run_root=tmp_path / "runs")
    context = RunContext.create(scenario="unit_trace", profile="fast", env="local")

    run_dir = store.start_run(context)
    assert run_dir.is_dir()
    assert store.read_run(context.run_id)["scenario"] == "unit_trace"
    assert store.read_status(context.run_id)["status"] == "running"

    store.record_step(context.run_id, {"step": "login", "status": "passed"})
    store.record_step(context.run_id, {"step": "create_ticket", "status": "passed"})
    steps = store.read_steps(context.run_id)
    assert [step["step"] for step in steps] == ["login", "create_ticket"]

    status = store.finish_run(context.run_id, "passed", exit_code=0)
    assert status["status"] == "passed"
    assert status["exit_code"] == 0
    assert store.read_status(context.run_id)["finished_at"]


def test_trace_store_preserves_parent_process_metadata(tmp_path):
    store = TraceStore(run_root=tmp_path / "runs")
    context = RunContext.create(scenario="subprocess", run_id="run-parent")
    store.start_run(context)
    store.update_run(context.run_id, command=["python", "-m", "pytest"], pid=1234)
    store.update_status(context.run_id, "running", pid=1234)

    store.start_run(RunContext.create(scenario="subprocess", run_id="run-parent"))

    run = store.read_run(context.run_id)
    assert run["pid"] == 1234
    assert run["command"] == ["python", "-m", "pytest"]


def test_trace_store_normalizes_invalid_terminal_status(tmp_path):
    store = TraceStore(run_root=tmp_path / "runs")
    context = RunContext.create(scenario="invalid_status")
    store.start_run(context)

    result = store.finish_run(context.run_id, "unknown")

    assert result["status"] == "error"