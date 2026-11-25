#!/usr/bin/env pwsh
# 本机启动入口：构建检查 → 前台服务由 helper 以分离进程启动 → 打开浏览器。
# 运行：pwsh.exe -File scripts/start.ps1 [-NoBrowser]

[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Control = Join-Path $Root "scripts\server_control.py"
Set-Location $Root

function Stop-WithMessage([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Python)) {
    Stop-WithMessage "未找到 .venv。请先运行: pwsh.exe -File scripts/setup.ps1"
}

# 读取配置（host/port/数据目录/前端 dist），不启动服务。
$infoRaw = & $Python $Control info
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "读取配置失败。"
}
$info = $infoRaw | ConvertFrom-Json
if (-not $info.frontend_built) {
    Write-Host "前端尚未构建：$($info.frontend_dist)" -ForegroundColor Red
    Stop-WithMessage "请运行: pwsh.exe -File scripts/setup.ps1"
}

Write-Host "正在检查 NovelVoice Studio 服务..." -ForegroundColor Cyan

# 已由本项目 helper 启动且健康：直接复用。
& $Python $Control status *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "服务已在运行: $($info.url)" -ForegroundColor Green
    if (-not $NoBrowser) {
        Start-Process $info.url
    }
    exit 0
}

# 端口被占用时只报告，不结束任何进程。
$listeners = @(Get-NetTCPConnection -LocalPort ([int]$info.port) -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $ownerPid = ($listeners | Select-Object -First 1).OwningProcess
    $process = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    $commandLine = ""
    try {
        $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction Stop).CommandLine
    } catch {
        $commandLine = "(无法读取命令行)"
    }
    Write-Host "端口 $($info.port) 已被占用，未结束任何进程：" -ForegroundColor Yellow
    Write-Host ("  PID: {0}" -f $ownerPid)
    if ($process) {
        Write-Host ("  进程: {0}" -f $process.ProcessName)
    }
    Write-Host ("  命令行: {0}" -f $commandLine)
    Write-Host "若确认是残留的本项目服务，请检查 .run/server.json 或手动处理端口占用。" -ForegroundColor Yellow
    exit 1
}

Write-Host "启动服务..." -ForegroundColor Cyan
& $Python $Control start
$code = $LASTEXITCODE
if ($code -ne 0 -and $code -ne 2) {
    Write-Host "启动失败，退出码 $code。日志: .run/server.log" -ForegroundColor Red
    exit 1
}

if (-not $NoBrowser) {
    Start-Process $info.url
}
Write-Host ""
Write-Host "地址: $($info.url)" -ForegroundColor Green
Write-Host "数据: $($info.data_dir)"
Write-Host "日志: .run/server.log"
Write-Host "停止: pwsh.exe -File scripts/stop.ps1"
exit 0