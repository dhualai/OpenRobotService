# Dot-source this file:
#   . .\automation\scripts\setup-local-env.ps1
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Warning "Virtual environment not found: $venvPython"
    Write-Host "Create it with: python -m venv $repoRoot\.venv"
}

$env:PYTHONPATH = $repoRoot
if (-not $env:AUTOMATION_ENV) { $env:AUTOMATION_ENV = 'local' }
if (-not $env:USE_MOCK) { $env:USE_MOCK = '1' }
if (-not $env:API_BASE_URL) { $env:API_BASE_URL = 'http://localhost:8400' }
if (-not $env:AI_EVAL_BASE_URL) { $env:AI_EVAL_BASE_URL = 'http://localhost:8401' }
if (-not $env:PLAYWRIGHT_BASE_URL) { $env:PLAYWRIGHT_BASE_URL = 'http://127.0.0.1:5173' }
if (-not $env:PLAYWRIGHT_HEADLESS) { $env:PLAYWRIGHT_HEADLESS = '1' }
if (-not $env:CONTRACT_TEST_ENABLED) { $env:CONTRACT_TEST_ENABLED = '0' }
if (-not $env:AI_EVAL_NO_RUN) { $env:AI_EVAL_NO_RUN = '1' }

Write-Host "OpenRobot venv:   $venvPython"
Write-Host "PYTHONPATH:       $env:PYTHONPATH"
Write-Host "AUTOMATION_ENV:   $env:AUTOMATION_ENV"
Write-Host "USE_MOCK:         $env:USE_MOCK"
Write-Host "API_BASE_URL:     $env:API_BASE_URL"
Write-Host "AI_EVAL_BASE_URL: $env:AI_EVAL_BASE_URL"
Write-Host "PLAYWRIGHT_BASE_URL: $env:PLAYWRIGHT_BASE_URL"