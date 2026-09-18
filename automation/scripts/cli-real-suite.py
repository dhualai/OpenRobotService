"""Run a Pro scenario through a temporary SSH tunnel."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.src.client import OpenRobotTestClient  # noqa: E402
from automation.src.remote import SSHTunnel  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="Run real-environment scenarios through SSH")
    parser.add_argument("--scenario", default="real_smoke", choices=("real_smoke", "real_lifecycle"))
    parser.add_argument("--env", default="test")
    parser.add_argument("--ssh-prefix", default="REAL")
    parser.add_argument("--timeout", type=float, default=1800.0)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        tunnel = SSHTunnel.from_env(args.ssh_prefix)
    except KeyError as exc:
        print(f"Missing SSH environment variable: {exc}", file=sys.stderr)
        return 2

    with tunnel:
        os.environ["REAL_API_BASE_URL"] = tunnel.local_url()
        os.environ["USE_MOCK"] = "0"
        result = OpenRobotTestClient().run(
            scenario=args.scenario,
            profile="pro",
            env=args.env,
            timeout=args.timeout,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if (result.get("status") or {}).get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())