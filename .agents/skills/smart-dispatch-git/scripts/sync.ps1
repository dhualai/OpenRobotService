#Requires -Version 5.1
<#
.SYNOPSIS
  Merge origin/test into feature/smart-dispatch and/or push that branch.
.PARAMETER Action
  all   = fetch, merge origin/test, push (default)
  sync  = fetch + merge origin/test only
  push  = push feature/smart-dispatch only
#>
param(
    [ValidateSet("all", "sync", "push")]
    [string]$Action = "all"
)

$ErrorActionPreference = "Stop"
$Branch = "feature/smart-dispatch"
$Remote = "origin"
$TestRef = "origin/test"

function Fail([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

function Invoke-Git {
    param([string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        Fail ("git " + ($GitArgs -join " ") + " 失败（exit $LASTEXITCODE）")
    }
}

$root = (& git rev-parse --show-toplevel 2>$null)
if (-not $root) {
    Fail "当前目录不是 git 仓库。"
}
Set-Location $root

$current = (& git branch --show-current).Trim()
$dirty = (& git status --porcelain)
if ($dirty) {
    Write-Host "工作区有未提交改动，先处理后再同步：" -ForegroundColor Yellow
    git status -sb
    Fail "已中止，避免把未提交改动卷进 merge。"
}

if ($Action -eq "push") {
    if ($current -ne $Branch) {
        Fail "当前在 '$current'，推送只允许在 $Branch 上。先切过去：git checkout $Branch"
    }
    Write-Host "推送 $Branch -> $Remote/$Branch（禁止强推）"
    Invoke-Git @("push", $Remote, $Branch)
    git status -sb
    Write-Host "推送完成。PR 请自己提：$Branch -> test（用 merge commit，不要 squash）。" -ForegroundColor Green
    exit 0
}

Write-Host "fetch $Remote ..."
Invoke-Git @("fetch", $Remote)

$testExists = (& git rev-parse --verify $TestRef 2>$null)
if (-not $testExists) {
    Fail "找不到 $TestRef。确认远程有 test 分支。"
}

if ($current -ne $Branch) {
    Write-Host "切换到 $Branch（当前 $current）"
    Invoke-Git @("checkout", $Branch)
}

Write-Host "merge $TestRef into $Branch"
& git merge --no-edit $TestRef
if ($LASTEXITCODE -ne 0) {
    Write-Host "合并冲突。请手动解决后：" -ForegroundColor Yellow
    Write-Host "  git add <文件>"
    Write-Host "  git commit"
    Write-Host "  .\.agents\skills\smart-dispatch-git\scripts\sync.ps1 -Action push"
    git status -sb
    exit 1
}

if ($Action -eq "all") {
    Write-Host "推送 $Branch -> $Remote/$Branch"
    Invoke-Git @("push", $Remote, $Branch)
}

git status -sb
Write-Host "完成。PR 请自己提：$Branch -> test（用 merge commit，不要 squash）。" -ForegroundColor Green
