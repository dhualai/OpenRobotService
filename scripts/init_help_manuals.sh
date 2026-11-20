#!/usr/bin/env bash
# ============================================================
# 服务器端初始化：创建多产品日志分析帮助手册目录
# 目标结构:
#   /data/apps/OpenRobotService_Data/help_manuals/
#   ├── USP日志分析指南/   (或按需改成 usp/)
#   └── ORS日志分析指南/
# 用法:
#   sudo bash init_help_manuals.sh     (在服务器 /data/apps 有写权限时无需 sudo)
# ============================================================
set -euo pipefail

DATA_ROOT="${1:-/data/apps/OpenRobotService_Data}"
TMAN="${DATA_ROOT}/help_manuals"

echo "[1/3] 创建帮助手册根目录: ${TMAN}"
mkdir -p "${TMAN}/USP日志分析指南"
mkdir -p "${TMAN}/ORS日志分析指南"

echo "[2/3] 赋予读写权限（如果运行账号非所有者）"
# openrobotservice 为典型运行用户，可按实际调整；此步可跳过
chmod -R u+rwX "${TMAN}" 2>/dev/null || true

echo "[3/3] 生成占位 README"
cat > "${TMAN}/README.md" <<'MD'
# Help Manuals — 日志分析帮助手册

服务器端统一存放各产品的日志分析/排查手册，由日志分析 Discovery 层按产品路由加载。

目录:
- `USP日志分析指南/` — USP 产品
- `ORS日志分析指南/` — ORS 产品

同步: 从本地 `D:\CodeHub\Algorithm\{产品}日志分析指南` 用 scp/rsync 推送到对应子目录。
MD

echo "完成。目录结构:"
find "${TMAN}" -maxdepth 1 | sort
