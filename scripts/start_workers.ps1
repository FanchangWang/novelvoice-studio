#!/usr/bin/env pwsh
# 兼容入口：workers/ 引擎服务目录的启动脚本。
# 实际逻辑在 src/novelvoice_studio/engine_services.py，跨平台控制命令为：
#   uv run novelvoice-studio worker start [--preload]
#   uv run novelvoice-studio worker status
#   uv run novelvoice-studio worker logs
# 本脚本只负责找到核心 venv 并转交参数，不再重复实现进程管理。

[CmdletBinding()]
param(
    [switch]$Preload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
Set-Location $Root

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {
    # 终端不支持时保持原编码。
}

if (-not (Test-Path $Python)) {
    Write-Host "未找到核心 .venv。请先运行: pwsh.exe -File scripts/setup.ps1" -ForegroundColor Red
    exit 1
}

$workerArgs = @("-m", "novelvoice_studio.engine_services", "start")
if ($Preload) {
    $workerArgs += "--preload"
}

& $Python @workerArgs
exit $LASTEXITCODE