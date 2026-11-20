"""CLI wrapper for the OpenRobot MCP server."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.mcp_server.server import main  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="Run the OpenRobot MCP server")
    parser.add_argument("--transport", default="stdio", choices=("stdio", "sse"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    main(transport=args.transport, host=args.host, port=args.port)