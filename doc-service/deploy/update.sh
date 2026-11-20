#!/usr/bin/env bash
# Overlay a code tarball onto this install. Never touches Postgres.
#
# Preserves:
#   secrets / .env
#   the database volume (different container; this script has no pg client on purpose)
#   /app/www is replaced with the packaged SPA (static; not user data)
#
# Usage: /app/bin/update.sh /path/to/topo-ng-*.tar.gz
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
TAR="${1:?usage: $0 /path/to/topo-ng-*.tar.gz}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar xzf "$TAR" -C "$TMP" --strip-components=1

if [[ ! -d "$TMP/app" ]]; then
  echo "FATAL: tarball has no app/" >&2
  exit 1
fi

# Code only. Do not copy env, secrets, or anything that looks like a dump.
rsync -a \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='secrets/' \
  --exclude='*.pyc' \
  --exclude='__pycache__/' \
  --exclude='.venv/' \
  "$TMP/app/" "$BASE/app/"

if [[ -f "$TMP/alembic.ini" ]]; then
  install -m 644 "$TMP/alembic.ini" "$BASE/alembic.ini"
fi
if [[ -d "$TMP/alembic" ]]; then
  rsync -a --delete \
    --exclude='*.pyc' \
    --exclude='__pycache__/' \
    "$TMP/alembic/" "$BASE/alembic/"
fi

if [[ -d "$TMP/www" ]]; then
  rsync -a --delete \
    --exclude='.env' \
    --exclude='secrets/' \
    "$TMP/www/" "$BASE/www/"
fi

echo "updated code + www; postgres volume untouched"
