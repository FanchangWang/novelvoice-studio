#!/usr/bin/env pwsh
# 停止本机服务：优先请求优雅退出，必要时 -Force 结束本项目自己的进程。
# 运行：pwsh.exe -File scripts/stop.ps1 [-Force]

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Control = Join-Path $Root "scripts\server_control.py"
Set-Location $Root

if (-not (Test-Path $Python)) {
    Write-Host "未找到 .venv，无法调用停止 helper。" -ForegroundColor Red
    Write-Host "若 .run/server.json 存在，请人工核对 PID 后处理；脚本不会结束未知进程。" -ForegroundColor Yellow
    exit 1
}

$arguments = @($Control, "stop")
if ($Force) {
    $arguments += "--force"
}
& $Python @arguments
exit $LASTEXITCODE