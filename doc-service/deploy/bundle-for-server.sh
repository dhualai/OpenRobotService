#!/usr/bin/env bash
# Pack deploy/ + release tarball for the server host (no secrets).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy-guard.sh
source "$ROOT/deploy/deploy-guard.sh"
topo_ng_refuse_legacy "$@"

./package-release.sh
mkdir -p deploy/install
cp -f dist/topo-ng-latest.tar.gz deploy/install/
chmod +x deploy/*.sh deploy/entrypoint-api.sh deploy/gen-secrets.py

OUT="dist/topo-ng-deploy-latest.tar.gz"
# Secrets and local tarball history stay off the bundle (install/*.tar.gz is the one payload).
tar czf "$OUT" \
  --exclude='secrets' \
  --exclude='*.pyc' \
  -C deploy .
echo "wrote $OUT"
echo "server: mkdir -p ~/topo-ng-deploy && tar xzf topo-ng-deploy-latest.tar.gz -C ~/topo-ng-deploy && cd ~/topo-ng-deploy && TOPO_NG_DEPLOY=1 TOPO_SSH_PUBLIC_KEY='…' ./run-docker.sh"
