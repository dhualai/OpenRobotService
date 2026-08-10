# 本地原生 MinIO 一键启动脚本（后备方案）
#
# 与 docker-compose.minio.yml 共用同一份数据 D:\minio-data，
# 二者二选一，不可同时运行（端口 9000/9001 冲突）。
#
# 使用：在 PowerShell 中执行
#   .\scripts\start-minio.ps1
#
# 启动后：
#   S3 API  : http://localhost:9000
#   控制台  : http://localhost:9001  (账号 wxing / 15911Lm!)
#
# 注意：MINIO_ROOT_USER/MINIO_ROOT_PASSWORD 需与后端 .env 的
# MINIO_ACCESS_KEY/MINIO_SECRET_KEY 一致（wxing / 15911Lm!）。
# 若后端 .env 设 MINIO_SECURE=True，则控制台需 https 且浏览器访问会报警（自签证书）。

$minioExe = "D:\minio.exe"
$dataDir = "D:\minio-data"

if (-not (Test-Path $minioExe)) {
    Write-Error "未找到 $minioExe，请确认 minio 安装路径"
    exit 1
}

if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
    Write-Host "已创建数据目录 $dataDir"
}

# 必须与后端 .env 的 MINIO_ACCESS_KEY/MINIO_SECRET_KEY 一致（本地为 MinIO 默认账号）
$env:MINIO_ROOT_USER = "minioadmin"
$env:MINIO_ROOT_PASSWORD = "minioadmin"

# 后台启动，控制台监听 9001
Start-Process -FilePath $minioExe -ArgumentList "server", "$dataDir", "--console-address", ":9001" -NoNewWindow

Write-Host "MinIO 启动中... 稍候访问 http://localhost:9000 (S3) / http://localhost:9001 (控制台，账号 minioadmin/minioadmin)"
