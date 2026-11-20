#!/usr/bin/env bash
# Pre-deploy checks for the NEW portal only.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PORTAL="$(cd "$HERE/.." && pwd)"
FAIL=0

echo "==> compose must not collide with the live legacy stack"
COMPOSE="$HERE/docker-compose.yml"
for needle in 'container_name: topo-portal' '/opt/topo-portal' 'topo-app-data'; do
  if grep -F -q "$needle" "$COMPOSE"; then
    echo "FORBIDDEN in compose: $needle" >&2
    FAIL=1
  fi
done
if awk '/^[[:space:]]*#/{next} /8766|9022/{found=1} END{exit !found}' "$COMPOSE"; then
  echo "FORBIDDEN: compose maps a legacy host port" >&2
  FAIL=1
fi
if grep -qE '^name:' "$COMPOSE"; then
  echo "FORBIDDEN: top-level name: (breaks docker-compose 1.x)" >&2
  FAIL=1
fi
if ! grep -q 'container_name: topo-ng-api' "$COMPOSE"; then
  echo "MISSING topo-ng-api container_name" >&2
  FAIL=1
fi
if ! grep -q 'topo-ng-db:local' "$COMPOSE"; then
  echo "MISSING topo-ng-db:local (offline image tag)" >&2
  FAIL=1
fi
if grep -qE 'image: postgres:' "$COMPOSE"; then
  echo "FORBIDDEN: compose would pull postgres from Docker Hub" >&2
  FAIL=1
fi
if grep -qE '5432:5432' "$COMPOSE"; then
  echo "FORBIDDEN: postgres published to host" >&2
  FAIL=1
fi
if ! grep -q '8776' "$COMPOSE" || ! grep -q '9122' "$COMPOSE"; then
  echo "MISSING default NEW ports 8776 / 9122" >&2
  FAIL=1
fi

echo "==> update.sh is code-only"
UPD="$HERE/update.sh"
for bad in pg_dump pg_restore psql board.json /opt/topo-portal; do
  if grep -F -q "$bad" "$UPD"; then
    echo "FORBIDDEN in update.sh: $bad" >&2
    FAIL=1
  fi
done
if ! grep -q -- "--exclude='.env'" "$UPD"; then
  echo "MISSING rsync exclude .env" >&2
  FAIL=1
fi
if ! grep -q -- "--exclude='secrets/'" "$UPD"; then
  echo "MISSING rsync exclude secrets/" >&2
  FAIL=1
fi
if ! grep -q 'alembic.ini' "$UPD"; then
  echo "MISSING alembic.ini overlay in update.sh" >&2
  FAIL=1
fi
if ! grep -q 'server/alembic/' "$PORTAL/package-release.sh" || ! grep -q 'alembic.ini' "$PORTAL/package-release.sh"; then
  echo "MISSING alembic in package-release.sh" >&2
  FAIL=1
fi

echo "==> deploy scripts must refuse legacy targets and require TOPO_NG_DEPLOY"
for f in run-docker.sh push-update-container.sh export-docker-image.sh; do
  path="$HERE/$f"
  if ! grep -q 'topo_ng_require_deploy_ack' "$path"; then
    echo "MISSING TOPO_NG_DEPLOY ack in $f" >&2
    FAIL=1
  fi
  if ! grep -q 'topo_ng_refuse_legacy' "$path"; then
    echo "MISSING legacy refuse in $f" >&2
    FAIL=1
  fi
done

echo "==> no accidental copy of secrets / dumps / old portal"
while IFS= read -r -d '' f; do
  base="$(basename "$f")"
  [[ "$base" == "selfcheck.sh" ]] && continue
  if grep -qE 'scp[^;]*secrets/|scp[^;]*\.env[^.]|rsync[^;]*secrets/' "$f"; then
    echo "FORBIDDEN secret copy in $f" >&2
    FAIL=1
  fi
  if grep -qE 'docs/algo-capability-topology/deploy' "$f"; then
    echo "FORBIDDEN reference to old deploy scripts in $f" >&2
    FAIL=1
  fi
  if grep -qE 'pg_restore|pg_dump' "$f"; then
    echo "FORBIDDEN postgres dump/restore in $f" >&2
    FAIL=1
  fi
done < <(find "$HERE" -maxdepth 1 -name '*.sh' -print0)

echo "==> data-push script must refuse"
if ! grep -q 'exit 1' "$HERE/push-data-container.sh"; then
  echo "push-data-container.sh must refuse" >&2
  FAIL=1
fi

if [[ -f "$PORTAL/server/pyproject.toml" ]]; then
  echo "==> requirements-api.txt covers pyproject dependencies"
  while IFS= read -r dep; do
    pkg="${dep}"
    if ! grep -F -q "$pkg" "$HERE/requirements-api.txt"; then
      echo "MISSING $pkg in requirements-api.txt (from pyproject: $dep)" >&2
      FAIL=1
    fi
  done < <(python3 - "$PORTAL/server/pyproject.toml" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text()
inside = False
for line in text.splitlines():
    if line.strip().startswith("dependencies"):
        inside = True
        continue
    if inside:
        if line.strip().startswith("]"):
            break
        s = line.strip().strip(",").strip('"').strip("'")
        if not s:
            continue
        name = s.split("[", 1)[0]
        for sep in (">=", "<=", "==", "~=", ">", "<"):
            name = name.split(sep, 1)[0]
        print(name)
PY
)
fi

if [[ "$FAIL" -ne 0 ]]; then
  echo "deploy selfcheck FAILED" >&2
  exit 1
fi
echo "OK: topo-ng deploy selfcheck"
