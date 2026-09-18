def test_pytest_runtime_fixture_is_available(run_context, trace_store):
    assert run_context is not None
    assert trace_store is not None
    assert run_context.run_id
    assert run_context.trace_id
    assert trace_store.read_status(run_context.run_id)["status"] == "running"