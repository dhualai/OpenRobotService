#!/usr/bin/env bash
# Pack SPA + API code. No secrets, no Postgres dumps, no tests, no .env.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export PATH="$ROOT/.tools/node-v22.18.0-linux-x64/bin:${PATH}"
if ! command -v node >/dev/null; then
  echo "node not found; put a linux-x64 distro under $ROOT/.tools/" >&2
  exit 1
fi
if ! command -v npm >/dev/null; then
  echo "npm not found" >&2
  exit 1
fi

echo "==> web build"
cd "$ROOT/web"
if [[ ! -d node_modules ]]; then
  npm ci
fi
npm run build
cd "$ROOT"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
NAME="topo-ng"
mkdir -p "$STAGE/$NAME/app" "$STAGE/$NAME/www" "$STAGE/$NAME/bin"

rsync -a --delete \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='.env.*' \
  "$ROOT/server/app/" "$STAGE/$NAME/app/"

rsync -a --delete \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  "$ROOT/server/alembic/" "$STAGE/$NAME/alembic/"
cp "$ROOT/server/alembic.ini" "$STAGE/$NAME/alembic.ini"

rsync -a --delete "$ROOT/web/dist/" "$STAGE/$NAME/www/"
cp "$ROOT/deploy/update.sh" "$STAGE/$NAME/bin/update.sh"
chmod 755 "$STAGE/$NAME/bin/update.sh"

# Refuse packing secrets / dumps
if find "$STAGE" \( -name '.env' -o -name '*.dump' -o -name '*.sql' \) | grep -q .; then
  echo "FATAL: refuse to pack env or sql dumps" >&2
  exit 1
fi

mkdir -p "$ROOT/dist" "$ROOT/deploy/install"
OUT="$ROOT/dist/topo-ng-latest.tar.gz"
tar czf "$OUT" -C "$STAGE" "$NAME"
cp -f "$OUT" "$ROOT/dist/topo-ng-$(date +%Y%m%d-%H%M%S).tar.gz"
cp -f "$OUT" "$ROOT/deploy/install/topo-ng-latest.tar.gz"
echo "wrote $OUT"
echo "also $ROOT/deploy/install/topo-ng-latest.tar.gz"
