"""Run context primitives for the OpenRobot test runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4


def utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def new_trace_id() -> str:
    """Create a short trace identifier."""
    return f"tr-{uuid4().hex[:12]}"


def new_run_id() -> str:
    """Create a sortable run identifier."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{stamp}-{uuid4().hex[:6]}"


@dataclass
class RunContext:
    """Identity and metadata for one test runtime execution."""

    run_id: str
    trace_id: str
    scenario: str
    profile: str = "fast"
    env: str = "local"
    started_at: str = field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        scenario: str,
        profile: str = "fast",
        env: str = "local",
        metadata: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> "RunContext":
        """Create a run context, reusing ids supplied by a parent runtime."""
        return cls(
            run_id=run_id or new_run_id(),
            trace_id=trace_id or new_trace_id(),
            scenario=scenario,
            profile=profile,
            env=env,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "scenario": self.scenario,
            "profile": self.profile,
            "env": self.env,
            "started_at": self.started_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunContext":
        return cls(
            run_id=str(data["run_id"]),
            trace_id=str(data["trace_id"]),
            scenario=str(data.get("scenario", "unknown")),
            profile=str(data.get("profile", "fast")),
            env=str(data.get("env", "local")),
            started_at=str(data.get("started_at", utc_now_iso())),
            metadata=dict(data.get("metadata") or {}),
        )