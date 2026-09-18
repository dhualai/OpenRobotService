#!/usr/bin/env python3
"""Merge AI-eval failures into the Bad Case ledger.

Reads failure records (JSON array) and merges them into
`automation/testdata/fixtures/ai/bad_case_log.json`, deduplicated by
case_id + failure_mode: existing entries get count+1 and last_seen
updated, new entries are appended with status=open.

The ledger is only written by this CLI (pytest runs never touch it).

Usage:
    python scripts/cli-update-bad-cases.py --input failures.json --dry-run
    python scripts/cli-update-bad-cases.py --input failures.json
"""

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.config.paths import FIXTURES_DIR  # noqa: E402

LEDGER_PATH = FIXTURES_DIR / "ai" / "bad_case_log.json"

ALLOWED_MODES = {"l1", "l2", "l3", "veto", "unknown"}
ALLOWED_STATUS = {"open", "fixed", "wonfix"}


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"description": "AI 评测 Bad Case 台账（长期沉淀，非单轮报告）", "log": []}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize(record: dict) -> dict:
    """Validate and normalize one input failure record."""
    case_id = str(record.get("case_id", "")).strip()
    if not case_id:
        raise ValueError(f"record missing case_id: {record}")
    mode = str(record.get("failure_mode", "unknown")).strip().lower()
    if mode not in ALLOWED_MODES:
        mode = "unknown"
    risk = str(record.get("risk_level", "P1")).strip().upper()
    if risk not in {"P0", "P1"}:
        risk = "P1"
    today = date.today().isoformat()
    return {
        "case_id": case_id,
        "failure_mode": mode,
        "risk_level": risk,
        "repro_input": str(record.get("repro_input", ""))[:500],
        "first_seen": today,
        "last_seen": today,
        "count": 1,
        "status": "open",
        "note": str(record.get("note", ""))[:200],
    }


def merge(records: list, ledger: dict) -> list:
    """Merge records into the ledger; return a list of change descriptions."""
    changes = []
    log = ledger.setdefault("log", [])
    for record in records:
        item = normalize(record)
        key = (item["case_id"], item["failure_mode"])
        existing = next((e for e in log if (e["case_id"], e["failure_mode"]) == key), None)
        if existing is None:
            log.append(item)
            changes.append(f"+ {key[0]}/{key[1]} (new, {item['risk_level']})")
        else:
            existing["count"] = int(existing.get("count", 1)) + 1
            existing["last_seen"] = item["last_seen"]
            existing["repro_input"] = item["repro_input"]
            existing["risk_level"] = item["risk_level"]
            changes.append(f"~ {key[0]}/{key[1]} (count={existing['count']})")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--input", required=True,
                        help="path to failures JSON array [{case_id, failure_mode, ...}]")
    parser.add_argument("--dry-run", action="store_true", help="print merge plan only")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}")
        return 1

    records = json.loads(input_path.read_text(encoding="utf-8-sig"))
    if not isinstance(records, list):
        print("ERROR: input must be a JSON array")
        return 1

    ledger = load_ledger(LEDGER_PATH)
    changes = merge(records, ledger)

    print(f"ledger: {LEDGER_PATH}")
    print(f"records: {len(records)}, changes: {len(changes)}")
    for c in changes:
        print(f"  {c}")

    if args.dry_run:
        print("dry-run: no file written")
        return 0

    backup = LEDGER_PATH.with_suffix(".json.bak")
    if LEDGER_PATH.exists():
        shutil.copy2(LEDGER_PATH, backup)
        print(f"backup: {backup}")
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"written: {LEDGER_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
