#!/usr/bin/env pwsh
# 停止由 scripts/start_workers.ps1 启动的语音 Worker。
# 只处理 .run/workers 中记录且命令行匹配本项目 server.py 的进程；
# 端口被其他进程占用时只报告，不结束未知进程。
# 运行：pwsh.exe -File scripts/stop_workers.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $Root ".run\workers"

function Test-WorkerHealth([int]$Port) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -NoProxy
        return $null -ne $response
    } catch {
        return $false
    }
}

if (-not (Test-Path $RunDir)) {
    Write-Host "没有 Worker 运行记录。" -ForegroundColor DarkGray
    exit 0
}

$states = @(Get-ChildItem -Path $RunDir -Filter "*.json" -File)
if ($states.Count -eq 0) {
    Write-Host "没有 Worker 运行记录。" -ForegroundColor DarkGray
} else {
    foreach ($file in $states) {
        try {
            $state = Get-Content $file.FullName -Raw | ConvertFrom-Json
        } catch {
            Write-Host "忽略无法解析的状态文件：$($file.Name)" -ForegroundColor Yellow
            continue
        }
        if (-not $state.pid) {
            Remove-Item $file.FullName -Force
            continue
        }

        $process = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
        if (-not $process) {
            Write-Host "$($state.label) 已不在运行（PID $($state.pid)）。" -ForegroundColor DarkGray
            Remove-Item $file.FullName -Force
            continue
        }

        $verified = $false
        try {
            $info = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.pid)" -ErrorAction Stop
            $command = ""
            if ($info.CommandLine) {
                $command = $info.CommandLine.Replace("/", "\")
            }
            $expected = ([string]$state.server).Replace("/", "\")
            if ($command -and $expected -and $command.IndexOf($expected, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $verified = $true
            }
        } catch {
            $verified = $false
        }

        if (-not $verified) {
            Write-Host "拒绝结束 PID $($state.pid)：命令行与记录的 server.py 不一致。" -ForegroundColor Yellow
            Write-Host "  记录：$($state.server)" -ForegroundColor DarkGray
            Write-Host "  请人工核对后处理；状态文件保留：$($file.FullName)" -ForegroundColor Yellow
            continue
        }

        try {
            Stop-Process -Id ([int]$state.pid) -Force
            Wait-Process -Id ([int]$state.pid) -Timeout 10 -ErrorAction SilentlyContinue
            Write-Host "$($state.label) 已停止（PID $($state.pid)）。" -ForegroundColor Green
        } catch {
            Write-Host "停止 PID $($state.pid) 失败：$($_.Exception.Message)" -ForegroundColor Red
            continue
        }
        Remove-Item $file.FullName -Force
    }
}

foreach ($port in @(8766, 8767)) {
    if (Test-WorkerHealth $port) {
        Write-Host "仍检测到 127.0.0.1:$port 有 Worker 响应，可能不是由本项目启动。" -ForegroundColor Yellow
    }
}

exit 0