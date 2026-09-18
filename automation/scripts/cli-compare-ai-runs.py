#!/usr/bin/env python3
"""Compare two AI eval runs (version regression check).

Reads run.json + results.csv from two runs under output/ai-eval-runs/,
classifies per-case status transitions, prints the migration table and
metric-level regressions.

Exit code 1 when --fail-on-regression is set and regressions exist.

Usage:
    python scripts/cli-compare-ai-runs.py --base demo-run-1 --new demo-run-2
    python scripts/cli-compare-ai-runs.py --base path/to/run1 --new path/to/run2
    python scripts/cli-compare-ai-runs.py --latest-two [--fail-on-regression]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.config.paths import OUTPUT_DIR  # noqa: E402

RUNS_ROOT = OUTPUT_DIR / "ai-eval-runs"


def _resolve_run_dir(ref: str) -> Path:
    p = Path(ref)
    if p.is_dir():
        return p
    return RUNS_ROOT / ref


def _load_run(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rows = []
    with open(run_dir / "results.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {"meta": meta, "rows": rows}


def _case_map(run: dict) -> dict:
    return {r["case_id"]: r for r in run["rows"]}


def analyze(base_dir: Path, new_dir: Path) -> dict:
    """Classify per-case transitions between two runs.

    Returns {"transitions": {case_id: kind}, "regressions": [...],
             "fingerprint_mismatch": bool, "base_id", "new_id"}.
    kinds: ok / regression / fixed / new_failure / still_failing /
           skipped (either side skipped_all) / removed (only in base).
    """
    base = _load_run(base_dir)
    new = _load_run(new_dir)
    base_map = _case_map(base)
    new_map = _case_map(new)

    def status(row) -> str:
        return "skipped" if row["skipped_all"] == "True" else (
            "pass" if row["passed"] == "True" else "fail")

    transitions: dict = {}
    regressions: list = []
    for case_id, row in sorted(new_map.items()):
        b = base_map.get(case_id)
        n = status(row)
        if b is None:
            transitions[case_id] = "new_failure" if n == "fail" else (
                "ok" if n == "pass" else "skipped")
            if n == "fail":
                regressions.append({"case_id": case_id, "metric": row["failed_metrics"],
                                    "transition": "new_failure"})
            continue
        b_status = status(b)
        if n == "skipped" or b_status == "skipped":
            transitions[case_id] = "skipped"
            continue
        if b_status == "pass" and n == "fail":
            transitions[case_id] = "regression"
            regressions.append({"case_id": case_id, "metric": row["failed_metrics"],
                                "transition": "regression"})
        elif b_status == "fail" and n == "pass":
            transitions[case_id] = "fixed"
        elif b_status == "fail" and n == "fail":
            transitions[case_id] = "still_failing"
        else:
            transitions[case_id] = "ok"

    for case_id in base_map:
        if case_id not in new_map:
            transitions[case_id] = "removed"

    return {
        "transitions": transitions,
        "regressions": regressions,
        "fingerprint_mismatch": (
            base["meta"].get("golden_fingerprint") != new["meta"].get("golden_fingerprint")
        ),
        "base_id": base["meta"].get("run_id", base_dir.name),
        "new_id": new["meta"].get("run_id", new_dir.name),
        "base_counts": base["meta"].get("counts", {}),
        "new_counts": new["meta"].get("counts", {}),
    }


def _latest_two() -> list:
    if not RUNS_ROOT.is_dir():
        return []
    dirs = sorted(
        (d for d in RUNS_ROOT.iterdir() if (d / "run.json").exists()),
        key=lambda d: (d / "run.json").stat().st_mtime,
    )
    return dirs[-2:]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--base", help="base run id or path")
    parser.add_argument("--new", help="new run id or path")
    parser.add_argument("--latest-two", action="store_true",
                        help="compare the two newest runs under output/ai-eval-runs")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="exit 1 when regressions exist")
    args = parser.parse_args(argv)

    if args.latest_two:
        pair = _latest_two()
        if len(pair) < 2:
            print("ERROR: need at least two runs under output/ai-eval-runs")
            return 2
        base_dir, new_dir = pair
    elif args.base and args.new:
        base_dir = _resolve_run_dir(args.base)
        new_dir = _resolve_run_dir(args.new)
        if not base_dir.is_dir() or not new_dir.is_dir():
            print(f"ERROR: run dir not found: {base_dir} / {new_dir}")
            return 2
    else:
        parser.print_help()
        return 2

    result = analyze(base_dir, new_dir)
    t = result["transitions"]
    kinds = ["regression", "fixed", "new_failure", "still_failing", "skipped", "removed", "ok"]
    print(f"base: {result['base_id']}  (pass={result['base_counts'].get('passed')}, "
          f"fail={result['base_counts'].get('failed')}, skip={result['base_counts'].get('skipped')})")
    print(f"new : {result['new_id']}  (pass={result['new_counts'].get('passed')}, "
          f"fail={result['new_counts'].get('failed')}, skip={result['new_counts'].get('skipped')})")
    if result["fingerprint_mismatch"]:
        print("WARNING: golden fingerprints differ between runs - comparison is reference only")
    print()
    for kind in kinds:
        cases = [c for c, k in t.items() if k == kind]
        if cases:
            print(f"{kind.upper()} ({len(cases)}): {', '.join(cases)}")

    if result["regressions"]:
        print("\nmetric-level regressions:")
        for r in result["regressions"]:
            print(f"  {r['case_id']}: {r['metric'] or '(no failed metric)'} ({r['transition']})")

    if args.fail_on_regression and result["regressions"]:
        print("\n[gate] REGRESSIONS DETECTED (exit 1)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
