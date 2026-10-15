#!/usr/bin/env bash
# PID 1 in topo-ng-api: sshd + uvicorn. Never exec uvicorn as PID 1 (code update pkill must not kill SSH).
set -euo pipefail

umask 022
mkdir -p /app/app /app/www /app/bin /var/run/sshd /home/topo/.ssh
chmod 700 /home/topo/.ssh

if [[ ! -f /etc/ssh/ssh_host_ed25519_key && ! -f /etc/ssh/ssh_host_rsa_key ]]; then
  ssh-keygen -A
fi

if [[ -n "${TOPO_SSH_PASSWORD:-}" ]]; then
  echo "topo:${TOPO_SSH_PASSWORD}" | chpasswd
  passwd -u topo 2>/dev/null || true
fi

if [[ -n "${TOPO_SSH_PUBLIC_KEY:-}" ]]; then
  printf '%s\n' "$TOPO_SSH_PUBLIC_KEY" > /home/topo/.ssh/authorized_keys
  chmod 600 /home/topo/.ssh/authorized_keys
  chown -R topo:topo /home/topo/.ssh
fi

seed_from_tarball() {
  local tar=""
  shopt -s nullglob
  local cands=(/opt/install/topo-ng-latest.tar.gz /opt/install/topo-ng-*.tar.gz)
  shopt -u nullglob
  for t in "${cands[@]}"; do
    if [[ -f "$t" ]]; then
      tar="$t"
      break
    fi
  done
  [[ -n "$tar" ]] || return 1
  local tmp
  tmp="$(mktemp -d)"
  tar xzf "$tar" -C "$tmp" --strip-components=1
  if [[ -d "$tmp/app" ]]; then
    cp -a "$tmp/app/." /app/app/
  fi
  if [[ -d "$tmp/www" ]]; then
    cp -a "$tmp/www/." /app/www/
  fi
  if [[ -d "$tmp/bin" ]]; then
    cp -a "$tmp/bin/." /app/bin/
  fi
  rm -rf "$tmp"
}

if [[ ! -f /app/app/main.py ]]; then
  seed_from_tarball || true
fi
if [[ ! -f /app/bin/update.sh && -f /opt/payload/bin/update.sh ]]; then
  mkdir -p /app/bin
  cp -a /opt/payload/bin/update.sh /app/bin/update.sh
fi
chown -R topo:topo /app/app /app/www /app/bin 2>/dev/null || true
chmod 755 /app/bin/update.sh 2>/dev/null || true

/usr/sbin/sshd

export PYTHONPATH=/app
while true; do
  su -s /bin/bash topo -c 'cd /app && PYTHONPATH=/app /usr/local/bin/uvicorn app.main:app --host 0.0.0.0 --port 8767' || true
  echo "uvicorn exited; restart in 2s" >&2
  sleep 2
done
