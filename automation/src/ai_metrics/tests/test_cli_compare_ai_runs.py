"""Self-tests for the run-compare CLI logic (tmp run dirs, no LLM)."""

import importlib.util
import sys
from pathlib import Path

import pytest

from automation.src.ai_metrics.run_recorder import RunRecorder

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "cli-compare-ai-runs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("cli_compare", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cli_compare"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def compare_mod():
    return _load_module()


def _make_run(root: Path, run_id: str, rows: list):
    rec = RunRecorder(run_id=run_id)
    for row in rows:
        rec.record(case_id=row[0], suite=row[1], passed=row[2],
                   skipped_all=row[3], checks=row[4])
    rec.dump(root)
    return root / run_id


class TestCompareRuns:
    def test_transition_classification(self, tmp_path, compare_mod):
        base = _make_run(tmp_path, "base", [
            ("A", "s", True, False, []),       # pass
            ("B", "s", True, False, []),       # pass -> regression
            ("C", "s", False, False, [{"layer": "l1", "metric": "code", "passed": False}]),  # fail -> fixed
            ("D", "s", False, False, [{"layer": "l1", "metric": "code", "passed": False}]),  # fail -> fail
            ("E", "s", False, True, []),       # skipped
        ])
        new = _make_run(tmp_path, "new", [
            ("A", "s", True, False, []),
            ("B", "s", False, False, [{"layer": "l3", "metric": "veto", "passed": False}]),
            ("C", "s", True, False, []),
            ("D", "s", False, False, [{"layer": "l1", "metric": "code", "passed": False}]),
            ("E", "s", False, True, []),
            ("F", "s", False, False, [{"layer": "l2", "metric": "recall", "passed": False}]),  # new failure
        ])
        result = compare_mod.analyze(base, new)
        t = result["transitions"]
        assert t["A"] == "ok"
        assert t["B"] == "regression"
        assert t["C"] == "fixed"
        assert t["D"] == "still_failing"
        assert t["E"] == "skipped"
        assert t["F"] == "new_failure"
        assert result["base_id"] == "base"
        assert result["new_id"] == "new"

    def test_regressions_detail(self, tmp_path, compare_mod):
        base = _make_run(tmp_path, "base", [("B", "s", True, False, [])])
        new = _make_run(tmp_path, "new", [
            ("B", "s", False, False, [{"layer": "l3", "metric": "veto", "passed": False}])])
        result = compare_mod.analyze(base, new)
        assert result["regressions"] == [
            {"case_id": "B", "metric": "l3:veto", "transition": "regression"}]

    def test_fingerprint_mismatch_warning(self, tmp_path, compare_mod):
        base = _make_run(tmp_path, "base", [("A", "s", True, False, [])])
        base / "run.json"  # fingerprint set explicitly:
        import json
        meta = json.loads((base / "run.json").read_text(encoding="utf-8"))
        meta["golden_fingerprint"] = {"diagnosis": 10}
        (base / "run.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        new = _make_run(tmp_path, "new", [("A", "s", True, False, [])])
        meta2 = json.loads((new / "run.json").read_text(encoding="utf-8"))
        meta2["golden_fingerprint"] = {"diagnosis": 11}
        (new / "run.json").write_text(json.dumps(meta2, ensure_ascii=False), encoding="utf-8")

        result = compare_mod.analyze(base, new)
        assert result["fingerprint_mismatch"] is True

    def test_resolve_and_fail_gate(self, tmp_path, compare_mod):
        base = _make_run(tmp_path, "base", [("B", "s", True, False, [])])
        new = _make_run(tmp_path, "new", [
            ("B", "s", False, False, [{"layer": "l3", "metric": "veto", "passed": False}])])
        result = compare_mod.analyze(base, new)
        assert result["regressions"]
        code = compare_mod.main([
            "--base", str(base), "--new", str(new), "--fail-on-regression",
        ])
        assert code == 1
