"""Orchestration for the ticket-driven test pipeline."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from automation.src.ticket_pipeline.models import TicketCandidate
from automation.src.ticket_pipeline.scanner import TicketScanner


@dataclass
class TicketPipelineResult:
    scanned: int = 0
    generated: int = 0
    skipped: int = 0
    errors: int = 0
    outputs: List[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scanned": self.scanned,
            "generated": self.generated,
            "skipped": self.skipped,
            "errors": self.errors,
            "outputs": self.outputs or [],
        }


class TicketPipeline:
    """Scan candidates, generate cases, and promote reviewed artifacts."""

    def __init__(
        self,
        scanner: TicketScanner,
        generator,
        *,
        promoted_root: Optional[Path] = None,
    ):
        self.scanner = scanner
        self.generator = generator
        self.candidate_root = Path(generator.output_root)
        self.promoted_root = Path(promoted_root or self.candidate_root.parent.parent / "promoted-cases")

    def scan(self, limit: int = 100) -> List[TicketCandidate]:
        return self.scanner.scan(limit=limit)

    async def generate(self, ticket: TicketCandidate) -> Dict[str, Any]:
        return await self.generator.generate(ticket)

    def _is_stale(self, ticket: TicketCandidate) -> bool:
        """Return True when a previously generated candidate needs regeneration."""
        manifest_path = self.candidate_root / str(ticket.id) / "manifest.json"
        if not manifest_path.is_file():
            return True
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return True
        generated_at = manifest.get("generated_at")
        if ticket.updated_at and generated_at:
            try:
                generated = datetime.fromisoformat(generated_at)
            except ValueError:
                return True
            if generated.tzinfo is None:
                generated = generated.replace(tzinfo=timezone.utc)
            updated = ticket.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            return updated > generated
        return False

    async def run(
        self,
        limit: int = 100,
        ticket_ids: Optional[Sequence[int]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        wanted = {int(item) for item in ticket_ids} if ticket_ids else None
        result = TicketPipelineResult(outputs=[])
        source = getattr(self.scanner, "source", None)
        if wanted is not None and source is not None and hasattr(source, "fetch_by_ids"):
            # 指定工单号时按 id 直查生产库，无视 scan 的项目/状态/类型过滤
            tickets = source.fetch_by_ids(wanted)
        else:
            tickets = self.scan(limit=limit)
        for ticket in tickets:
            result.scanned += 1
            if wanted is not None and ticket.id not in wanted:
                result.skipped += 1
                continue
            if not force and not self._is_stale(ticket):
                result.skipped += 1
                result.outputs.append(
                    {
                        "ticket_id": ticket.id,
                        "status": "skipped",
                        "reason": "candidate already generated",
                    }
                )
                continue
            try:
                output = await self.generate(ticket)
                result.generated += 1
                result.outputs.append(output)
            except Exception as exc:  # noqa: BLE001 - aggregate per-ticket failures
                result.errors += 1
                result.outputs.append({"ticket_id": ticket.id, "status": "error", "error": str(exc)})
        return result.to_dict()

    def promote(self, ticket_id: int, *, require_approved: bool = True) -> Dict[str, Any]:
        candidate_dir = self.candidate_root / str(ticket_id)
        manifest_path = candidate_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Candidate manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        status = manifest.get("review_status")
        if require_approved and status != "approved":
            raise ValueError(
                f"Ticket {ticket_id} is not approved (review_status={status!r}); "
                "review and update manifest.json before promotion"
            )
        promoted_root = self.promoted_root.resolve()
        target_dir = (promoted_root / str(ticket_id)).resolve()
        if target_dir != promoted_root and promoted_root not in target_dir.parents:
            raise ValueError(f"Refusing to promote outside {promoted_root}: {target_dir}")
        if not any(candidate_dir.glob("test_*.py")):
            raise ValueError(
                f"Ticket {ticket_id} has no executable test file; "
                "regenerate the candidate before promotion"
            )
        if target_dir.exists():
            shutil.rmtree(target_dir)
        # 排除 __pycache__：候选目录里可能残留 pytest collect 产生的字节码，
        # 带过去会让 pytest 加载嵌着旧路径的 stale .pyc，traceback 混乱
        shutil.copytree(candidate_dir, target_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        promoted_at = datetime.now(timezone.utc).isoformat()
        manifest.update(
            {
                "review_status": "promoted",
                "promoted_at": promoted_at,
                "promoted_from": str(candidate_dir),
            }
        )
        (target_dir / "promotion.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "status": "promoted",
            "ticket_id": ticket_id,
            "source_dir": str(candidate_dir),
            "target_dir": str(target_dir),
            "promoted_at": promoted_at,
        }