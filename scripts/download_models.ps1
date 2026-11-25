#!/usr/bin/env pwsh
# 显式确认后才运行：从 ModelScope 国内源下载 POC 所需模型。
# 用法：pwsh.exe -File scripts/download_models.ps1
# 说明：本脚本只下载模型，不安装 Python 依赖；下载前请确认磁盘空间（约 14 GB）。

$ErrorActionPreference = "Stop"

$cache = Join-Path $env:USERPROFILE "Documents\NovelVoiceStudio\cache\modelscope"
New-Item -ItemType Directory -Force -Path $cache | Out-Null

$models = @(
    @{ Id = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"; Dir = "Qwen3-TTS-12Hz-1.7B-VoiceDesign" },
    @{ Id = "Qwen/Qwen3-TTS-Tokenizer-12Hz"; Dir = "Qwen3-TTS-Tokenizer-12Hz" },
    @{ Id = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"; Dir = "Fun-CosyVoice3-0.5B-2512" },
    @{ Id = "iic/CosyVoice-ttsfrd"; Dir = "CosyVoice-ttsfrd" }
)

Write-Host "=== 从 ModelScope 下载 POC 模型 ===" -ForegroundColor Cyan
Write-Host "缓存目录: $cache"
Write-Host ""

foreach ($m in $models) {
    $target = Join-Path $cache $m.Dir
    Write-Host ("下载 {0} -> {1}" -f $m.Id, $target) -ForegroundColor Green
    uvx modelscope download $m.Id --local_dir $target --max-workers 8
    if ($LASTEXITCODE -ne 0) {
        throw ("下载失败: {0}" -f $m.Id)
    }
}

Write-Host "全部模型下载完成。" -ForegroundColor Cyan
