"""Data-driven API runner: Excel cases -> parametrized pytest tests.

Usage:
    from automation.src.runner import load_cases, run_case
"""

from automation.src.runner.cases import load_cases
from automation.src.runner.executor import run_case

__all__ = ["load_cases", "run_case"]
