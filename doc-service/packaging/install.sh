#!/usr/bin/env bash
# Customer-machine install. Generates secrets locally. Does not copy application data.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${TOPO_ENV_FILE:-$ROOT/../secrets/install.env}"
mkdir -p "$(dirname "$ENV_FILE")"
if [[ ! -f "$ENV_FILE" ]]; then
  python3 "$ROOT/gen-secrets.py" --write "$ENV_FILE"
fi
echo "Using $ENV_FILE"
echo "Postgres is not published. API binds 127.0.0.1:8767."
echo "Do not commit or copy this env file into an image."
