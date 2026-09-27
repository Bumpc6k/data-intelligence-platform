# 在 Windows 侧把 dsh Web 壳起起来（M4-03 / Issue #19 的第 1 步用了它）
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File ops\dsh-web.ps1
#
# 为什么要有这个脚本：dsh 是 Node 程序、跑在 Windows；仓库其余部分（PG docker / 内核 / 平台 / skill）
# 都跑在 WSL。demo.sh 在 WSL 里用 `powershell.exe -File ...` 把它拉起来，一条命令起全栈才成立。
#
# 两个要点：
#   1. DSH_HOME 必须是**原生路径**（写 /d/... 会被 node 当成"当前盘下的相对路径"，另造一套目录）；
#   2. API key 从仓库 .env 里读，**不落到任何新文件**（避免把密钥写进日志或临时文件）。

$ErrorActionPreference = 'Stop'

$RepoDir = 'D:\Projects\data-intelligence-platform'
$DshDir  = 'D:\Projects\_dsh-probe\dsh'
$LogPath = 'D:\Projects\_dsh-web-demo.log'

if (Test-Path (Join-Path $DshDir 'package.json')) {
  $env:DSH_HOME = 'D:/Projects/_dsh-probe/dsh-home'
} else {
  Write-Host "dsh not found at $DshDir" -ForegroundColor Yellow
  exit 3
}

$env:PATH = 'C:\Program Files\nodejs;' + $env:PATH

$envFile = Join-Path $RepoDir '.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*DEEPSEEK_API_KEY\s*=\s*(.+?)\s*$') {
      $env:DEEPSEEK_API_KEY = $matches[1].Trim('"').Trim("'")
    }
  }
}
if (-not $env:DEEPSEEK_API_KEY) {
  Write-Host 'DEEPSEEK_API_KEY not found (repo .env)' -ForegroundColor Yellow
  exit 4
}

# 代理一律不走（DeepSeek 与本地 skill 都是直连）
foreach ($name in 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY') {
  Remove-Item "env:$name" -ErrorAction SilentlyContinue
}

Set-Location $DshDir
Write-Host "dsh web starting (DSH_HOME=$env:DSH_HOME, log=$LogPath)"
pnpm dsh web --no-open *> $LogPath
