"""Export only the JUnit case identities and outcomes needed by Test Gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from automation.src.reporting.gate_verdict import REQUIRED_SUITES


def export_junit(source: Path, destination: Path) -> None:
    root = ET.parse(source).getroot()
    if root.tag not in ("testsuites", "testsuite"):
        raise ValueError("Invalid JUnit root")

    exported = ET.Element("testsuite")
    for case in root.iter("testcase"):
        identity = (case.get("file"), case.get("classname"), case.get("name"))
        if not identity[-1]:
            raise ValueError("Missing JUnit case name")
        # pytest parameter IDs can contain arguments. Preserve identity without exporting their text.
        case_id = hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()
        result = ET.SubElement(exported, "testcase", name=case_id)
        for status in ("error", "failure", "skipped"):
            if case.find(status) is not None:
                ET.SubElement(result, status)
                break

    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(exported).write(destination, encoding="utf-8", xml_declaration=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("suites", nargs="+", choices=[*(s.key for s in REQUIRED_SUITES), "scenarios"])
    args = parser.parse_args(argv)
    if args.input_dir.resolve() == args.output_dir.resolve():
        parser.error("Input and output directories must differ")

    exit_code = 0
    for suite in args.suites:
        name = f"junit-{suite}.xml"
        destination = args.output_dir / name
        try:
            destination.unlink(missing_ok=True)
            export_junit(args.input_dir / name, destination)
        except (OSError, ET.ParseError, LookupError, ValueError):
            # Report only the known suite key, never parser details derived from the raw artifact.
            print(f"::warning::Cannot export safe JUnit evidence for {suite}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
