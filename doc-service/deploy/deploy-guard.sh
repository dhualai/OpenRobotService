#!/usr/bin/env bash
# NEW portal only. Refuse anything that could hit the live legacy stack.
#
# Legacy (DO NOT TOUCH):
#   container topo-portal · web 8766 · SSH 9022 · /opt/topo-portal
#
# Emergency: TOPO_NG_DEPLOY_FORCE=1 (still refuses legacy names/ports)

topo_ng_refuse_legacy() {
  local blob="${*:-} ${TOPO_NG_SSH_PORT:-} ${TOPO_NG_WEB_PORT:-} ${TOPO_CONTAINER:-} ${REMOTE:-}"
  if echo "$blob" | grep -qE '(^|[^0-9])8766([^0-9]|$)|(^|[^0-9])9022([^0-9]|$)'; then
    echo "FATAL: 端口 8766 / 9022 是旧版拓扑站，禁止出现在 topo-ng 部署命令里" >&2
    exit 1
  fi
  if echo "$blob" | grep -qE '(^|[^a-z-])topo-portal([^a-z-]|$)'; then
    echo "FATAL: 容器名 topo-portal 是旧版，禁止作为 topo-ng 目标" >&2
    exit 1
  fi
  if echo "$blob" | grep -q '/opt/topo-portal'; then
    echo "FATAL: /opt/topo-portal 是旧版路径，禁止写入" >&2
    exit 1
  fi
}

topo_ng_require_deploy_ack() {
  if [[ "${TOPO_NG_DEPLOY_FORCE:-}" == "1" ]]; then
    echo "WARN: TOPO_NG_DEPLOY_FORCE=1" >&2
    return 0
  fi
  if [[ "${TOPO_NG_DEPLOY:-}" != "1" ]]; then
    echo "FATAL: 未确认部署到新门户。请显式：TOPO_NG_DEPLOY=1 $*" >&2
    echo "  这不会也不允许更新旧版（8766 / 9022 / topo-portal）。" >&2
    exit 1
  fi
}

topo_ng_refuse_legacy_port() {
  local port="${1:-}"
  local kind="${2:-port}"
  if [[ "$port" == "8766" || "$port" == "9022" ]]; then
    echo "FATAL: ${kind} ${port} 属于旧版服务" >&2
    exit 1
  fi
}
