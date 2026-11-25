# 本机模型清单与使用方式

> 状态：2026-11-26。本文只记录模型 ID、官方页面、获取命令和运行方式，不提交模型文件。
> 原则：Ollama 模型由 Ollama 自己管理，放在其默认目录，不放进本项目数据目录；项目只保存模型 ID 和调用配置。

## 1. 当前使用的模型

| 用途 | 模型 ID | 官方页面 | 本机缓存占用 | 运行时使用者 |
|---|---|---|---:|---|
| 小说分析 LLM | `modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest`（Ollama 引用名） | [unsloth/Qwen3.5-4B-GGUF](https://modelscope.cn/models/unsloth/Qwen3.5-4B-GGUF) | 约 3.2 GB | 本机 Ollama `127.0.0.1:11434` |
| 音色设计 VoiceDesign | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | [Qwen3-TTS-12Hz-1.7B-VoiceDesign](https://modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) | 约 4.3 GB | Qwen TTS Worker `127.0.0.1:8766` |
| TTS / 音色转换 | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` | [Fun-CosyVoice3-0.5B-2512](https://modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512) | 约 9.1 GB | CosyVoice Worker `127.0.0.1:8767` |
| TTS 文本前端辅助（POC 已下载） | `iic/CosyVoice-ttsfrd` | [CosyVoice-ttsfrd](https://modelscope.cn/models/iic/CosyVoice-ttsfrd) | 约 656 MB | 当前未直接加载，见下文说明 |
| TTS Tokenizer（POC 已下载） | `Qwen/Qwen3-TTS-Tokenizer-12Hz` | [Qwen3-TTS-Tokenizer-12Hz](https://modelscope.cn/models/Qwen/Qwen3-TTS-Tokenizer-12Hz) | 约 651 MB | 当前未直接读取独立目录，见下文说明 |

> 体积为本机 2026-11-26 的缓存占用，只作磁盘估算，不等于显存占用。
> LLM 实际拉取的具体文件是 `Qwen3.5-4B-Q4_K_M.gguf` 与 `mmproj-BF16.gguf`；`ollama show` 显示 4.2B 参数、Q4_K_M 量化、262144 上下文。

### 1.1 当前未直接参与运行的两个下载项

- `iic/CosyVoice-ttsfrd`：POC 阶段下载过，但 CosyVoice 的 ttsfrd 未装配成功。阶段 3 起 CosyVoice Worker 使用 `wetext==0.0.7` 自带的 FST，日志为 `use wetext frontend`，当前链路不读取 ttsfrd 资源。
- `Qwen/Qwen3-TTS-Tokenizer-12Hz`：`download_models.ps1` 会下载独立副本；Qwen TTS Worker 实际加载 `Qwen3-TTS-12Hz-1.7B-VoiceDesign`，其仓库内已包含 `speech_tokenizer/`。独立目录暂时只作为 POC 资源保留。

这两个目录可以保留用于复现或后续切换前端；删除不影响当前 Worker 启动，但重新运行下载脚本会再次拉取。

## 2. Ollama LLM

### 2.1 存储位置

- 模型由 Ollama 管理，默认目录：`%USERPROFILE%\.ollama\models`。
- 本项目**不设置** `OLLAMA_MODELS`，也**不把 Ollama 模型放进** `%USERPROFILE%\Documents\NovelVoiceStudio` 数据目录。
- 应用只通过回环 HTTP `http://127.0.0.1:11434` 访问 Ollama，不直接读取模型目录。

2026-11-26 已把模型从项目缓存目录迁回上述默认目录；源目录已删除。

### 2.2 获取与检查

需要联网，且由用户显式执行；应用不会自动下载：

```powershell
ollama pull modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest
ollama list
ollama show modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest
```

本机当前 `ollama list` 应包含：

```text
modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest    a2edab9721bf    3.4 GB
```

### 2.3 在应用中使用

`config/config.yaml` 的 `llm.model` 必须与 Ollama 中的模型名完全一致：

```yaml
llm:
  provider: "ollama"
  base_url: "http://127.0.0.1:11434"
  model: "modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest"
  enable_thinking: false
```

然后以真实模式启动：

```powershell
$env:NOVELVOICE_PROVIDERS__MODE = "real"
pwsh.exe -File scripts/start.ps1
```

也可以在启动前用环境变量覆盖模型：

```powershell
$env:NOVELVOICE_PROVIDERS__MODE = "real"
$env:NOVELVOICE_LLM__MODEL = "modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest"
pwsh.exe -File scripts/start.ps1
```

注意：

- Qwen3.5 默认开启 thinking，内容会进入 `message.thinking` 而不是 `message.content`。应用请求已固定发送 `think=false`；自行用 `/api/chat` 调试时也要带上该字段。
- 应用不会自动拉取模型。模型缺失时，状态页会明确提示执行 `ollama pull <模型ID>`。
- 手工对话可用 `ollama run modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest`。
- 结构化 JSON 冒烟（POC 脚本，使用单下划线的 `NOVELVOICE_LLM_MODEL`，不要与应用的 `NOVELVOICE_LLM__MODEL` 混淆）：

```powershell
$env:NOVELVOICE_LLM_MODEL = "modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest"
uv run python -m novelvoice_studio.poc.ollama_json_check
```

## 3. 语音模型

### 3.1 存储位置

语音模型由本项目脚本显式下载，默认放在应用数据目录：

```text
%USERPROFILE%\Documents\NovelVoiceStudio\cache\modelscope\
  Qwen3-TTS-12Hz-1.7B-VoiceDesign\
  Fun-CosyVoice3-0.5B-2512\
  CosyVoice-ttsfrd\
  Qwen3-TTS-Tokenizer-12Hz\
```

这些是语音模型的本地缓存，不属于 Ollama 目录，也不会提交到仓库。CosyVoice Worker 还需要 `workers/cosyvoice_worker/CosyVoice` 源码目录；该源码来自 [FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice)，当前验证提交为 `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`。

### 3.2 下载

需要约 14 GB 磁盘空间，由用户显式执行：

```powershell
pwsh.exe -File scripts/download_models.ps1
```

脚本实际调用：

```text
uvx modelscope download <模型ID> --local_dir <缓存目录>
```

它只下载语音模型，不下载 Ollama LLM，也不安装 Python 依赖。

### 3.3 启动 Worker

两个 Worker 使用各自的虚拟环境，不要装进核心 `.venv`：

```powershell
cd workers/qwen_tts_worker
uv sync
cd ../cosyvoice_worker
uv sync
cd ../..

pwsh.exe -File scripts/start_workers.ps1
# 可选：启动时预加载模型
pwsh.exe -File scripts/start_workers.ps1 -Preload
```

检查连通性：

```powershell
Invoke-RestMethod http://127.0.0.1:8766/health
Invoke-RestMethod http://127.0.0.1:8767/health
```

两个 Worker 只监听回环地址：8766（VoiceDesign）与 8767（CosyVoice3 / Voice Conversion / 去齿）。应用在「声音库」与「配音」页调用它们；未启动 Worker 时不会伪装成功。

### 3.4 在应用中使用

1. 启动两个 Worker。
2. 设置 `$env:NOVELVOICE_PROVIDERS__MODE = "real"` 并启动应用。
3. 在「声音库」为旁白或角色生成、试听并绑定音色。
4. 在书籍工作区的「配音」页逐段生成、试听和回退版本。

Worker 的模型目录可通过 `NOVELVOICE_QWEN_MODEL_DIR` / `NOVELVOICE_COSYVOICE_MODEL_DIR` 覆盖；两者默认都从 `NOVELVOICE_MODEL_CACHE` 下的对应子目录加载。

## 4. 许可证与核对边界

- `ollama show` 对当前 Qwen3.5 GGUF 显示 Apache License 2.0；具体分发条件仍以模型页面和上游仓库为准。
- VoiceDesign、CosyVoice3、ttsfrd、Tokenizer 的许可证需分别查看各自 ModelScope 页面；阶段 0 的许可证核对仍未完成，不能把本文当作商用许可结论。
- 本文记录的链接在 2026-11-26 均可访问；模型文件、权重和第三方源码不进入本仓库。

## 5. 相关文档

- [README](../README.md)：安装、启动、配置与数据目录。
- [POC_REPORT.md](reports/POC_REPORT.md)：阶段 0 实测记录、模型来源与试听结论。
- [STAGE3_REPORT.md](reports/STAGE3_REPORT.md)：阶段 3 Worker 协议、音质遗留项与测试结果。