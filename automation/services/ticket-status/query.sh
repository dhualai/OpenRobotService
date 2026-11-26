#!/usr/bin/env bash
# 查一个工单的生产状态。CI 和本地都能用。
#
#   TICKET_STATUS_API_URL=https://xxx TICKET_STATUS_API_TOKEN=yyy ./query.sh 1053
#
# 退出码：0 = 查到了（不管 eligible 真假）；非 0 = 查询失败，调用方必须当成「无法判断」。
set -euo pipefail

: "${TICKET_STATUS_API_URL:?需要 TICKET_STATUS_API_URL}"
: "${TICKET_STATUS_API_TOKEN:?需要 TICKET_STATUS_API_TOKEN}"
id="${1:?用法: query.sh <工单号，如 1053 或 ORS-1053>}"

curl -fsS --max-time 20 \
  -H "Authorization: Bearer ${TICKET_STATUS_API_TOKEN}" \
  "${TICKET_STATUS_API_URL%/}/tickets/${id}"
printf '\n'
