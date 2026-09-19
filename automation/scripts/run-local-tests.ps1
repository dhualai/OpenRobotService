$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$automationRoot = Join-Path $repoRoot 'automation'
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$allureResults = Join-Path $automationRoot 'output\allure-results'

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment not found: $venvPython"
}

$env:PYTHONPATH = $repoRoot
if (-not $env:AUTOMATION_ENV) { $env:AUTOMATION_ENV = 'local' }
if (-not $env:AI_EVAL_NO_RUN) { $env:AI_EVAL_NO_RUN = '1' }

Push-Location $automationRoot
try {
    $pytestArgs = @($args)
    if ($pytestArgs.Count -eq 0) {
        $pytestArgs = @('-q', '--no-trace', '-p', 'no:cacheprovider')
    }

    $hasAllureDir = $pytestArgs | Where-Object { $_ -like '--alluredir*' }
    if (-not $hasAllureDir) {
        $pytestArgs += "--alluredir=$allureResults"
    }

    & $venvPython -m pytest @pytestArgs
} finally {
    Pop-Location
}