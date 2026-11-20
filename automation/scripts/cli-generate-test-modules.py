#!/usr/bin/env python3
"""Scan Excel sheets → auto-generate missing pytest test files.

Usage:
    python scripts/cli-generate-test-modules.py              # show what would be created
    python scripts/cli-generate-test-modules.py --write      # actually write files
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import openpyxl

from automation.config.paths import CASES_FILE

EXCEL_PATH = CASES_FILE
TESTS_DIR = Path("tests")
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "test_module.py.txt"

# Modules that should NOT get auto-generated test files (covered otherwise)
SKIP_MODULES = {"call", "tasks", "admin", "auth"}


def _module_to_class(module: str) -> str:
    return module.title().replace("_", "").replace("-", "")


def main():
    dry_run = "--write" not in sys.argv
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    sheet_names = wb.sheetnames
    wb.close()

    found = []
    for name in sheet_names:
        if name in SKIP_MODULES:
            continue
        test_file = TESTS_DIR / name / "test_{name}.py"
        if test_file.exists():
            print(f"  SKIP {name:12} → {test_file} (already exists)")
        else:
            found.append(name)
            print(f"  NEW  {name:12} → {test_file}")

    if not found:
        print("\nAll sheets already have test files. Nothing to generate.")
        return

    if dry_run:
        print(f"\n{len(found)} module(s) without test file. Run with --write to create.")
        return

    for name in found:
        module_dir = TESTS_DIR / name
        module_dir.mkdir(parents=True, exist_ok=True)
        init_file = module_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")

        test_file = module_dir / f"test_{name}.py"
        class_name = _module_to_class(name)
        content = template.format(module=name, class_name=class_name)
        test_file.write_text(content)
        print(f"  Created {test_file}")

    print(f"\n{len(found)} test file(s) created. Run `pytest tests/ -v` to verify.")


if __name__ == "__main__":
    main()
