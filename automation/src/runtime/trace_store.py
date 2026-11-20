"""File-backed trace store for OpenRobot test runs."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from automation.config.paths import OUTPUT_DIR

from .context import RunContext, utc_now_iso

TERMINAL_STATUSES = {"passed", "failed", "stopped", "error"}


class TraceStore:
    """Persist run metadata, status, steps, and artifacts under output/runs."""

    def __init__(self, run_root: Optional[Path] = None):
        self.run_root = Path(run_root) if run_root else OUTPUT_DIR / "runs"
        self._lock = threading.RLock()

    def run_dir(self, run_id: str) -> Path:
        return self.run_root / run_id

    def start_run(self, context: RunContext) -> Path:
        """Create or update a run directory for a new execution."""
        with self._lock:
            run_dir = self.run_dir(context.run_id)
            (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            steps_path = run_dir / "steps.jsonl"
            if not steps_path.exists():
                steps_path.touch()

            existing = self._read_json(run_dir / "run.json")
            run_data = {**(existing or {}), **context.to_dict()}
            if existing and existing.get("pid") is not None:
                run_data["pid"] = existing["pid"]
            if existing and existing.get("command"):
                run_data["command"] = existing["command"]
            self._write_json(run_dir / "run.json", run_data)

            status = self._read_json(run_dir / "status.json") or {}
            status.update(
                {
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "status": "running",
                    "updated_at": utc_now_iso(),
                }
            )
            status.setdefault("started_at", context.started_at)
            status.setdefault("exit_code", None)
            status.setdefault("summary", None)
            self._write_json(run_dir / "status.json", status)
            return run_dir

    def update_run(self, run_id: str, **fields: Any) -> Dict[str, Any]:
        """Merge fields into run.json."""
        with self._lock:
            path = self.run_dir(run_id) / "run.json"
            data = self._read_json(path) or {"run_id": run_id}
            data.update(fields)
            data["updated_at"] = utc_now_iso()
            self._write_json(path, data)
            return data

    def update_status(self, run_id: str, status: str, **fields: Any) -> Dict[str, Any]:
        """Merge fields into status.json."""
        with self._lock:
            path = self.run_dir(run_id) / "status.json"
            data = self._read_json(path) or {"run_id": run_id}
            data.update({"status": status, "updated_at": utc_now_iso(), **fields})
            self._write_json(path, data)
            return data

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        exit_code: Optional[int] = None,
        summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark a run as terminal."""
        if status not in TERMINAL_STATUSES:
            status = "error"
        return self.update_status(
            run_id,
            status,
            exit_code=exit_code,
            summary=summary,
            finished_at=utc_now_iso(),
        )

    def record_step(self, run_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        """Append one structured step to steps.jsonl."""
        record = {"ts": utc_now_iso(), **dict(step)}
        with self._lock:
            path = self.run_dir(run_id) / "steps.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record

    def append_artifact(self, run_id: str, source: Path, name: Optional[str] = None) -> Path:
        """Copy an artifact into the run's artifacts directory."""
        source = Path(source)
        target = self.run_dir(run_id) / "artifacts" / (name or source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def read_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(self.run_dir(run_id) / "run.json")

    def read_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(self.run_dir(run_id) / "status.json")

    def read_steps(self, run_id: str) -> List[Dict[str, Any]]:
        path = self.run_dir(run_id) / "steps.jsonl"
        if not path.exists():
            return []
        records: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"raw": line, "parse_error": True})
        return records

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.run_root.exists():
            return []
        runs = []
        for path in sorted(self.run_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_dir():
                continue
            run_id = path.name
            runs.append(
                {
                    "run_id": run_id,
                    "run": self.read_run(run_id),
                    "status": self.read_status(run_id),
                }
            )
            if len(runs) >= limit:
                break
        return runs

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        temp.replace(path)