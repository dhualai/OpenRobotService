"""High-level run manager for the OpenRobot test runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import RunContext
from .trace_store import TraceStore


class RunManager:
    """Create, query, and finish runtime runs."""

    def __init__(self, store: Optional[TraceStore] = None):
        self.store = store or TraceStore()

    def create_run(
        self,
        scenario: str,
        profile: str = "fast",
        env: str = "local",
        *,
        metadata: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> RunContext:
        context = RunContext.create(
            scenario=scenario,
            profile=profile,
            env=env,
            metadata=metadata,
            run_id=run_id,
            trace_id=trace_id,
        )
        self.store.start_run(context)
        return context

    def get_run(self, run_id: str) -> Dict[str, Any]:
        return {
            "run_id": run_id,
            "run": self.store.read_run(run_id),
            "status": self.store.read_status(run_id),
            "steps": self.store.read_steps(run_id),
        }

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.store.list_runs(limit=limit)

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        exit_code: Optional[int] = None,
        summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.store.finish_run(
            run_id,
            status,
            exit_code=exit_code,
            summary=summary,
        )