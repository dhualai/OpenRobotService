# ============================================================
# OpenRobotService - MySQL 8.4 初始化脚本 (Windows)
# 请以【管理员身份】运行 PowerShell 后执行本脚本
# 作用：初始化数据目录 -> 注册并启动服务 -> 设置 root 密码 -> 创建数据库
# ============================================================

$ErrorActionPreference = "Stop"

$MYSQL_HOME = "C:\Program Files\MySQL\MySQL Server 8.4"
$BIN        = "$MYSQL_HOME\bin"
$DATA       = "C:\ProgramData\MySQL\MySQL Server 8.4\Data"
$SVC        = "MySQL84"
$DB_NAME    = "openrobotservice"

# >>> 请把下面这行改成你想要的 root 密码 <<<
$ROOT_PWD   = "mysql_password"

Write-Host "==> 1/6 检查管理员权限..." -ForegroundColor Cyan
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "请右键 PowerShell -> 以管理员身份运行，再执行本脚本！" -ForegroundColor Red; exit 1 }

Write-Host "==> 2/6 初始化数据目录 (若已存在则跳过)..." -ForegroundColor Cyan
if (-not (Test-Path "$DATA\mysql")) {
    New-Item -ItemType Directory -Force -Path $DATA | Out-Null
    & "$BIN\mysqld.exe" --initialize-insecure "--datadir=$DATA"
    Write-Host "    数据目录初始化完成 (root 暂无密码)" -ForegroundColor Green
} else {
    Write-Host "    数据目录已存在，跳过初始化" -ForegroundColor Yellow
}

Write-Host "==> 3/6 注册 Windows 服务 (若已存在则跳过)..." -ForegroundColor Cyan
$existing = Get-Service -Name $SVC -ErrorAction SilentlyContinue
if (-not $existing) {
    & "$BIN\mysqld.exe" --install $SVC "--datadir=$DATA"
    Write-Host "    服务 $SVC 注册完成" -ForegroundColor Green
} else {
    Write-Host "    服务 $SVC 已存在，跳过" -ForegroundColor Yellow
}

Write-Host "==> 4/6 启动服务..." -ForegroundColor Cyan
Start-Service -Name $SVC
Start-Sleep -Seconds 3
Write-Host "    服务状态: $((Get-Service -Name $SVC).Status)" -ForegroundColor Green

Write-Host "==> 5/6 设置 root 密码..." -ForegroundColor Cyan
# 初始化后 root@localhost 无密码，用空密码连接并设置新密码
# 注意：SQL 必须放进双引号变量，否则 'root'@'localhost' 中的 @' 会被当成 here-string
$sqlSetPwd = "ALTER USER 'root'@'localhost' IDENTIFIED BY '$ROOT_PWD'; FLUSH PRIVILEGES;"
& "$BIN\mysql.exe" -u root --execute $sqlSetPwd
Write-Host "    root 密码已设置" -ForegroundColor Green

Write-Host "==> 6/6 创建数据库 $DB_NAME ..." -ForegroundColor Cyan
$sqlCreateDb = "CREATE DATABASE IF NOT EXISTS $DB_NAME CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
& "$BIN\mysql.exe" -u root "-p$ROOT_PWD" --execute $sqlCreateDb
Write-Host "    数据库 $DB_NAME 创建完成" -ForegroundColor Green

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " MySQL 安装配置完成！" -ForegroundColor Green
Write-Host "   主机: 127.0.0.1   端口: 3306" -ForegroundColor Green
Write-Host "   用户: root        密码: (你在脚本里设置的 ROOT_PWD)" -ForegroundColor Green
Write-Host "   数据库: $DB_NAME" -ForegroundColor Green
Write-Host "   服务名: $SVC (开机自启)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
