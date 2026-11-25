#!/usr/bin/env pwsh
# 首次安装：检查工具、安装 Python/前端依赖、创建数据目录并构建前端。
# 运行：pwsh.exe -File scripts/setup.ps1
# 说明：不会下载任何大模型，也不会安装 Worker 环境。

[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
Set-Location $Root

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "== $Title ==" -ForegroundColor Cyan
}

function Test-Tool([string]$Command, [string]$Label) {
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    if ($found) {
        Write-Host ("  OK      {0}" -f $Label) -ForegroundColor Green
        return $true
    }
    Write-Host ("  MISSING {0}（{1}）" -f $Label, $Command) -ForegroundColor Yellow
    return $false
}

Write-Host "=== NovelVoice Studio 阶段 1 安装 ===" -ForegroundColor Cyan
Write-Host "项目目录: $Root"

Write-Section "检查基础工具"
$uvOk = Test-Tool "uv" "uv"
$nodeOk = Test-Tool "node.exe" "Node.js"
$npmOk = Test-Tool "npm.cmd" "npm"
if (-not ($uvOk -and $nodeOk -and $npmOk)) {
    Write-Host "缺少必需工具，请先安装后再运行本脚本：https://docs.astral.sh/uv/ 与 https://nodejs.org/" -ForegroundColor Red
    exit 1
}

$nodeVersion = (& node.exe --version).Trim().TrimStart("v")
if ([version]$nodeVersion -lt [version]"20.19.0") {
    Write-Host "  WARN    Node.js $nodeVersion 低于前端要求（>=20.19）" -ForegroundColor Yellow
} else {
    Write-Host "  OK      Node.js $nodeVersion"
}

Test-Tool "ffmpeg.exe" "FFmpeg（阶段 2 导出需要）" | Out-Null
Test-Tool "ffprobe.exe" "ffprobe（阶段 2 导出需要）" | Out-Null
$ollamaExe = (Get-Command "ollama.exe" -ErrorAction SilentlyContinue).Source
if (-not $ollamaExe) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $candidate) {
        $ollamaExe = $candidate
    }
}
if ($ollamaExe) {
    Write-Host "  OK      Ollama（$ollamaExe）" -ForegroundColor Green
} else {
    Write-Host "  MISSING Ollama（阶段 2 LLM 需要，阶段 1 不影响）" -ForegroundColor Yellow
}

Write-Section "安装后端依赖"
& uv sync
if ($LASTEXITCODE -ne 0) {
    throw "uv sync 失败（退出码 $LASTEXITCODE）"
}

Write-Section "准备配置与数据目录"
$configPath = Join-Path $Root "config\config.yaml"
$examplePath = Join-Path $Root "config\config.example.yaml"
if (-not (Test-Path $configPath)) {
    Copy-Item $examplePath $configPath
    Write-Host "  已创建 config/config.yaml（可自行修改，已被 git 忽略）" -ForegroundColor Green
} else {
    Write-Host "  保留已有 config/config.yaml"
}
$dataDir = (& $Python -c "from novelvoice_studio.config import get_settings; print(get_settings().ensure_dirs())").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "初始化数据目录失败"
}
Write-Host "  数据目录: $dataDir" -ForegroundColor Green

Write-Section "安装前端依赖"
Push-Location (Join-Path $Root "frontend")
try {
    if (-not (Test-Path "node_modules")) {
        & npm.cmd install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            throw "npm install 失败（退出码 $LASTEXITCODE）"
        }
    } else {
        Write-Host "  node_modules 已存在，跳过安装（依赖变更时可删除后重跑）"
    }

    if (-not $SkipFrontendBuild) {
        Write-Host "  构建前端（vue-tsc + vite build）..."
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) {
            throw "前端构建失败（退出码 $LASTEXITCODE）"
        }
    } else {
        Write-Host "  已按 -SkipFrontendBuild 跳过构建；start.ps1 需要 frontend/dist" -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}

Write-Section "Ollama（可选）"
try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
    $count = @($tags.models).Count
    Write-Host "  已连接 Ollama，本机模型数: $count" -ForegroundColor Green
} catch {
    if ($ollamaExe) {
        Write-Host "  Ollama 已安装，但本地服务未响应（阶段 1 Mock 模式不受影响）" -ForegroundColor Yellow
    } else {
        Write-Host "  未安装 Ollama（阶段 1 Mock 模式不受影响）" -ForegroundColor Yellow
    }
}

Write-Section "安装完成"
Write-Host "启动: pwsh.exe -File scripts/start.ps1" -ForegroundColor Green
Write-Host "停止: pwsh.exe -File scripts/stop.ps1"
Write-Host "开发: 终端 1 uv run novelvoice-studio；终端 2 cd frontend; npm run dev" -ForegroundColor DarkGray
Write-Host ""
Write-Host "阶段 1 使用 Mock Provider，无需下载模型。" -ForegroundColor DarkGray
Write-Host "阶段 2/3 的 Ollama / TTS 模型需另行确认，见 docs/reports/POC_REPORT.md 与 README。" -ForegroundColor DarkGray
Write-Host "本脚本不会自动下载模型，也不会安装 Worker 虚拟环境。" -ForegroundColor DarkGray