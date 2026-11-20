import json
from datetime import datetime, timezone

import pytest

from automation.src.ticket_pipeline import TicketPipeline
from automation.src.ticket_pipeline.models import TicketCandidate


class FakeScanner:
    def __init__(self, tickets):
        self.tickets = tickets

    def scan(self, limit=100):
        return self.tickets[:limit]


class FakeGenerator:
    def __init__(self, root):
        self.output_root = root
        self.calls = []

    async def generate(self, ticket):
        self.calls.append(ticket.id)
        output = self.output_root / str(ticket.id)
        output.mkdir(parents=True, exist_ok=True)
        (output / "manifest.json").write_text(
            json.dumps({"ticket_id": ticket.id, "review_status": "pending_review"}),
            encoding="utf-8",
        )
        (output / "cases.json").write_text("[]", encoding="utf-8")
        (output / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")
        return {"ticket_id": ticket.id, "output_dir": str(output), "case_count": 0}


def make_ticket(ticket_id):
    return TicketCandidate(
        id=ticket_id,
        title="title",
        description="description",
        task_type="feature",
        status="new",
        project_id="Leo_test",
        project_name="摇人号",
    )


@pytest.mark.asyncio
async def test_pipeline_run_and_promote(tmp_path):
    generator = FakeGenerator(tmp_path / "generated")
    pipeline = TicketPipeline(
        FakeScanner([make_ticket(837), make_ticket(838)]),
        generator,
        promoted_root=tmp_path / "promoted",
    )

    result = await pipeline.run(ticket_ids=[837])

    assert result["scanned"] == 2
    assert result["generated"] == 1
    assert result["skipped"] == 1

    manifest_path = tmp_path / "generated" / "837" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["review_status"] = "approved"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    promoted = pipeline.promote(837)
    assert promoted["status"] == "promoted"
    assert (tmp_path / "promoted" / "837" / "promotion.json").is_file()


def test_promote_requires_approval(tmp_path):
    generator = FakeGenerator(tmp_path / "generated")
    pipeline = TicketPipeline(FakeScanner([]), generator, promoted_root=tmp_path / "promoted")
    output = tmp_path / "generated" / "837"
    output.mkdir(parents=True)
    (output / "manifest.json").write_text('{"review_status":"pending_review"}', encoding="utf-8")

    with pytest.raises(ValueError, match="not approved"):
        pipeline.promote(837)


def test_promote_requires_executable_test_file(tmp_path):
    generator = FakeGenerator(tmp_path / "generated")
    pipeline = TicketPipeline(FakeScanner([]), generator, promoted_root=tmp_path / "promoted")
    output = tmp_path / "generated" / "837"
    output.mkdir(parents=True)
    (output / "manifest.json").write_text('{"review_status":"approved"}', encoding="utf-8")

    with pytest.raises(ValueError, match="no executable test file"):
        pipeline.promote(837)


def test_promote_copies_executable_test_file(tmp_path):
    generator = FakeGenerator(tmp_path / "generated")
    pipeline = TicketPipeline(FakeScanner([]), generator, promoted_root=tmp_path / "promoted")
    output = tmp_path / "generated" / "837"
    output.mkdir(parents=True)
    (output / "manifest.json").write_text('{"review_status":"approved"}', encoding="utf-8")
    (output / "test_gen.py").write_text("def test_ok(): pass", encoding="utf-8")

    pipeline.promote(837)

    assert (tmp_path / "promoted" / "837" / "test_gen.py").is_file()


@pytest.mark.asyncio
async def test_run_skips_already_generated_candidates(tmp_path):
    generator = FakeGenerator(tmp_path / "generated")
    pipeline = TicketPipeline(
        FakeScanner([make_ticket(837)]),
        generator,
        promoted_root=tmp_path / "promoted",
    )

    await pipeline.run()
    second = await pipeline.run()

    assert generator.calls == [837]
    assert second["generated"] == 0
    assert second["skipped"] == 1
    assert second["outputs"][0]["status"] == "skipped"


@pytest.mark.asyncio
async def test_run_force_regenerates_existing_candidates(tmp_path):
    generator = FakeGenerator(tmp_path / "generated")
    pipeline = TicketPipeline(
        FakeScanner([make_ticket(837)]),
        generator,
        promoted_root=tmp_path / "promoted",
    )

    await pipeline.run()
    second = await pipeline.run(force=True)

    assert generator.calls == [837, 837]
    assert second["generated"] == 1


@pytest.mark.asyncio
async def test_run_regenerates_when_ticket_was_updated(tmp_path):
    generator = FakeGenerator(tmp_path / "generated")
    updated = make_ticket(837)
    updated.updated_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pipeline = TicketPipeline(
        FakeScanner([updated]),
        generator,
        promoted_root=tmp_path / "promoted",
    )

    await pipeline.run()
    manifest_path = tmp_path / "generated" / "837" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_at"] = "2026-01-01T00:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    second = await pipeline.run()

    assert second["generated"] == 1