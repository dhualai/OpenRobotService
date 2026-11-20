"""CLI entry point for the OpenRobot local test runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AUTOMATION_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AUTOMATION_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.src.client import OpenRobotTestClient  # noqa: E402


def _parse_variables(values):
    variables = {}
    for item in values or []:
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"variable must be key=value: {item}")
        key, value = item.split("=", 1)
        variables[key] = value
    return variables


def _print(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def build_parser():
    parser = argparse.ArgumentParser(description="OpenRobot test runtime client")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Check local runtime readiness")
    sub.add_parser("scenarios", help="List registered scenarios")

    run = sub.add_parser("run", help="Run a scenario and wait for completion")
    run.add_argument("--scenario", required=True)
    run.add_argument("--profile", default="fast", choices=("fast", "pro", "nightly"))
    run.add_argument("--env", default="local")
    run.add_argument("--timeout", type=float, default=1800.0)
    run.add_argument("--var", action="append", default=[])
    run.add_argument("--extra", action="append", default=[])

    status = sub.add_parser("status", help="Read one run")
    status.add_argument("--run-id", required=True)

    trace = sub.add_parser("trace", help="Read steps for one run")
    trace.add_argument("--run-id", required=True)

    list_runs = sub.add_parser("list", help="List recent runs")
    list_runs.add_argument("--limit", type=int, default=20)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    client = OpenRobotTestClient()

    if args.command == "health":
        _print(client.health())
    elif args.command == "scenarios":
        _print(client.capabilities())
    elif args.command == "run":
        result = client.run(
            scenario=args.scenario,
            profile=args.profile,
            env=args.env,
            variables=_parse_variables(args.var),
            extra_args=args.extra,
            timeout=args.timeout,
        )
        _print(result)
        status = (result.get("status") or {}).get("status")
        return 0 if status == "passed" else 1
    elif args.command == "status":
        _print(client.get_run(args.run_id))
    elif args.command == "trace":
        run = client.get_run(args.run_id)
        _print({"run_id": args.run_id, "steps": run["steps"]})
    elif args.command == "list":
        _print(client.list_runs(limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())