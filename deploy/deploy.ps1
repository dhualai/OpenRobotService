<#
.SYNOPSIS
    OpenRobotService 一键部署脚本（前端 + 后端 + 算法）。

.DESCRIPTION
    依据 deplp.md 中的部署说明：
      - 本地构建前端 (npm run build:test / build:prod)，未安装依赖时自动先 npm install
      - 上传 dist 内容到 nginx html 目录
      - 上传 backend/app（忽略 main.py），重启 supervisor 后端服务
      - 上传 ai 代码（忽略 run.py），重启 supervisor 算法服务
    使用 tar 打包 + scp 上传 + ssh 远程执行，兼容 Windows 10+ 自带 OpenSSH 与 bsdtar。

.PARAMETER Environment
    环境类型：test 或 prod。默认 test。

.PARAMETER Components
    要部署的组件：frontend, backend, ai, all。默认 all。

.PARAMETER SshHost
    远程服务器地址（IP 或域名）。未提供则取静态配置，仍为空则交互式询问。

.PARAMETER SshUser
    SSH 用户名。默认取静态配置（root）。

.PARAMETER SshPort
    SSH 端口。默认取静态配置（22）。

.PARAMETER SshIdentity
    可选 SSH 私钥文件路径。密钥登录默认自动使用 ~/.ssh 下的密钥，指定此项则使用该私钥。

.PARAMETER SudoPassword
    远端 sudo 密码（supervisorctl 需要 sudo）。配置后通过 stdin 传给 sudo -S，全程免交互；
    留空则回退为交互式输入。也可在脚本顶部静态配置区设置。

.PARAMETER SkipBuild
    跳过前端构建（使用已存在的 frontend/dist）。

.PARAMETER CleanRemote
    部署前清空远程 backend/app 目录（避免遗留旧 .py 文件）。默认开启，加 -CleanRemote:$false 取消。

.PARAMETER DryRun
    只打印将要执行的命令，不真正执行。

.EXAMPLE
    .\deploy.ps1 -Environment test -Components all -SshHost 10.0.0.1
.EXAMPLE
    .\deploy.ps1 -Environment prod -Components frontend,backend -SshHost prod.example.com -SshUser deploy
.EXAMPLE
    .\deploy.ps1 -SshHost 10.0.0.1 -SkipBuild -Components ai
#>
[CmdletBinding()]
param(
    [ValidateSet('test','prod')]
    [string]$Environment = 'test',

    [string[]]$Components = @('all'),

    [string]$SshHost,
    [string]$SshUser,
    [int]$SshPort = 0,
    [string]$SshIdentity,
    [string]$SudoPassword,
    [switch]$SkipBuild,
    [System.Nullable[bool]]$CleanRemote = $true,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# ====================== 静态配置区（按需修改） ======================
#  - SSH 密钥登录：默认自动使用 ~/.ssh 下的密钥；如需指定私钥，填写 SshIdentity。
#  - SudoPassword：远端 sudo 密码（supervisorctl 需要 sudo 权限）。
#    配置后脚本通过 stdin 传给 sudo -S，全程免交互；
#    留空则回退为 ssh -t 交互式输入密码。
#  - 注意：明文密码有泄露风险，请勿将填写密码后的本脚本提交到仓库。
$ScriptConfig = @{
    SshHost      = ''      # 默认服务器地址，如 '10.0.0.1'；留空则运行时询问
    SshUser      = ''  # 默认 SSH 用户名
    SshPort      = 80      # 默认 SSH 端口
    SshIdentity  = ''      # 私钥路径，如 'C:\Users\me\.ssh\id_rsa'；留空用默认密钥
    SudoPassword = ''      # 远端 sudo 密码；留空则交互输入
}

# 合并优先级：命令行参数 > 静态配置
if (-not $SshHost)      { $SshHost      = $ScriptConfig.SshHost }
if (-not $SshUser)      { $SshUser      = $ScriptConfig.SshUser }
if ($SshPort -le 0)     { $SshPort      = $ScriptConfig.SshPort }
if (-not $SshIdentity)  { $SshIdentity  = $ScriptConfig.SshIdentity }
if (-not $SudoPassword) { $SudoPassword = $ScriptConfig.SudoPassword }

# ---------- 路径与目标 ----------
# 脚本位于 deploy/ 子目录，项目根为其上一级
$RepoRoot   = Split-Path $PSScriptRoot -Parent
$FrontendDir = Join-Path $RepoRoot 'frontend'
$BackendDir  = Join-Path $RepoRoot 'backend'
$AiDir       = Join-Path $RepoRoot 'ai'

# 远端路径 / supervisor 服务名 / conda 环境按环境区分
switch ($Environment) {
    'test' {
        $RemoteBase     = '/data/apps/TestOpenRobotService'
        $NginxHtml      = "$RemoteBase/nginx/html/test"
        $BackendRemote  = "$RemoteBase/backend"
        $AiRemote       = "$RemoteBase/ai"
        $CondaEnv       = 'test-ai'
        $SupBackend     = 'test-open-robot'
        $SupAi          = 'test-open-robot-ai'
        $NpmScript      = 'build:test'
    }
    'prod' {
        $RemoteBase     = '/data/apps/OpenRobotService'
        $NginxHtml      = "$RemoteBase/nginx/html/prod"
        $BackendRemote  = "$RemoteBase/backend"
        $AiRemote       = "$RemoteBase/ai"
        $CondaEnv       = '/data/workspace/ai'
        $SupBackend     = 'openrobot'
        $SupAi          = 'openrobotAI'
        $NpmScript      = 'build:prod'
    }
}

# ---------- 辅助函数 ----------
function Write-Step { param([string]$msg) Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok  { param([string]$msg) Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Err { param([string]$msg) Write-Host "    [ERR] $msg" -ForegroundColor Red }
function Write-Info { param([string]$msg) Write-Host "    ->  $msg" -ForegroundColor DarkGray }

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# 注意：ssh 端口参数为 -p，scp 端口参数为 -P
function Get-SshArgs {
    param([ValidateSet('ssh','scp')][string]$Tool)
    $a = @('-o','StrictHostKeyChecking=accept-new','-o','ConnectTimeout=10')
    if ($Tool -eq 'ssh') { $a += @('-p',"$SshPort") } else { $a += @('-P',"$SshPort") }
    if ($SshIdentity) { $a += @('-i',$SshIdentity) }
    return $a
}

function Get-SshTarget { return "$SshUser@$SshHost" }

function Invoke-RemoteCmd {
    # 通过 ssh 在远程执行命令
    # -Sudo: 命令需要 sudo 权限。若配置了 SudoPassword，则将远程命令改写为
    #        sudo -S 从 stdin 读取密码（免交互，密码不经命令行暴露）；
    #        未配置则回退为 ssh -t 分配 TTY 交互式输入密码。
    param(
        [string]$Command,
        [switch]$Sudo
    )
    $useStdinPwd = $Sudo -and $SudoPassword
    if ($useStdinPwd) {
        # sudo supervisorctl ... -> sudo -S -p '' supervisorctl ...
        # （单引号字符串中 '''' 表示两个 ' 字符，即远端 shell 的空串参数）
        $Command = $Command -replace '(^|\s)sudo\s+', '$1sudo -S -p '''' '
    }
    $sshArgs = @()
    if ($Sudo -and -not $useStdinPwd) { $sshArgs += '-t' }
    $sshArgs += Get-SshArgs -Tool ssh
    $sshArgs += (Get-SshTarget)
    $sshArgs += $Command
    if ($DryRun) {
        Write-Info "[dryrun] ssh $($sshArgs -join ' ')"
        return
    }
    if ($useStdinPwd) {
        $SudoPassword | & ssh @sshArgs
    } else {
        & ssh @sshArgs
    }
    if ($LASTEXITCODE -ne 0) { throw "远程命令执行失败: $Command" }
}

function Send-Tarball {
    # 将本地 tar 包 scp 到远端 /tmp/，返回远端 tar 路径
    param([string]$LocalTar, [string]$RemoteName)
    $remoteTar = "/tmp/$RemoteName"
    $scpArgs = @(Get-SshArgs -Tool scp) + @($LocalTar,"$(Get-SshTarget):$remoteTar")
    if ($DryRun) {
        Write-Info "[dryrun] scp $($scpArgs -join ' ')"
        return $remoteTar
    }
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) { throw "scp 上传失败: $LocalTar" }
    return $remoteTar
}

function New-LocalTar {
    # 生成本地 tar.gz：-C 指定源目录，后续参数为要打包的内容
    param(
        [string]$SourceDir,
        [string[]]$Paths,            # 相对 SourceDir 的内容，如 @('.') 或 @('app','main.py')
        [string[]]$Excludes = @()
    )
    if (-not (Test-Path $SourceDir)) { throw "源目录不存在: $SourceDir" }
    $tarball = Join-Path $env:TEMP "ors_deploy_$(Get-Random).tar.gz"
    $tarArgs = @('-czf',$tarball,'-C',$SourceDir)
    foreach ($ex in $Excludes) { $tarArgs += @('--exclude',$ex) }
    $tarArgs += $Paths
    if ($DryRun) {
        Write-Info "[dryrun] tar $($tarArgs -join ' ')"
        return $tarball
    }
    & tar @tarArgs
    if ($LASTEXITCODE -ne 0) { throw "tar 打包失败: $SourceDir" }
    return $tarball
}

# ---------- 前置检查 ----------
Write-Step '前置检查'

if (-not $SshHost) {
    $SshHost = Read-Host "请输入远程服务器地址 (IP/域名)"
    if (-not $SshHost) { throw '必须提供远程服务器地址' }
}

foreach ($tool in 'tar','scp','ssh','npm') {
    if (-not (Test-Command $tool)) { throw "未找到依赖工具: $tool。请确保其已安装并在 PATH 中。" }
}

# 解析组件列表：支持逗号/分号/空格分隔，如 "backend,ai" / "backend ai" / "all"
$validComponents = 'frontend','backend','ai','all'
$compList = $Components -join ' ' -split '[,;\s]+' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
if (-not $compList) { $compList = @('all') }
foreach ($c in $compList) {
    if ($validComponents -notcontains $c) { throw "无效组件: '$c'（允许: $($validComponents -join ', '))" }
}
if ($compList -contains 'all') { $compList = @('frontend','backend','ai') }
$Components = $compList

if ($Environment -eq 'prod' -and -not $DryRun) {
    $confirm = Read-Host "即将部署到【生产环境】服务器 $SshHost，确认继续？输入 yes 继续"
    if ($confirm -ne 'yes') { Write-Host '已取消。' ; exit 0 }
}

Write-Ok "环境: $Environment | 服务器: ${SshUser}@${SshHost}:${SshPort}"
Write-Ok "组件: $($Components -join ', ')"
Write-Ok "远端基目录: $RemoteBase"

# ---------- 前端 ----------
if ($Components -contains 'frontend') {
    Write-Step '【前端】构建与上传'

    $distDir = Join-Path $FrontendDir 'dist'
    if (-not $SkipBuild) {
        # 未安装依赖时先 npm install
        $nodeModules = Join-Path $FrontendDir 'node_modules'
        if (-not (Test-Path $nodeModules)) {
            Write-Info "未检测到 node_modules，先执行 npm install (在 $FrontendDir)"
            if (-not $DryRun) {
                Push-Location $FrontendDir
                try {
                    & npm install
                    if ($LASTEXITCODE -ne 0) { throw 'npm install 失败' }
                } finally { Pop-Location }
            }
        }
        Write-Info "执行 npm run $NpmScript (在 $FrontendDir)"
        if (-not $DryRun) {
            Push-Location $FrontendDir
            try {
                & npm run $NpmScript
                if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
            } finally { Pop-Location }
        }
    } else {
        Write-Info '已跳过构建，使用现有 dist'
    }

    if (-not (Test-Path $distDir)) { throw "前端 dist 目录不存在: $distDir（请先构建或去掉 -SkipBuild）" }
    Write-Ok "前端产物目录: $distDir"

    $tarball = New-LocalTar -SourceDir $distDir -Paths @('.')
    $remoteTar = Send-Tarball -LocalTar $tarball -RemoteName 'frontend_dist.tar.gz'

    $extractCmd = "mkdir -p `"$NginxHtml`" && rm -rf `"$NginxHtml`"/* && tar -xzf `"$remoteTar`" -C `"$NginxHtml`" && rm -f `"$remoteTar`" && echo FRONTEND_DONE"
    Write-Info "远端解压到 $NginxHtml"
    Invoke-RemoteCmd -Command $extractCmd
    Write-Ok '前端部署完成'

    if (Test-Path $tarball) { Remove-Item $tarball -Force -ErrorAction SilentlyContinue }
}

# ---------- 后端 ----------
if ($Components -contains 'backend') {
    Write-Step '【后端】上传 app 并重启（忽略 main.py，保留远端版本）'

    $excludes = @('__pycache__','*.pyc','*.pyo','.pytest_cache','.mypy_cache','.ruff_cache')
    $tarball = New-LocalTar -SourceDir $BackendDir -Paths @('app') -Excludes $excludes
    $remoteTar = Send-Tarball -LocalTar $tarball -RemoteName 'backend_app.tar.gz'

    $cleanCmd = ''
    if ($CleanRemote) {
        $cleanCmd = "rm -rf `"$BackendRemote/app`" && "
    }
    $extractCmd = "mkdir -p `"$BackendRemote`" && ${cleanCmd}tar -xzf `"$remoteTar`" -C `"$BackendRemote`" && rm -f `"$remoteTar`" && echo BACKEND_UPLOAD_DONE"
    Write-Info "远端解压到 $BackendRemote"
    Invoke-RemoteCmd -Command $extractCmd
    Write-Ok '后端代码上传完成'

    Write-Info "重启 supervisor 服务: $SupBackend"
    Invoke-RemoteCmd -Sudo -Command "sudo supervisorctl restart $SupBackend && echo BACKEND_RESTART_DONE"
    Write-Ok '后端部署完成'

    if (Test-Path $tarball) { Remove-Item $tarball -Force -ErrorAction SilentlyContinue }
}

# ---------- 算法 ----------
if ($Components -contains 'ai') {
    Write-Step '【算法】上传 ai 代码并重启'

    # 排除虚拟环境、向量库、模型、缓存、测试、本地数据等；run.py 保留远端版本
    $excludes = @(
        '__pycache__','*.pyc','*.pyo',
        '.venv','venv','env',
        '.pytest_cache','.mypy_cache','.ruff_cache',
        'kb','embed_models','docs','tests','tools','uploads',
        '.env','.env.*','*.log','logs','*.sqlite3','*.db',
        '.git','.idea','.vscode',
        'run.py'
    )
    $tarball = New-LocalTar -SourceDir $AiDir -Paths @('.') -Excludes $excludes
    $remoteTar = Send-Tarball -LocalTar $tarball -RemoteName 'ai_code.tar.gz'

    $extractCmd = "mkdir -p `"$AiRemote`" && tar -xzf `"$remoteTar`" -C `"$AiRemote`" && rm -f `"$remoteTar`" && echo AI_UPLOAD_DONE"
    Write-Info "远端解压到 $AiRemote"
    Invoke-RemoteCmd -Command $extractCmd
    Write-Ok '算法代码上传完成'

    Write-Info "重启 supervisor 服务: $SupAi"
    Invoke-RemoteCmd -Sudo -Command "sudo supervisorctl restart $SupAi && echo AI_RESTART_DONE"
    Write-Ok '算法部署完成'

    if (Test-Path $tarball) { Remove-Item $tarball -Force -ErrorAction SilentlyContinue }
}

Write-Step '全部完成'
Write-Ok "$Environment 环境部署结束: $($Components -join ', ')"
