<#
.SYNOPSIS
  一键触发 AI 深度检视（哨兵 push 方式，fork/同仓 PR 通用）。
.EXAMPLE
  .\ai-review-go.ps1 -Pr 495
#>
param(
  [Parameter(Mandatory = $true)][int]$Pr,
  [string]$Repo = "dhualai/OpenRobotService"
)

$ErrorActionPreference = "Stop"

function GhApi([string]$Method, [string]$Path, $Body) {
  # 有 Body 时走 stdin 喂 JSON（--input -）；
  # 不能写两次 --input（第二个会把 JSON 字符串当文件名）
  if ($Body) {
    $json = $Body | ConvertTo-Json -Depth 5
    return $json | gh api $Path -X $Method --input - 2>$null | ConvertFrom-Json
  }
  return gh api $Path 2>$null | ConvertFrom-Json
}

# 1. 取哨兵分支当前指向；不存在则从 test 的 head 创建
$ref = $null
try { $ref = (GhApi GET "repos/$Repo/git/ref/heads/ai-review-go").object.sha } catch {}
if (-not $ref) {
  $testTree = (GhApi GET "repos/$Repo/git/ref/heads/test").object.tree.sha
  $init = GhApi POST "repos/$Repo/git/commits" @{
    message = "ai-review sentinel init"
    tree    = $testTree
  }
  GhApi POST "repos/$Repo/git/refs" @{
    ref = "refs/heads/ai-review-go"
    sha = $init.sha
  } | Out-Null
  $ref = $init.sha
}

# 2. 在哨兵分支上追加空提交，message 点名 PR（push 事件触发检视）。
# tree 必须取 test 最新——push 事件加载的 workflow 定义来自该 tree，
# 若沿用 parent tree 会把定义永远钉在首次创建时的版本
$testTree = (GhApi GET "repos/$Repo/git/ref/heads/test").object.tree.sha
$commit = GhApi POST "repos/$Repo/git/commits" @{
  message = "ai-review PR#$Pr"
  tree    = $testTree
  parents = @($ref)
}
GhApi PATCH "repos/$Repo/git/refs/heads/ai-review-go" @{ sha = $commit.sha } | Out-Null

Write-Host "哨兵已推送（commit $($commit.sha.Substring(0,8))），深度检视已触发。"
Write-Host "进度与报告见 PR #$Pr 的评论区（约 1~2 分钟）。"
