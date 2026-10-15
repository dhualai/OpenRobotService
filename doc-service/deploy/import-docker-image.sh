#!/usr/bin/env bash
# Load topo-ng image tarball on the server host.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy-guard.sh
source "$ROOT/deploy-guard.sh"
topo_ng_refuse_legacy "$@"

TAR="${1:?usage: $0 topo-ng-image.tar.gz}"
[[ -f "$TAR" ]] || { echo "missing: $TAR" >&2; exit 1; }

echo "==> docker load"
gzip -dc "$TAR" | docker load
docker images | grep -E 'topo-ng-|postgres' || true
echo "OK — run: TOPO_NG_DEPLOY=1 TOPO_SKIP_BUILD=1 ./run-docker.sh"
echo "Do not import or start anything named topo-portal."
