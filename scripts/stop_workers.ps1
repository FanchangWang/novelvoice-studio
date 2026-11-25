#!/usr/bin/env pwsh
# 兼容入口：停止由引擎服务控制器启动的 Worker。
# 实际逻辑在 src/novelvoice_studio/engine_services.py，跨平台控制命令为：
#   uv run novelvoice-studio worker stop [--force]
# 只处理 .run/workers 中记录、且实例 ID/命令行可核实的本工具进程；
# 外部启动的服务只报告，不结束。

[CmdletBinding()]
param(
    [switch]$Force
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

$workerArgs = @("-m", "novelvoice_studio.engine_services", "stop")
if ($Force) {
    $workerArgs += "--force"
}

& $Python @workerArgs
exit $LASTEXITCODE