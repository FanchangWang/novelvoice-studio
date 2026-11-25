# 阶段 0 POC 验证报告

> 状态：**试听 gate 有条件通过（2026-11-25）**（LLM 验证通过、VoiceDesign 已生成 3 音色、CosyVoice3 已生成 neutral / angry / sad 三个情绪样本；用户试听评分：音色差异 8/10、跨情绪一致性 7/7/3、中文发音与杂音 6.5/10；sad 表现力与句尾气声列为阶段 3 音质遗留项）
> 日期：2026-11-25
> 原则：只记录实测结果。未执行的项一律保持「待验证」，不得伪造成功结论。

## 1. 硬件与运行环境（已实测）

| 项目 | 结果 | 证据 / 命令 |
|---|---|---|
| GPU | NVIDIA GeForce RTX 4070 Ti | `nvidia-smi` |
| 显存总量 | 12282 MiB（约 12 GB） | `nvidia-smi --query-gpu=memory.total` |
| 驱动 / CUDA UMD | 610.88 / 13.3 | `nvidia-smi` |
| Python（核心） | 3.12.15（uv 管理） | `uv run python --version` |
| Python（CosyVoice Worker） | 3.10.22（uv 管理，独立 venv） | `uv venv --python 3.10` |
| uv / uvx | 0.13.9 | `uv --version` |
| Node.js / npm | 26.13.1 / 12.2.3 | `node --version` / `npm --version` |
| FFmpeg / ffprobe | 9.1（Gyan.FFmpeg，winget） | `ffmpeg -version` |
| 磁盘可用空间 | C: 约 1.02 TB | `Get-PSDrive C` |
| Ollama | 0.40.18，已安装并运行于 `127.0.0.1:11434` | GitHub 加速器下载安装包 + 静默安装 |
| Ollama 模型目录 | `%USERPROFILE%\.ollama\models`（Ollama 默认目录；2026-11-26 已从项目缓存迁回） | `ollama list`；未设置 `OLLAMA_MODELS` |
| 语音模型缓存 | `%USERPROFILE%\Documents\NovelVoiceStudio\cache\modelscope` | `scripts/download_models.ps1` |

## 2. 待验证清单

- [x] Ollama 安装完成并能通过 `/api/tags` 健康检查
- [x] 确认 `qwen3.5:4b` 在 Ollama 官方库真实存在（3.3–4.0 GB，256K 上下文）；实际采用 ModelScope 文本版 GGUF
- [x] 中文结构化 JSON 输出（Pydantic 校验通过，详见第 4 节）
- [x] Qwen3-TTS VoiceDesign 生成 3 个音色不同的参考声音（用户 2026-11-25 试听：差异 8/10，通过）
- [x] CosyVoice3 使用同一参考音生成 neutral / angry / sad（2026-11-25 修复脚本后已生成 3 个样本，详见第 4 节）
- [x] 同一角色不同情绪仍可辨认为同一声音（**人工主观试听 gate**；用户 2026-11-25 确认通过，评分与遗留项见第 4.1、8 节）
- [x] 中文发音与杂音人工试听：6.5/10，有条件通过；句尾存在气声，转入阶段 3 修复
- [x] GPU 分时加载未出现持续 OOM（Ollama 模型卸载后，依次独立加载 VoiceDesign 与 CosyVoice3；未测试 Provider 的显式 unload API）
- [x] Windows 原生环境依赖已解决，无需 WSL2 Worker（`openai-whisper` 用 setuptools 80.9 + `--no-build-isolation` 补装）

## 3. 模型候选与实际来源（已获用户确认）

| 用途 | 模型 | 体积 | 实际来源 | 状态 |
|---|---|---|---|---|
| LLM | [`modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest`](https://modelscope.cn/models/unsloth/Qwen3.5-4B-GGUF) | 3,416,520,596 B（约 3.18 GiB）；Q4_K_M + mmproj-BF16 | ModelScope 国内源（经 Ollama） | ✅ 已下载并验证 |
| Voice Design | [`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`](https://modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) | 约 4.21 GiB | ModelScope 官方镜像 | ✅ 已下载 |
| TTS | [`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`](https://modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512) | 完整仓库 19 个文件约 9.08 GiB | ModelScope 官方镜像 | ✅ 已下载 |
| TTS 辅助 | [`iic/CosyVoice-ttsfrd`](https://modelscope.cn/models/iic/CosyVoice-ttsfrd) | 含 339 MB resource.zip/tar | ModelScope | ✅ 已下载；当前使用 wetext，ttsfrd 未直接加载 |
| TTS Tokenizer | [`Qwen/Qwen3-TTS-Tokenizer-12Hz`](https://modelscope.cn/models/Qwen/Qwen3-TTS-Tokenizer-12Hz) | 较小 | ModelScope | ✅ 已下载；当前由 VoiceDesign 内置 `speech_tokenizer/` 提供 |

> 用户已明确确认：LLM 用 ModelScope 文本版，语音模型用 ModelScope 魔搭源，CosyVoice3 完整下载。
> 语音模型下载集中在 `scripts/download_models.ps1`，仅在用户确认后运行；Ollama LLM 由 `ollama pull modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest` 获取，不经过该脚本。
> 模型页面、默认目录与完整使用方式见 [MODELS.md](../MODELS.md)。Ollama 模型由 Ollama 管理，默认位于 `%USERPROFILE%\.ollama\models`，不占本项目数据目录。

## 4. 实测记录

### 2026-11-25 LLM 结构化 JSON 验证

- 命令：`NOVELVOICE_LLM_MODEL=modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest uv run python -m novelvoice_studio.poc.ollama_json_check`
- 脚本：`src/novelvoice_studio/poc/ollama_json_check.py`
- **第一次调用**：耗时 108.50s，`message.content` 长度为 0。原因：Qwen3.5 默认开启 thinking，输出进入 `message.thinking`。
- **修正**：请求参数增加 `"think": false`。
- **第二次调用**：耗时 1.17s，`content` 长度 254，`thinking` 长度 0；Pydantic 模型 `CharacterAnalysis` 校验通过。
- 实际输出（摘要）：识别出「李寻欢」（别名「小李探花」，male，confidence 0.9）和「林诗音」（female，confidence 0.9），均带原文证据片段。
- 结论：中文结构化 JSON 输出在目标硬件上可用；业务 Provider 实现必须显式关闭 thinking。

### VoiceDesign 三音色生成（2026-11-25）

- 脚本：`workers/qwen_tts_worker/voice_design_poc.py`
- 环境：Python 3.12 + `torch 2.11.0+cu128` + `qwen-tts 0.1.1`
- 模型加载：4.93s；`attn_implementation="sdpa"`（未装 flash-attn，官方提示仅影响速度）
- 同一参考文本，3 种人物描述，各生成一段约 5 秒参考音：

| 候选 | 描述 | 音频时长 | 生成耗时 |
|---|---|---|---|
| candidate_01 | 中年男性，中低音，沉稳略疲惫 | 5040ms | 19.79s |
| candidate_02 | 年轻女性，清亮温柔 | 4720ms | 16.49s |
| candidate_03 | 老年男性，沙哑缓慢 | 5760ms | 20.64s |

- 输出目录：`%USERPROFILE%\Documents\NovelVoiceStudio\cache\poc_output\voices`
- 注意：SoX 未安装，仅产生提示，不影响生成。
- [人工试听] 3 个音色是否明显不同：**已确认，8/10，通过**（2026-11-25，用户本人）。

### CosyVoice3 跨模型情绪生成尝试（2026-11-25）— 失败

- 脚本：`workers/cosyvoice_worker/cosyvoice_poc.py`
- 环境：Python 3.10.22 + `torch 2.3.1+cu121` + CosyVoice 源码（含 Matcha-TTS 子模块）
- 模型加载成功，耗时 61.45s。期间 wetext 从 ModelScope 下载大量 FST 资源；`hi/tn/tagger.fst`（31MB）多次返回 HTTP 403，日志出现 `no frontend is avaliable`，文本前端降级为可用子集。
- `openai-whisper==20231117` 通过 `setuptools==80.9.0` + `--no-build-isolation` 补装成功，解决了 `ModuleNotFoundError: whisper`。
- 推理阶段失败：`TypeError: 'generator' object is not subscriptable`，位置 `cosyvoice_poc.py` 第 74 行 `outputs[0]["tts_speech"]`。即 CosyVoice 的 `inference_instruct2(..., stream=False)` 返回的是生成器，脚本按列表取值导致失败。
- **结果：未生成 neutral / angry / sad 任何样本。** 跨模型音色迁移与情绪可控性均未获得实测结论。
- 修复方向：按生成器方式遍历 `for item in outputs: wav = item["tts_speech"]`，并按句拼接多个 chunk。

### CosyVoice3 情绪生成重跑（修复后，2026-11-25）

- 修复内容：`cosyvoice_poc.py` 改为遍历生成器，取 `item["tts_speech"]` 并用 `torch.cat(dim=-1)` 拼接分句 chunk；固定 `set_all_random_seed(42)`；记录文本前端状态。
- 命令：`workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/cosyvoice_poc.py`
- 结果：退出码 0。模型加载 6.88s（已缓存），3 个样本均生成（每个 1 个 chunk）：

| 情绪 | 指令 | 时长 | 生成耗时 | RTF（日志） |
|---|---|---|---|---|
| neutral | 用平静、克制的语气说这句话。 | 4760ms | 3.81s | 0.635 |
| angry | 愤怒而激动地大声质问，语速略快，保持角色音色不变。 | 4960ms | 3.27s | 0.553 |
| sad | 悲伤、压抑地低声说，语速较慢，保持角色音色不变。 | 8400ms | 5.46s | 0.581 |

- 音频事实：均为 24000 Hz 单声道 WAV；峰值 0.549 / 0.586 / 0.449，RMS 约 -19.7 / -20.0 / -20.1 dBFS（用 soundfile 计算，仅作客观记录，不代表听感）。
- 输出目录：`%USERPROFILE%\Documents\NovelVoiceStudio\cache\poc_output\emotions`
- 运行日志：`%USERPROFILE%\Documents\NovelVoiceStudio\logs\cosyvoice_poc.log`
- **已知限制（必须写进 Provider 设计）：**
  1. 本次 `inference_instruct2` 只把参考 WAV 当作音色提示，参考文本并未参与推理；`REF_TEXT` 仅记录在 `emotions.json` 中。跨模型「参考音 + 参考文本」中的文本条件在本 API 下未生效，不能据此宣称完整复刻了参考文本对应的音色迁移链路。
  2. 文本前端（ttsfrd / wetext）均未装配成功，运行日志出现 `no frontend is avaliable`，本次为无规范化直出。普通中文短句可用，但数字、英文缩写等场景尚未验证。
  3. 三种情绪的听感差异与同一角色辨识度当时尚未确认；现已由用户试听，结果见第 4.1 节。

### 4.1 人工试听结果（2026-11-25，用户本人）

试听文件：`candidate_01.wav` / `candidate_02.wav` / `candidate_03.wav`，以及 `neutral.wav` / `angry.wav` / `sad.wav`。

| 评估项 | 评分 | 判定 | 听感备注 |
|---|---|---|---|
| 三音色差异 | 8/10 | 通过 | 三个候选可明确区分，满足「至少 3 个音色不同的虚构声音」 |
| 跨情绪一致性 | neutral 7 / angry 7 / sad 3 | 通过 | 三种情绪仍可辨认为同一角色；sad 能听出悲伤意图，但表现力明显不足，最多 3 分 |
| 中文发音与杂音 | 6.5/10 | 有条件通过 | 句尾存在 `qi` / `si` / `ci` 一类气声，影响成片质感 |

- **gate 结论：有条件通过并关闭试听 gate。** 依据是需求书的二值条件「三音色可区分」与「跨情绪仍可辨认」均已满足；sad 表现力和句尾气声不构成 gate 失败，但构成阶段 3 的音质遗留项。
- **不等于音质验收通过。** 该结果不能宣称可交付有声书音质，也不能宣称「参考 WAV + 参考文本」完整链路通过。
- 初步归因：参考音本身是愤怒台词（`candidate_01` 参考文本为“你竟然敢骗我……”），可能把情绪和气息带入音色锚点；CosyVoice 文本前端未装配成功；0.5B 模型对 sad 的表现力有限；句尾气声也可能来自推理或 chunk 拼接。
- 阶段 3 音质修复与退出标准见 `docs/reports/STAGE3_AUDIO_QUALITY_PLAN.md`。

### 语音模型下载（2026-11-25）

- 命令：`pwsh.exe -File scripts/download_models.ps1`
- 来源：ModelScope 国内源，全程约 30–45 MB/s。
- CosyVoice3 完整快照 19 个文件（含 `llm.pt`、`llm.rl.pt` 等）已就绪。
- 本节仅记录下载当时的状态；语音推理与试听结果见上文，此处不重复结论。

### 环境准备

- Qwen3-TTS Worker（Python 3.12）：`qwen-tts==0.1.1` 已安装；PyPI 默认装到 CPU 版 torch，必须改用国内 PyTorch CUDA 镜像。
- CosyVoice Worker（Python 3.10）：源码已克隆（含 Matcha-TTS 子模块）；原 `requirements.txt` 中的 `openai-whisper` 在 Windows 构建失败，POC 不需要，已用 `requirements-poc.txt` 去除。

## 5. 依赖与许可证核对

- [ ] 具体 checkpoint 的许可证
- [ ] Python 依赖的许可证
- [ ] FFmpeg 二进制的许可证
- [ ] 替换模型的许可证

## 6. 结论与模型组合决策

- 阶段 0 的**技术链路已全部跑通**：LLM 结构化 JSON、VoiceDesign 三音色、CosyVoice3 三情绪样本均已产出；**人工试听 gate 已于 2026-11-25 有条件关闭**（见第 4.1、8 节），但不等于音质验收通过。
- 跨模型音色迁移（VoiceDesign → CosyVoice3）：已有可试听样本；但本次使用的 `inference_instruct2` **不消费参考文本**，只使用参考 WAV 的音色提示与自然语言指令，因此「参考 WAV + 参考文本」组合只完成了前半部分。不得宣称完整通过。
- 3 个音色之间是否明显不同、同一角色不同情绪是否仍可辨认，用户已完成试听：三音色差异 8/10；跨情绪 neutral/angry/sad 为 7/7/3，可辨认但 sad 表现力不足。
- 环境事实：两个 Worker 均已具备 CUDA torch（Qwen3-TTS：2.11.0+cu128；CosyVoice：2.3.1+cu121），分时加载未出现持续 OOM。CosyVoice3 二次加载（已缓存）耗时 6.88s。
- 环境备注（用户指出）：Worker 可在自身 `pyproject.toml` 中限定 `requires-python == "3.10"`，再用 `uv run` 自动选用该版本；当前实现使用独立 venv 隔离，按用户指示本次不再改动。
- 文本前端待办：`ttsfrd` 资源未装配到 CosyVoice 源码的 `pretrained_models/` 路径，`wetext` 初始化因 ModelScope 403 失败；阶段 3 接入真实 TTS 前需解决数字/符号规范化问题。
- 阶段 0 剩余待办：第 5 节许可证核对尚未完成；`inference_instruct2` 的参考文本条件未验证；音质遗留项（sad 表现力、句尾气声、参考音情绪偏差）转入阶段 3 首个里程碑。
- 下一步：先执行阶段 3 音质修复 spike（见 `docs/reports/STAGE3_AUDIO_QUALITY_PLAN.md`），通过内部音质门槛后再固化 VoiceDesign → CosyVoice3 链路；另用 `inference_zero_shot` 补测参考文本条件或准备同模型替代方案。

## 7. 后续变更记录（2026-11-25，阶段 1）

- 两个 Worker 已各自补充 `pyproject.toml` 与 `uv.lock`：CosyVoice 限定 `>=3.10,<3.11`，Qwen3-TTS 限定 `>=3.12,<3.13`；`uv run` 可按目录自动选择解释器。原 POC venv 仍保留，未删除。
- 阶段 1 的 Web 骨架、TXT 导入与章节闭环已落地，使用 Mock Provider；不改变本报告第 4 节的任何试听结论。
- 人工试听 gate 已于 2026-11-25 有条件关闭；关闭记录与遗留项见第 8 节。

## 8. 人工试听 gate 关闭记录（2026-11-25）

- 验收人：用户本人（项目负责人）。
- 试听对象：`candidate_01..03.wav` 与 `neutral.wav` / `angry.wav` / `sad.wav`。
- 评分：三音色差异 8/10；跨情绪一致性 neutral 7、angry 7、sad 3；中文发音与杂音 6.5/10。
- 判定：**有条件通过并关闭试听 gate**。理由：
  1. 至少 3 个虚构声音可明确区分；
  2. 同一角色 neutral / angry / sad 仍可辨认为同一声音；
  3. CUDA 分时加载未出现持续 OOM；
  4. 环境、模型 ID、运行命令已在本报告记录。
- 限制：
  1. sad 表现力不足（用户评分 3/10），暂不作为可交付音质；
  2. 句尾存在气声（发音与杂音 6.5/10）；
  3. 本次 `inference_instruct2` 未消费参考文本，参考 WAV + 参考文本的完整链路仍未验证；
  4. 第 5 节许可证核对未完成。
- 结论边界：**关闭的是阶段 0 试听 gate，不等同于阶段 0 全部签收，也不等同于音质验收通过。**
- 后续：阶段 3 首先执行音质修复 spike，退出标准见 `docs/reports/STAGE3_AUDIO_QUALITY_PLAN.md`；未达到门槛前不进入批量生成与导出。

## 9. 阶段 3 音质 spike 后续记录（2026-11-25）

- 已执行阶段 3 音质修复 spike，报告见 `docs/reports/STAGE3_AUDIO_QUALITY_REPORT.md`。本节不修改第 4.1、8 节的阶段 0 听感结论。
- CosyVoice 文本前端已通过 `wetext==0.0.7` 内置 FST 修复，不再依赖 ModelScope 的 FST 下载；日志已出现 `use wetext frontend`。
- 已重新生成 9 条中性参考音候选，并完成 22 句评估集、核心情绪、种子、`instruct2` / `zero_shot`、空/错误参考文本、分句拼接与后处理的实验矩阵。
- 技术实验记录：45 条生成记录（44 有效、1 失败）、1 条派生记录；失败项为空参考文本 `zero_shot` 的 40ms 静音输出。
- 当前新增阻塞：RTF 中位数 1.617，未达到计划中的 < 1；年轻女性 angry 存在客观音色漂移。
- 用户已于 2026-11-25 完成阶段 3 试听评分，结论为 **gate 不通过**：跨情绪一致性 1/10、sad 表现力 1/10、尾音气声明显、老年男声拖慢；记录见 `docs/reports/STAGE3_LISTENING_RESULT.md`。这不改变第 4.1、8 节阶段 0 的“有条件通过”结论。
- 因此阶段 3 的声音库、单段 WAV、音色绑定和版本管理尚未开始；阶段 0 第 5 节许可证核对仍未完成。
- 复现命令、样本路径和退出标准对照见阶段 3 报告第 2、8、11 节。

## 10. Ollama 模型目录迁移（2026-11-26）

- 已将 Ollama 模型从项目缓存目录 `%USERPROFILE%\Documents\NovelVoiceStudio\cache\ollama` 迁回 Ollama 默认目录 `%USERPROFILE%\.ollama\models`；源目录已删除。
- 迁移后 `ollama list` 可见 `modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest`；`ollama show` 显示 4.2B、Q4_K_M、262144 上下文与 Apache-2.0 元数据。
- 本机未设置 `OLLAMA_MODELS`；项目数据目录不再保存 Ollama 模型，应用仍通过 `127.0.0.1:11434` 访问。
- 模型清单、ModelScope 链接、下载与使用方式已集中到 [MODELS.md](../MODELS.md)。
