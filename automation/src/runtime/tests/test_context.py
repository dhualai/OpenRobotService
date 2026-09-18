from automation.src.runtime import RunContext


def test_create_generates_ids_and_metadata():
    context = RunContext.create(
        scenario="business_chain",
        profile="fast",
        env="local",
        metadata={"ticket_id": "837"},
    )

    assert context.run_id.startswith("run-")
    assert context.trace_id.startswith("tr-")
    assert context.scenario == "business_chain"
    assert context.profile == "fast"
    assert context.env == "local"
    assert context.metadata["ticket_id"] == "837"


def test_round_trip_preserves_parent_ids():
    context = RunContext.create(
        scenario="real_lifecycle",
        run_id="run-parent-001",
        trace_id="tr-parent-001",
    )
    restored = RunContext.from_dict(context.to_dict())

    assert restored.run_id == "run-parent-001"
    assert restored.trace_id == "tr-parent-001"
    assert restored.scenario == "real_lifecycle"