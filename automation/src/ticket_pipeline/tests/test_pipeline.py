import json

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

    async def generate(self, ticket):
        output = self.output_root / str(ticket.id)
        output.mkdir(parents=True)
        (output / "manifest.json").write_text(
            json.dumps({"ticket_id": ticket.id, "review_status": "pending_review"}),
            encoding="utf-8",
        )
        (output / "cases.json").write_text("[]", encoding="utf-8")
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