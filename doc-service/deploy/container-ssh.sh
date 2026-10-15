#!/usr/bin/env bash
# Source only. SSH into the NEW portal API container (default 9122, never 9022).
topo_ng_ssh_init() {
  TOPO_NG_SSH_PORT="${TOPO_NG_SSH_PORT:-9122}"
  if [[ "$TOPO_NG_SSH_PORT" == "9022" ]]; then
    echo "FATAL: 9022 是旧版容器 SSH" >&2
    exit 1
  fi
  TOPO_NG_SSH_IDENTITY="${TOPO_SSH_IDENTITY:-}"
  if [[ -z "$TOPO_NG_SSH_IDENTITY" && -f "${HOME}/.ssh/id_rsa" ]]; then
    TOPO_NG_SSH_IDENTITY="${HOME}/.ssh/id_rsa"
  fi
  TOPO_NG_SSH_OPTS=(
    -o StrictHostKeyChecking=no
    -o "UserKnownHostsFile=${TOPO_NG_SSH_KNOWN_HOSTS:-/tmp/topo_ng_known_hosts}"
  )
  if [[ -n "$TOPO_NG_SSH_IDENTITY" ]]; then
    TOPO_NG_SSH_OPTS+=(-i "$TOPO_NG_SSH_IDENTITY" -o BatchMode=yes)
  fi
}
