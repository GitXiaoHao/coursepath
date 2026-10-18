<#
.SYNOPSIS
    一键拉起 Dify + 自研 campus-kb 服务。不修改 Dify 源码，只在其 docker 目录旁放 compose 覆盖文件。

.DESCRIPTION
    步骤：
      1. 把 Docker CLI 加入当前会话 PATH（本机 PATH 未刷新时必需）
      2. 稀疏克隆 langgenius/dify（只取 docker/ 目录）到 deploy/dify
      3. 生成 deploy/dify/docker/.env（若无），并补齐本项目需要的变量
      4. 把 deploy/dify.override.yaml 复制为 docker-compose.override.yaml
      5. docker compose up -d

.NOTES
    幂等：重复执行不会覆盖已有 .env 与已有密钥。
#>
[CmdletBinding()]
param(
    [switch]$SkipUp,          # 只准备文件，不启动
    [switch]$RecreateEnv      # 强制从 .env.example 重建 .env（会丢失自定义项，慎用）
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$difyRoot = Join-Path $repo 'deploy\dify'
$dockerDir = Join-Path $difyRoot 'docker'
$envFile = Join-Path $dockerDir '.env'
$composeUrl = 'https://github.com/langgenius/dify.git'

# 原生程序把进度写到 stderr 属正常；$ErrorActionPreference='Stop' 会把它当致命错误，故统一走本函数
function Invoke-Native {
    # 注意：必须用显式数组传参。PowerShell 会把裸 `-d` 当成函数参数（-Debug 前缀）吞掉，
    # 导致 `docker compose up -d` 变成 `docker compose up`，脚本被前台日志流挂住。
    param(
        [Parameter(Mandatory, Position = 0)][string]$Exe,
        [Parameter(Mandatory, Position = 1)][string[]]$Arguments
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) { throw "命令失败（exit=$code）：$Exe $($Arguments -join ' ')" }
}
function Add-DockerToPath {
    $bin = 'C:\Program Files\Docker\Docker\resources\bin'
    if ((Test-Path $bin) -and ($env:PATH -notlike "*$bin*")) {
        $env:PATH = "$bin;$env:PATH"
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "找不到 docker 命令：请确认 Docker Desktop 已安装并启动"
    }
}

function Set-EnvVar([string]$Path, [string]$Key, [string]$Value) {
    $lines = @(Get-Content -LiteralPath $Path)
    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    if ($lines | Where-Object { $_ -match $pattern }) {
        $lines = $lines | ForEach-Object { if ($_ -match $pattern) { "$Key=$Value" } else { $_ } }
    } else {
        # 末尾可能是空行，替换最后一行空行避免文件不断变长
        $lines = @($lines + "$Key=$Value")
    }
    # 必须无 BOM：带 BOM 会让 compose 解析首行键失败（Windows PowerShell 5.1 的 -Encoding UTF8 会写 BOM）
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, [string[]]$lines, $utf8)
}

function Get-EnvVar([string]$Path, [string]$Key) {
    $m = Get-Content -LiteralPath $Path | Select-String -Pattern "^\s*$([regex]::Escape($Key))\s*=(.*)$"
    if ($m) { return $m.Matches[0].Groups[1].Value.Trim() }
    return $null
}

function New-Secret {
    # 兼容 Windows PowerShell 5.1：RandomNumberGenerator.Fill 是 .NET Core 才有的
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    try {
        $bytes = New-Object byte[] 24
        $rng.GetBytes($bytes)
        return ([Convert]::ToBase64String($bytes)).Replace('+','A').Replace('/','B').TrimEnd('=')
    } finally { $rng.Dispose() }
}

Write-Host '[1/5] 检查 Docker...' -ForegroundColor Cyan
Add-DockerToPath
$ver = Invoke-Native -Exe docker -Arguments @('version','--format','{{.Server.Version}}')
Write-Host "      Docker Engine $ver"

Write-Host '[2/5] 准备 Dify 源码（只取 docker 目录）...' -ForegroundColor Cyan
if (-not (Test-Path $dockerDir)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $difyRoot) | Out-Null
    & git clone --depth 1 --filter=blob:none --sparse $composeUrl $difyRoot
    Push-Location $difyRoot
    try { Invoke-Native -Exe git -Arguments @('sparse-checkout','set','docker') } finally { Pop-Location }
} else {
    Write-Host '      已存在，跳过克隆'
}

Write-Host '[3/5] 生成 .env...' -ForegroundColor Cyan
if ((Test-Path $envFile) -and $RecreateEnv) { Remove-Item -LiteralPath $envFile -Force }
if (-not (Test-Path $envFile)) {
    Copy-Item -LiteralPath (Join-Path $dockerDir '.env.example') -Destination $envFile
}
# campus-kb 与 Dify 同处 ssrf_proxy_network：域名必须放行，否则被 SSRF 代理拦下
Set-EnvVar $envFile 'SSRF_PROXY_ALLOW_PRIVATE_DOMAINS' 'campus-kb'
# 自研服务密钥：首次生成后沿用，避免每次重启都要重配 Dify
if (-not (Get-EnvVar $envFile 'CAMPUS_KB_API_KEY')) {
    Set-EnvVar $envFile 'CAMPUS_KB_API_KEY' (New-Secret)
}
Write-Host "      CAMPUS_KB_API_KEY = $(Get-EnvVar $envFile 'CAMPUS_KB_API_KEY')"

Write-Host '[4/5] 安装 compose 覆盖文件...' -ForegroundColor Cyan
$override = Join-Path $dockerDir 'docker-compose.override.yaml'
Copy-Item -LiteralPath (Join-Path $repo 'deploy\dify.override.yaml') -Destination $override -Force
Write-Host '      已写入 docker-compose.override.yaml'

if ($SkipUp) { Write-Host '跳过启动（-SkipUp）' -ForegroundColor Yellow; return }

Write-Host '[5/5] docker compose up -d ...' -ForegroundColor Cyan
Push-Location $dockerDir
try { Invoke-Native -Exe docker -Arguments @('compose','up','-d','--build') } finally { Pop-Location }

Write-Host ''
Write-Host '完成。检查：' -ForegroundColor Green
Write-Host '  docker compose ps'
Write-Host '  http://localhost/          （Dify Web，首次进入需设置管理员账号）'
Write-Host '  http://127.0.0.1:8088/health （campus-kb 自身健康检查）'
