"""Central path resolution for the automation framework.

All file locations are resolved relative to the automation package root,
so scripts and tests can run from any working directory without relying
on fragile relative paths like Path(__file__).parents[N].
"""
from pathlib import Path

AUTOMATION_ROOT = Path(__file__).resolve().parents[1]

TESTDATA_DIR = AUTOMATION_ROOT / "testdata"
CASES_DIR = TESTDATA_DIR / "cases"
CASES_FILE = CASES_DIR / "api-test-cases.xlsx"
FIXTURES_DIR = TESTDATA_DIR / "fixtures"
TEMPLATES_DIR = TESTDATA_DIR / "templates"
OUTPUT_DIR = AUTOMATION_ROOT / "output"
ALLURE_RESULTS_DIR = OUTPUT_DIR / "allure-results"
ALLURE_REPORT_DIR = OUTPUT_DIR / "allure-report"
