"""AI eval run recorder: versioned run metadata + long-table results.

Each pytest run of tests/ai/ produces an independent run record under
output/ai-eval-runs/{run-id}/:

- run.json: run metadata (id / timestamps / counts / env / golden fingerprint)
- results.csv: long table, one row per case x run

The run-id comes from the AI_EVAL_RUN_ID env var (explicit naming for
version comparison, e.g. before-prompt-v2) or an auto timestamp.

Pure Python, no LLM dependencies; unit-testable with tmp dirs.
"""

import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

RESULT_FIELDS = [
    "run_id", "case_id", "suite", "passed", "skipped_all",
    "veto_pending", "n_checks", "n_failed", "failed_metrics", "ts",
]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _auto_run_id() -> str:
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}"


class RunRecorder:
    """Accumulates per-case results during one eval session and dumps them."""

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or os.getenv("AI_EVAL_RUN_ID") or _auto_run_id()
        self.started_at = _now()
        self._results: List[Dict[str, Any]] = []
        self._golden_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------ input

    def record(
        self,
        case_id: str,
        suite: str,
        passed: bool,
        skipped_all: bool = False,
        veto_pending: bool = False,
        checks: Optional[List[dict]] = None,
    ) -> None:
        checks = checks or []
        failed = [c for c in checks if not c["passed"]]
        self._results.append({
            "run_id": self.run_id,
            "case_id": case_id,
            "suite": suite,
            "passed": bool(passed),
            "skipped_all": bool(skipped_all),
            "veto_pending": bool(veto_pending),
            "n_checks": len(checks),
            "n_failed": len(failed),
            "failed_metrics": ";".join(
                f"{c.get('layer', '?')}:{c.get('metric', '?')}" for c in failed
            ),
            "ts": _now(),
        })

    def set_golden_counts(self, counts: Dict[str, int]) -> None:
        """Fingerprint of the loaded golden suites (suite -> case count)."""
        self._golden_counts = dict(counts)

    @property
    def results(self) -> List[Dict[str, Any]]:
        return list(self._results)

    # ------------------------------------------------------------------- dump

    def dump(self, run_root: Path) -> Optional[Path]:
        """Write run.json + results.csv; returns the run dir (None when empty)."""
        if not self._results:
            return None
        run_dir = Path(run_root) / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        counts = {
            "total": len(self._results),
            "passed": sum(1 for r in self._results
                          if not r["skipped_all"] and r["passed"]),
            "failed": sum(1 for r in self._results
                          if not r["skipped_all"] and not r["passed"]),
            "skipped": sum(1 for r in self._results if r["skipped_all"]),
        }
        meta = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": _now(),
            "counts": counts,
            "env": {
                "ai_eval_base_url": os.getenv("AI_EVAL_BASE_URL", ""),
                "judge_model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                "ai_eval_no_run": os.getenv("AI_EVAL_NO_RUN", ""),
            },
            "golden_fingerprint": self._golden_counts,
        }
        (run_dir / "run.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        with open(run_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
            writer.writeheader()
            for row in self._results:
                writer.writerow(row)
        return run_dir


# ----------------------------------------------------------- module singleton

_recorder: Optional[RunRecorder] = None


def get_recorder() -> RunRecorder:
    """Module-level recorder singleton (shared across tests/ai modules)."""
    global _recorder
    if _recorder is None:
        _recorder = RunRecorder()
    return _recorder


def reset_recorder() -> None:
    """Reset the singleton (used by tests / session hooks)."""
    global _recorder
    _recorder = None


def dump_if_needed(run_root: Path) -> Optional[Path]:
    """Dump the recorder unless disabled via AI_EVAL_NO_RUN.

    Returns the run dir, or None when there was nothing to dump.
    """
    if os.getenv("AI_EVAL_NO_RUN"):
        return None
    try:
        return get_recorder().dump(Path(run_root))
    except Exception:  # noqa: BLE001 - reporting must never break the session
        return None
