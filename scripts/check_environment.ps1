#!/usr/bin/env pwsh
# 环境检查：只报告状态，不安装软件、不下载模型。
# 运行：pwsh.exe -File scripts/check_environment.ps1

$ErrorActionPreference = "Continue"

function Write-Status {
    param([string]$Status, [string]$Name, [string]$Detail)
    $color = switch ($Status) { "OK" { "Green" } "MISSING" { "Red" } default { "Yellow" } }
    Write-Host ("{0,-8} {1,-24} {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Test-Cmd {
    param([string]$Name, [string]$Label, [string[]]$Args = @())
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { Write-Status "MISSING" $Label "未找到 $Name"; return }
    try {
        $out = & $Name @Args 2>&1 | Select-Object -First 1
        Write-Status "OK" $Label "$out"
    } catch {
        Write-Status "WARN" $Label "命令存在但执行失败: $_"
    }
}

Write-Host "=== NovelVoice Studio 环境检查 ===" -ForegroundColor Cyan
Write-Host ""

Test-Cmd "uv" "uv" @("--version")
Test-Cmd "uvx" "uvx" @("--version")
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $py = & uv run python --version 2>&1 | Select-Object -First 1
    Write-Status "OK" "Python (uv)" "$py"
}

Test-Cmd "node.exe" "Node.js" @("--version")
Test-Cmd "npm.cmd" "npm" @("--version")
Test-Cmd "ffmpeg.exe" "FFmpeg" @("-version")
Test-Cmd "ffprobe.exe" "ffprobe" @("-version")
Test-Cmd "ollama.exe" "Ollama CLI" @("--version")
Test-Cmd "nvidia-smi.exe" "nvidia-smi" @("--query-gpu=name,memory.total,driver_version --format=csv,noheader")

# Ollama 服务健康检查（可选组件）
try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 -ErrorAction Stop
    $models = @($tags.models | ForEach-Object { $_.name })
    Write-Status "OK" "Ollama 服务" ("已安装模型: " + ($models -join ", "))
} catch {
    Write-Status "WARN" "Ollama 服务" "未响应 http://127.0.0.1:11434（服务未启动或尚未安装）"
}

# 数据盘空间
try {
    $drive = Get-PSDrive -Name C -ErrorAction Stop
    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    Write-Status "OK" "C: 可用空间" "$freeGB GB"
} catch {
    Write-Status "WARN" "C: 可用空间" "读取失败"
}

Write-Host ""
Write-Host "说明：本脚本不安装任何软件，也不下载模型。" -ForegroundColor DarkGray
