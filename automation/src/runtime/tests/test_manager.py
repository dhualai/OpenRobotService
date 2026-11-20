from automation.src.runtime import RunManager, TraceStore


def test_run_manager_create_get_finish(tmp_path):
    manager = RunManager(TraceStore(run_root=tmp_path / "runs"))

    context = manager.create_run("api_mock", profile="fast", env="local")
    run = manager.get_run(context.run_id)

    assert run["run"]["scenario"] == "api_mock"
    assert run["status"]["status"] == "running"
    assert run["steps"] == []

    manager.finish_run(context.run_id, "passed", exit_code=0)
    assert manager.get_run(context.run_id)["status"]["status"] == "passed"
    assert manager.list_runs()[0]["run_id"] == context.run_id