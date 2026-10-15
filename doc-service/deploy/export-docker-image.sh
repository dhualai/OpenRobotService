#!/usr/bin/env bash
# Build/pull images on a machine that CAN reach Docker Hub, then save a tarball
# for an offline server (no compose, no secrets file required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# shellcheck source=deploy-guard.sh
source "$ROOT/deploy-guard.sh"
topo_ng_refuse_legacy "$@"
topo_ng_require_deploy_ack "$0"

OUT="${TOPO_NG_IMAGE_OUT:-$(cd "$ROOT/.." && pwd)/dist/topo-ng-image.tar.gz}"
mkdir -p "$(dirname "$OUT")"

if ! command -v docker >/dev/null; then
  echo "need docker on this machine" >&2
  exit 1
fi

echo "==> pull bases + postgres"
docker pull python:3.12-slim-bookworm
docker pull nginx:1.27-alpine
docker pull postgres:16-alpine

echo "==> build topo-ng-api / topo-ng-web"
docker build -t topo-ng-api:local -f Dockerfile.api .
docker build -t topo-ng-web:local -f Dockerfile.web .
docker tag postgres:16-alpine topo-ng-db:local

echo "==> docker save → $OUT"
docker save topo-ng-api:local topo-ng-web:local topo-ng-db:local | gzip -c > "$OUT"
BYTES="$(wc -c < "$OUT")"
echo "wrote $OUT ($BYTES bytes)"
echo "scp to server, then: ./import-docker-image.sh ~/topo-ng-image.tar.gz"
echo "then: TOPO_NG_DEPLOY=1 TOPO_SKIP_BUILD=1 ./run-docker.sh"
