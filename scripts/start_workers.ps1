#!/usr/bin/env pwsh
# 启动阶段 3 的两个本机语音 Worker（Qwen3-TTS 8766 / CosyVoice3 8767）。
# 只监听回环地址；日志与 PID 记录放在 .run/workers，不会结束未知进程。
# 运行：pwsh.exe -File scripts/start_workers.ps1 [-Preload]

[CmdletBinding()]
param(
    [switch]$Preload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $Root ".run\workers"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$workers = @(
    [pscustomobject]@{
        Name = "qwen_tts"
        Label = "Qwen3-TTS VoiceDesign"
        Dir = Join-Path $Root "workers\qwen_tts_worker"
        Python = Join-Path $Root "workers\qwen_tts_worker\.venv\Scripts\python.exe"
        Server = Join-Path $Root "workers\qwen_tts_worker\server.py"
        Port = 8766
    },
    [pscustomobject]@{
        Name = "cosyvoice"
        Label = "CosyVoice3 VC"
        Dir = Join-Path $Root "workers\cosyvoice_worker"
        Python = Join-Path $Root "workers\cosyvoice_worker\.venv\Scripts\python.exe"
        Server = Join-Path $Root "workers\cosyvoice_worker\server.py"
        Port = 8767
    }
)

function Test-WorkerHealth([int]$Port) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -NoProxy
        return $null -ne $response
    } catch {
        return $false
    }
}

$started = @()
foreach ($worker in $workers) {
    if (-not (Test-Path $worker.Python)) {
        Write-Host "跳过 $($worker.Label)：未找到 Worker venv" -ForegroundColor Yellow
        Write-Host "  $($worker.Python)" -ForegroundColor DarkGray
        continue
    }
    if (-not (Test-Path $worker.Server)) {
        Write-Host "跳过 $($worker.Label)：未找到 server.py" -ForegroundColor Yellow
        continue
    }
    if (Test-WorkerHealth $worker.Port) {
        Write-Host "$($worker.Label) 已在 127.0.0.1:$($worker.Port) 运行，跳过启动。" -ForegroundColor Green
        continue
    }

    $stdout = Join-Path $RunDir "$($worker.Name).out.log"
    $stderr = Join-Path $RunDir "$($worker.Name).err.log"
    $arguments = @($worker.Server, "--host", "127.0.0.1", "--port", "$($worker.Port)")
    if ($Preload) {
        $arguments += "--preload"
    }

    Write-Host "启动 $($worker.Label) ..." -ForegroundColor Cyan
    $process = Start-Process `
        -FilePath $worker.Python `
        -ArgumentList $arguments `
        -WorkingDirectory $worker.Dir `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru

    $state = [pscustomobject]@{
        name = $worker.Name
        label = $worker.Label
        pid = $process.Id
        port = $worker.Port
        server = $worker.Server
        started_at = (Get-Date).ToString("o")
        stdout = $stdout
        stderr = $stderr
    }
    $state | ConvertTo-Json | Set-Content -Path (Join-Path $RunDir "$($worker.Name).json") -Encoding utf8
    $started += $worker
}

foreach ($worker in $started) {
    $deadline = (Get-Date).AddSeconds(90)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-WorkerHealth $worker.Port) {
            $healthy = $true
            break
        }
        Start-Sleep -Milliseconds 800
    }
    if ($healthy) {
        $state = Get-Content (Join-Path $RunDir "$($worker.Name).json") -Raw | ConvertFrom-Json
        Write-Host "$($worker.Label) 已就绪：127.0.0.1:$($worker.Port) (PID $($state.pid))" -ForegroundColor Green
    } else {
        Write-Host "$($worker.Label) 健康检查超时。" -ForegroundColor Red
        Write-Host "  错误日志：$((Join-Path $RunDir "$($worker.Name).err.log"))" -ForegroundColor Yellow
        $tail = Get-Content (Join-Path $RunDir "$($worker.Name).err.log") -Tail 20 -ErrorAction SilentlyContinue
        if ($tail) {
            Write-Host ($tail -join [Environment]::NewLine) -ForegroundColor DarkGray
        }
    }
}

Write-Host ""
Write-Host "Worker 默认按需加载模型；首次设计音色 / 生成语音会明显变慢。" -ForegroundColor DarkGray
Write-Host "日志：$RunDir" -ForegroundColor DarkGray
Write-Host "停止：pwsh.exe -File scripts/stop_workers.ps1" -ForegroundColor DarkGray