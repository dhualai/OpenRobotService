#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$ROOT/.tools/node-v22.18.0-linux-x64/bin:$PATH"
if ! command -v node >/dev/null; then
  echo "node not found; put a linux-x64 distro under $ROOT/.tools/" >&2
  exit 1
fi
cd "$ROOT/web"
exec npm run dev
