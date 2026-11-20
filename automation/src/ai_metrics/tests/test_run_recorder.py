"""Self-tests for the run recorder (tmp dirs, no LLM)."""

import csv
import json

from automation.src.ai_metrics.run_recorder import (
    RunRecorder,
    RESULT_FIELDS,
    get_recorder,
    reset_recorder,
)


def _sample_checks(passed: bool = True):
    return [{"layer": "l1", "metric": "code", "passed": True},
            {"layer": "l3", "metric": "veto", "passed": passed, "detail": "x"}]


class TestRunRecorder:
    def test_dump_creates_both_files(self, tmp_path):
        rec = RunRecorder(run_id="demo-1")
        rec.record("DIAG-001", "diagnosis", passed=True, checks=_sample_checks())
        run_dir = rec.dump(tmp_path)
        assert run_dir is not None
        assert (run_dir / "run.json").exists()
        assert (run_dir / "results.csv").exists()

    def test_run_json_counts(self, tmp_path):
        rec = RunRecorder(run_id="demo-2")
        rec.record("A", "s1", passed=True, checks=_sample_checks())
        rec.record("B", "s1", passed=False, checks=_sample_checks(passed=False))
        rec.record("C", "s2", passed=False, skipped_all=True, checks=[])
        rec.dump(tmp_path)
        meta = json.loads((tmp_path / "demo-2" / "run.json").read_text(encoding="utf-8"))
        assert meta["run_id"] == "demo-2"
        assert meta["counts"] == {"total": 3, "passed": 1, "failed": 1, "skipped": 1}

    def test_results_csv_long_table(self, tmp_path):
        rec = RunRecorder(run_id="demo-3")
        rec.record("DIAG-001", "diagnosis", passed=False, veto_pending=True,
                   checks=_sample_checks(passed=False))
        rec.dump(tmp_path)
        with open(tmp_path / "demo-3" / "results.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        row = rows[0]
        assert row["case_id"] == "DIAG-001"
        assert row["passed"] == "False"
        assert row["veto_pending"] == "True"
        assert row["failed_metrics"] == "l3:veto"
        assert set(row.keys()) == set(RESULT_FIELDS)

    def test_skipped_all_not_failed(self, tmp_path):
        rec = RunRecorder(run_id="demo-4")
        rec.record("RAG-001", "rag", passed=False, skipped_all=True, checks=[])
        rec.dump(tmp_path)
        meta = json.loads((tmp_path / "demo-4" / "run.json").read_text(encoding="utf-8"))
        assert meta["counts"]["skipped"] == 1
        assert meta["counts"]["failed"] == 0

    def test_veto_pending_passthrough(self, tmp_path):
        rec = RunRecorder(run_id="demo-5")
        rec.record("DIAG-001", "diagnosis", passed=False, veto_pending=True,
                   checks=_sample_checks(passed=False))
        rec.dump(tmp_path)
        with open(tmp_path / "demo-5" / "results.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["veto_pending"] == "True"

    def test_golden_fingerprint(self, tmp_path):
        rec = RunRecorder(run_id="demo-6")
        rec.set_golden_counts({"diagnosis": 10, "rag": 8})
        rec.record("A", "diagnosis", passed=True, checks=[])
        rec.dump(tmp_path)
        meta = json.loads((tmp_path / "demo-6" / "run.json").read_text(encoding="utf-8"))
        assert meta["golden_fingerprint"] == {"diagnosis": 10, "rag": 8}

    def test_empty_recorder_returns_none(self, tmp_path):
        rec = RunRecorder(run_id="demo-7")
        assert rec.dump(tmp_path) is None
        assert not (tmp_path / "demo-7").exists()

    def test_auto_run_id_when_not_given(self, tmp_path):
        rec = RunRecorder()
        assert rec.run_id.startswith("run-")


class TestRecorderSingleton:
    def test_get_recorder_reuses_instance(self):
        reset_recorder()
        a = get_recorder()
        b = get_recorder()
        assert a is b
        reset_recorder()

    def test_reset_recorder(self):
        reset_recorder()
        a = get_recorder()
        reset_recorder()
        b = get_recorder()
        assert a is not b
        reset_recorder()
