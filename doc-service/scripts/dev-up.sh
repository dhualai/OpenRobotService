#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UV_PID=""
WEB_PID=""
cleanup() {
  kill $UV_PID $WEB_PID 2>/dev/null || true
  wait $UV_PID $WEB_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

docker compose up -d
for _ in $(seq 1 40); do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-topo}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

cd "$ROOT/server"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8767 --reload &
UV_PID=$!
"$ROOT/scripts/dev-web.sh" &
WEB_PID=$!

echo "API  http://127.0.0.1:8767"
echo "Web  http://127.0.0.1:5173"
wait
