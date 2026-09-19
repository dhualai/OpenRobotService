[CmdletBinding()]
param(
    [string]$ConfigPath = "",
    [string]$SshHost = "",
    [string]$SshUser = "",
    [int]$SshPort = 0,
    [string]$SshKey = "",
    [string]$U1Username = "",
    [string]$U1Password = "",
    [string]$U2Username = "",
    [string]$U2Password = "",
    [string]$CleanupUsername = "",
    [string]$CleanupPassword = "",
    [switch]$EnableDbCleanup,
    [string]$DbCleanupUser = "",
    [string]$DbCleanupPassword = "",
    [string]$DbCleanupDatabase = "",
    [switch]$SkipFrontendBuild,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$automationRoot = Join-Path $repoRoot "automation"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $automationRoot "config\ui_regression.local.yaml"
}
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path

function Read-Secret {
    param([string]$Prompt)
    $secure = Read-Host $Prompt -AsSecureString
    return [System.Net.NetworkCredential]::new("", $secure).Password
}

function Resolve-Required {
    param(
        [string]$Value,
        [string]$EnvironmentName,
        [string]$ConfigValue,
        [string]$Prompt,
        [switch]$Secret
    )

    if (-not $Value) {
        $Value = [Environment]::GetEnvironmentVariable($EnvironmentName)
    }
    if (-not $Value) {
        $Value = $ConfigValue
    }
    if (-not $Value) {
        if ($Secret) {
            $Value = Read-Secret $Prompt
        } else {
            $Value = Read-Host $Prompt
        }
    }
    if (-not $Value) {
        throw "Missing required value: $EnvironmentName"
    }
    return $Value
}

function Test-PortAvailable {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect("127.0.0.1", $Port)
        return $false
    } catch {
        # No listener accepted the connection; continue with bind check.
    } finally {
        $client.Dispose()
    }

    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $Port
    )
    try {
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        $listener.Stop()
    }
}

function Get-FreePort {
    param([int]$PreferredPort)
    for ($port = $PreferredPort; $port -lt ($PreferredPort + 100); $port++) {
        if (Test-PortAvailable -Port $port) {
            return $port
        }
    }
    throw "No free report port found starting at $PreferredPort"
}

function Get-ManagedReportServer {
    param([string]$StatePath)
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return $null
    }
    try {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $process = Get-Process -Id ([int]$state.pid) -ErrorAction Stop
        $storedTicks = [long]$state.start_ticks
        $tickTolerance = [TimeSpan]::FromSeconds(2).Ticks
        if ([Math]::Abs($process.StartTime.Ticks - $storedTicks) -gt $tickTolerance) {
            return $null
        }
        return [pscustomobject]@{
            pid = [int]$state.pid
            port = [int]$state.port
            report_dir = [string]$state.report_dir
            url = [string]$state.url
        }
    } catch {
        return $null
    }
}

function Stop-ManagedReportServer {
    param($Server)
    if ($null -eq $Server) {
        return
    }
    Stop-Process -Id $Server.pid -ErrorAction SilentlyContinue
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (Test-PortAvailable -Port $Server.port) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment not found: $venvPython"
}
if (-not (Get-Command allure -ErrorAction SilentlyContinue)) {
    throw "Allure CLI is not available in PATH"
}

$configParser = @'
import json
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text(encoding="utf-8"))
print(json.dumps(data, ensure_ascii=False))
'@
$configJson = & $venvPython -c $configParser $ConfigPath
if ($LASTEXITCODE -ne 0) {
    throw "Failed to parse UI regression config: $ConfigPath"
}
$config = $configJson | ConvertFrom-Json

$resolvedSshHost = Resolve-Required `
    -Value $SshHost `
    -EnvironmentName "UI_REGRESSION_SSH_HOST" `
    -ConfigValue $config.ssh.host `
    -Prompt "SSH host"
$resolvedSshUser = Resolve-Required `
    -Value $SshUser `
    -EnvironmentName "UI_REGRESSION_SSH_USER" `
    -ConfigValue $config.ssh.user `
    -Prompt "SSH user"
$resolvedSshPort = if ($SshPort -gt 0) {
    $SshPort
} elseif ($env:UI_REGRESSION_SSH_PORT) {
    [int]$env:UI_REGRESSION_SSH_PORT
} else {
    [int]$config.ssh.port
}
$resolvedSshKey = if ($SshKey) {
    $SshKey
} elseif ($env:UI_REGRESSION_SSH_KEY) {
    $env:UI_REGRESSION_SSH_KEY
} else {
    [string]$config.ssh.key
}

$resolvedU1Username = Resolve-Required `
    -Value $U1Username `
    -EnvironmentName "UI_REGRESSION_U1_USERNAME" `
    -ConfigValue $config.tests.u1_username `
    -Prompt "U1 username"
$resolvedU1Password = Resolve-Required `
    -Value $U1Password `
    -EnvironmentName "UI_REGRESSION_U1_PASSWORD" `
    -Prompt "U1 password" `
    -Secret
$resolvedU2Username = Resolve-Required `
    -Value $U2Username `
    -EnvironmentName "UI_REGRESSION_U2_USERNAME" `
    -ConfigValue $config.tests.u2_username `
    -Prompt "U2 username"
$resolvedU2Password = Resolve-Required `
    -Value $U2Password `
    -EnvironmentName "UI_REGRESSION_U2_PASSWORD" `
    -Prompt "U2 password" `
    -Secret
$resolvedCleanupUsername = Resolve-Required `
    -Value $CleanupUsername `
    -EnvironmentName "UI_REGRESSION_CLEANUP_USERNAME" `
    -ConfigValue $config.tests.cleanup_username `
    -Prompt "Cleanup username"
$resolvedCleanupPassword = Resolve-Required `
    -Value $CleanupPassword `
    -EnvironmentName "UI_REGRESSION_CLEANUP_PASSWORD" `
    -Prompt "Cleanup password" `
    -Secret

$frontendDist = Join-Path $repoRoot $config.frontend.dist
$resultsDir = Join-Path $repoRoot $config.report.results_dir
$reportDir = Join-Path $repoRoot $config.report.report_dir
$preferredReportPort = [int]$config.report.port
$reportStatePath = Join-Path $automationRoot "output\ui-regression-report-server.json"
$dbCleanupEnabled = (
    $EnableDbCleanup -or
    [bool]$config.database_cleanup.enabled
)

if ($dbCleanupEnabled) {
    $resolvedDbCleanupUser = if ($DbCleanupUser) {
        $DbCleanupUser
    } elseif ($env:UI_REGRESSION_DB_USER) {
        $env:UI_REGRESSION_DB_USER
    } else {
        [string]$config.database_cleanup.user
    }
    $resolvedDbCleanupDatabase = if ($DbCleanupDatabase) {
        $DbCleanupDatabase
    } elseif ($env:UI_REGRESSION_DB_NAME) {
        $env:UI_REGRESSION_DB_NAME
    } else {
        [string]$config.database_cleanup.database
    }
    $resolvedDbCleanupPassword = Resolve-Required `
        -Value $DbCleanupPassword `
        -EnvironmentName "UI_REGRESSION_DB_PASSWORD" `
        -Prompt "Database cleanup password" `
        -Secret
}

if (-not $SkipFrontendBuild) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm is required to build the frontend"
    }
    $frontendRoot = Join-Path $repoRoot "frontend"
    Push-Location $frontendRoot
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules"))) {
            npm ci
            if ($LASTEXITCODE -ne 0) {
                throw "npm ci failed"
            }
        }
        npx vite build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build failed"
        }
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $frontendDist "index.html"))) {
    throw "Frontend dist is missing: $frontendDist"
}

if (Test-Path -LiteralPath $resultsDir) {
    Remove-Item -LiteralPath $resultsDir -Recurse -Force
}
New-Item -ItemType Directory -Path $resultsDir -Force | Out-Null

$env:PYTHONPATH = $repoRoot
$env:UI_REGRESSION_E2E = "1"
$env:UI_REGRESSION_SSH_HOST = $resolvedSshHost
$env:UI_REGRESSION_SSH_USER = $resolvedSshUser
$env:UI_REGRESSION_SSH_PORT = "$resolvedSshPort"
$env:UI_REGRESSION_SSH_KEY = $resolvedSshKey
$env:UI_REGRESSION_BACKEND_REMOTE_PORT = "$($config.ssh.backend_remote_port)"
$env:UI_REGRESSION_BACKEND_LOCAL_PORT = "$($config.ssh.backend_local_port)"
$env:UI_REGRESSION_AI_REMOTE_PORT = "$($config.ssh.ai_remote_port)"
$env:UI_REGRESSION_AI_LOCAL_PORT = "$($config.ssh.ai_local_port)"
$env:UI_REGRESSION_TUNNEL_TIMEOUT = "$($config.ssh.tunnel_timeout)"
$env:UI_REGRESSION_U1_USERNAME = $resolvedU1Username
$env:UI_REGRESSION_U1_PASSWORD = $resolvedU1Password
$env:UI_REGRESSION_U2_USERNAME = $resolvedU2Username
$env:UI_REGRESSION_U2_PASSWORD = $resolvedU2Password
$env:UI_REGRESSION_CLEANUP_USERNAME = $resolvedCleanupUsername
$env:UI_REGRESSION_CLEANUP_PASSWORD = $resolvedCleanupPassword
$env:UI_REGRESSION_DB_CLEANUP_ENABLED = if ($dbCleanupEnabled) { "1" } else { "0" }
if ($dbCleanupEnabled) {
    $env:UI_REGRESSION_DB_HOST = [string]$config.database_cleanup.host
    $env:UI_REGRESSION_DB_PORT = "$($config.database_cleanup.local_port)"
    $env:UI_REGRESSION_DB_REMOTE_PORT = "$($config.database_cleanup.remote_port)"
    $env:UI_REGRESSION_DB_LOCAL_PORT = "$($config.database_cleanup.local_port)"
    $env:UI_REGRESSION_DB_USER = $resolvedDbCleanupUser
    $env:UI_REGRESSION_DB_PASSWORD = $resolvedDbCleanupPassword
    $env:UI_REGRESSION_DB_NAME = $resolvedDbCleanupDatabase
    $env:UI_REGRESSION_CLEANUP_TITLE_PREFIX = [string]$config.database_cleanup.title_prefix
    $env:UI_REGRESSION_CLEANUP_PROJECT_ID = [string]$config.database_cleanup.project_id
    Write-Host "DB cleanup prefix: $env:UI_REGRESSION_CLEANUP_TITLE_PREFIX"
}
$env:UI_REGRESSION_HEADLESS = if ($config.tests.headless) { "1" } else { "0" }
$env:ALLURE_AUTO_OPEN = "0"

$pytestArgs = @(
    "automation/tests/ui/test_call_qa_to_ticket_close_regression.py",
    "automation/tests/ui/test_real_safe_api_smoke.py",
    "-m", "e2e or smoke",
    "-q",
    "--alluredir=$resultsDir"
)

Push-Location $repoRoot
try {
    & $venvPython -m pytest @pytestArgs
    $testExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

$env:JAVA_TOOL_OPTIONS = "-Dfile.encoding=UTF-8"
allure generate $resultsDir -o $reportDir --clean
if ($LASTEXITCODE -ne 0) {
    throw "Allure report generation failed"
}

if ($testExitCode -ne 0) {
    Write-Host ""
    Write-Host "Tests failed. Report: $reportDir" -ForegroundColor Yellow
    exit $testExitCode
}

$managedServer = Get-ManagedReportServer -StatePath $reportStatePath
$reportUrl = "http://$($config.report.host):$preferredReportPort/"
$reuseManagedServer = (
    $null -ne $managedServer -and
    $managedServer.port -eq $preferredReportPort -and
    $managedServer.report_dir -eq $reportDir
)

if ($reuseManagedServer) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing $managedServer.url -TimeoutSec 2
        $reuseManagedServer = $response.StatusCode -eq 200
    } catch {
        $reuseManagedServer = $false
    }
}

if ($reuseManagedServer) {
    $reportUrl = $managedServer.url
    $reportServerPid = $managedServer.pid
} else {
    if ($null -ne $managedServer) {
        Stop-ManagedReportServer -Server $managedServer
    }
    Remove-Item -LiteralPath $reportStatePath -Force -ErrorAction SilentlyContinue

    if (Test-PortAvailable -Port $preferredReportPort) {
        $reportPort = $preferredReportPort
    } else {
        $reportPort = Get-FreePort -PreferredPort $preferredReportPort
        Write-Warning (
            "Preferred report port $preferredReportPort is occupied by another " +
            "process; using $reportPort"
        )
    }

    $server = Start-Process `
        -FilePath $venvPython `
        -ArgumentList @("-m", "http.server", "$reportPort", "--directory", $reportDir) `
        -WindowStyle Hidden `
        -PassThru
    $reportUrl = "http://$($config.report.host):$reportPort/"
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 250
        try {
            $response = Invoke-WebRequest -UseBasicParsing $reportUrl -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            # Wait for the report server to start.
        }
    }
    if (-not $ready) {
        throw "Report server did not start: $reportUrl"
    }
    $reportServerPid = $server.Id
    @{
        pid = $reportServerPid
        port = $reportPort
        report_dir = $reportDir
        url = $reportUrl
        start_ticks = $server.StartTime.Ticks
    } | ConvertTo-Json | Set-Content -LiteralPath $reportStatePath -Encoding UTF8
}

Write-Host ""
Write-Host "UI regression passed." -ForegroundColor Green
Write-Host "Allure report: $reportUrl" -ForegroundColor Cyan
Write-Host "Report server PID: $reportServerPid"

if (-not $NoOpen) {
    Start-Process $reportUrl
}
