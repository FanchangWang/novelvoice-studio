# NovelVoice Studio

本地优先的 AI 多角色有声小说制作系统。当前版本 **0.4.0**，已完成 **阶段 1：骨架与 TXT 最小闭环**、**阶段 2：AI 小说分析与审核**、**阶段 3：固定音色与单段语音生成** 的应用链路；批量生成、连续播放与 M4B 导出属于阶段 4，尚未实现。

## 当前可用范围

- TXT 导入：GB18030 / UTF-8 等编码识别与低置信度确认、章节正则解析、预览、去重。
- 书库：书籍列表、详情、软删除/恢复、搜索。
- 章节工作区：正文渲染、重命名、拆分、合并、段落级操作入口。
- AI 分析（阶段 2）：本机 Ollama 结构化输出，提取角色、别名、关系、章节摘要与逐段说话人/情绪/演绎建议。
- 审核工作区：片段筛选与批量修改、角色资料编辑/锁定/合并/拆分、别名候选确认、低置信度与回退片段强制复核。
- 全书分析任务：进度、取消、失败重试、启动时中断恢复；用户编辑字段在重跑时保留。
- 声音库（阶段 3）：按书为旁白/角色生成多个音色候选，试听、绑定、切换、删除未使用候选；参考音在绑定时自动去齿。
- 配音工作台（阶段 3）：按片段生成 WAV，逐条试听，保留历史版本并回退；未识别说话人或未绑定音色的片段会明确阻止生成。
- 本机语音 Worker：Qwen3-TTS VoiceDesign（8766）+ CosyVoice3 Voice Conversion（8767），只监听回环地址。
- Mock Provider：不加载模型、不联网推理，返回确定性占位数据；界面明确标注「模拟模式」。
- 本机服务：FastAPI 自动迁移 SQLite、静态托管 Vue 构建产物、受控优雅停止。
- 测试：后端 pytest、前端 Vitest/TypeScript 构建。

**音质与功能是两个门槛。** 阶段 3B 复听 gate 已通过，阶段 3C 接受 `参考音先去齿、输出不处理` 的定稿链路；但“菜 / 三”等字的擦音仍可能听成“ci”，句尾气声也未完全消除，详见 [阶段 3B 复听结果](docs/reports/STAGE3B_LISTENING_RESULT.md) 与 [阶段 3C 复听结果](docs/reports/STAGE3C_LISTENING_RESULT.md)。功能可用不代表音质已验收。

## 环境要求

- Windows 10/11（当前仅按 Windows 验证）
- Python 3.12（由 uv 管理，`.python-version` 已固定）
- [uv](https://docs.astral.sh/uv/) 0.13+
- Node.js >= 20.19、npm
- 阶段 2 真实分析：本机 [Ollama](https://ollama.com/) 与 `modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest`（[ModelScope 页面](https://modelscope.cn/models/unsloth/Qwen3.5-4B-GGUF)）
- 阶段 3 真实语音：可用的 NVIDIA GPU、两个独立 Worker venv、ModelScope 模型缓存
- 可选：FFmpeg / ffprobe（阶段 4 音频导出；阶段 3 的 WAV 试听不需要）

Mock 模式不需要 GPU、不需要下载大模型，也不会自动安装 Worker 环境。

## 快速开始

在项目根目录打开 PowerShell 7：

```powershell
# 1. 安装依赖、创建 config/config.yaml 与数据目录、构建前端
pwsh.exe -File scripts/setup.ps1

# 2. 启动（后台分离进程 + 健康检查 + 打开浏览器）
pwsh.exe -File scripts/start.ps1

# 3. 停止（先请求优雅退出）
pwsh.exe -File scripts/stop.ps1

# 强制停止（仅在本项目进程校验通过后）
pwsh.exe -File scripts/stop.ps1 -Force
```

默认地址：`http://127.0.0.1:8765`，API 文档：`http://127.0.0.1:8765/api/docs`。

`start.ps1` 不会结束任何未知进程；端口被占用时只打印 PID/进程名/命令行。`stop.ps1` 只处理 `.run/server.json` 中记录的、PID + 可执行文件 + 创建时间均匹配的本项目进程。

### 启用真实语音

应用**不会自动下载模型，也不会自动拉起 Worker**。首次使用按以下顺序准备（会占用约 14 GB 磁盘，需你明确同意）：

```powershell
# 1. 从 ModelScope 国内源下载两阶段模型（显式运行，不自动触发）
pwsh.exe -File scripts/download_models.ps1

# 2. 准备两个独立 Worker 环境（不要装进核心 .venv）
cd workers/qwen_tts_worker
uv sync
cd ../cosyvoice_worker
uv sync
cd ../..

# 3. 启动两个 Worker（模型默认按需加载，首次生成会明显变慢）
pwsh.exe -File scripts/start_workers.ps1
# 可选：启动时即加载模型
pwsh.exe -File scripts/start_workers.ps1 -Preload

# 4. 以真实模式启动应用
$env:NOVELVOICE_PROVIDERS__MODE = "real"
pwsh.exe -File scripts/start.ps1

# 5. 停止 Worker（只处理本项目记录且命令行校验通过的进程）
pwsh.exe -File scripts/stop_workers.ps1
```

CosyVoice Worker 依赖 `workers/cosyvoice_worker/CosyVoice` 源码目录与模型缓存，准备方式见 [阶段 0 POC 报告](docs/reports/POC_REPORT.md)。各模型的 ID、ModelScope 链接与使用方式集中在 [模型清单](docs/MODELS.md)。真实模式要求 `voice_design.worker_url` 与 `tts.worker_url` 指向 `127.0.0.1` / `localhost` / `::1`，否则拒绝启动。

## 使用流程

1. 打开「书库」→「导入书籍」，选择 UTF-8/GBK 等 `.txt`。
2. 查看编码置信度、章节结构和正文样例；低置信度时手动选择编码。
3. 若检测到重复，选择中止、新建或替换；默认中止。
4. 导入后进入书籍工作区，检查章节；可重命名、拆分、合并。
5. 切换到「片段审核」或打开「AI 分析」抽屉，分析本章/未完成章节/全书。
6. 审核低置信度片段与角色：修改台词或说话人后字段自动锁定；重跑不会覆盖人工修改。
7. 处理别名候选、合并重复角色、拆分误合并角色；关系与摘要随章节同步更新。
8. 打开「声音库」，选择当前书籍；为旁白和每个主要角色填写音色描述并生成候选，试听后绑定一个。
9. 回到书籍工作区的「配音」页签，逐条生成语音；每条可试听、重新生成并查看/回退历史版本。
10. 修改台词、说话人或情绪后，旧音频会失效，需要重新生成。

「任务队列」仍是阶段 4 占位页。

测试小说可用 `tests/fixtures/sample_novel.txt`。

## 配置

配置优先级：**进程环境变量 > `.env` > `config/config.yaml` > 内置默认值**。

```powershell
Copy-Item config/config.example.yaml config/config.yaml
Copy-Item .env.example .env   # 可选，用于本机覆盖
```

常用环境变量：

```text
NOVELVOICE_APP__HOST=127.0.0.1
NOVELVOICE_APP__PORT=8765
NOVELVOICE_APP__DATA_DIR=D:\NovelVoiceData
NOVELVOICE_PROVIDERS__MODE=mock
NOVELVOICE_PROVIDERS__MODE=real          # 本机 Ollama + 本机语音 Worker
NOVELVOICE_LLM__MODEL=modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest

NOVELVOICE_VOICE_DESIGN__WORKER_URL=http://127.0.0.1:8766
NOVELVOICE_VOICE_DESIGN__CANDIDATES=3
NOVELVOICE_VOICE_DESIGN__SEEDS=[42,123,2026]

NOVELVOICE_TTS__WORKER_URL=http://127.0.0.1:8767
NOVELVOICE_TTS__DEFAULT_SPEED=1.0
NOVELVOICE_TTS__MAX_TEXT_CHARS=1200
NOVELVOICE_TTS__REFERENCE_DEESS__ENABLED=true
```

完整示例见 [.env.example](.env.example)。

真实分析只允许 `llm.base_url` 指向 `127.0.0.1` / `localhost` / `::1`，小说内容不会发送到云端。模型需要用户自行安装，应用不会自动下载：

```powershell
ollama pull modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest
ollama list
```

`config/config.yaml` 的 `llm.model` 必须与 Ollama 中的模型名完全一致。Ollama 模型由 Ollama 自己管理，默认放在 `%USERPROFILE%\.ollama\models`，本项目数据目录不保存 Ollama 模型，也不需要设置 `OLLAMA_MODELS`。模型清单、链接、体积与完整使用命令见 [docs/MODELS.md](docs/MODELS.md)。

- `app.host` 只接受 `127.0.0.1` / `localhost` / `::1`，填写其他地址会拒绝启动。
- 两个语音 Worker 同理，只接受本机回环地址。
- `NOVELVOICE_CONTROL_TOKEN` 由 `scripts/server_control.py` 在启动时随机生成并只注入子进程，不要手工固定或提交。
- `config/config.yaml`、`.env`、`.run/` 均已被 git 忽略。
- 参考音去齿参数（4–10kHz、`threshold_ratio=0.24`、`full_ratio=0.62`、最多 `-5dB`）是阶段 3C 的定稿值；仅在绑定音色时执行，生成阶段不重复处理。

## 数据目录与备份

默认数据目录：`%USERPROFILE%\Documents\NovelVoiceStudio`

```text
NovelVoiceStudio/
  app.db          # SQLite 元数据（WAL 模式）
  books/
    book_xxx/
      source/     # 导入的原文
      parsed/     # 解析中间文件
      voices/     # 各 owner 的音色候选与去齿后的参考音
      audio/      # 片段音频：<chapter>/<segment>/vNNN.wav
  backups/        # 自动迁移前备份
  cache/          # ModelScope 语音模型缓存（不含 Ollama 模型）
  logs/           # 预留
  trash/          # 软删除文件
```

- 音频二进制**不存入 SQLite**，只保存元数据、路径、校验和与生成参数。
- 每个片段的音频版本只增不减；回退是切换 `current_audio_asset_id`，不会删除旧 WAV。
- 迁移前会自动备份数据库；升级大版本前仍建议整个目录冷备份。
- 数据库使用 UTC ISO-8601 时间，前端转换为本地时区显示。

## 开发模式

```powershell
# 终端 1：后端（开发时可不加控制令牌）
uv run novelvoice-studio

# 终端 2：前端 Vite（/api 代理到 127.0.0.1:8765）
cd frontend
npm run dev
```

开发地址：`http://127.0.0.1:5173`。后端 CORS 仅放行本机 8765 / 5173 / 4173 端口。

## 测试与检查

```powershell
uv run pytest
cd frontend
npm test
npm run build
pwsh.exe -File ../scripts/check_environment.ps1
```

当前基线（2026-11-26）：后端 **102 passed**，前端 **33 passed / 7 files**，`vue-tsc + vite build` 通过。真实 Ollama 验收详情见 [docs/reports/STAGE2_REPORT.md](docs/reports/STAGE2_REPORT.md)，阶段 3 应用验收见 [docs/reports/STAGE3_REPORT.md](docs/reports/STAGE3_REPORT.md)。

## 本机安全边界

- 仅监听回环地址；CORS 不使用通配符。
- `TrustedHostMiddleware` 限制 Host；响应附带 `nosniff`、`DENY`、`no-referrer` 等头。
- 上传只接受 `.txt`，限制大小；路径使用受控 ID 与目录校验，防路径穿越。
- 语音 Worker 只接受回环请求，只交换 Base64/二进制音频，不接受任意文件路径，不自动下载模型。
- 不调用付费云 AI、无遥测。
- 隐藏停机接口仅在启动器注入随机令牌时存在，需 `X-NVS-Control` 头匹配；不进入 OpenAPI 文档。

## 目录结构

```text
src/novelvoice_studio/
  api/            # FastAPI 路由、上传限制、静态托管
  services/       # 导入、章节解析/操作、分析管道、片段操作、音色与音频服务、路径安全
  db/             # Engine、迁移、会话
  models/         # SQLAlchemy 模型
  schemas/        # Pydantic 契约
  ai/             # Provider 注册表、Ollama / Mock、Qwen/CosyVoice Worker 客户端与 Prompt
frontend/         # Vue 3 + Vite + Naive UI + Tailwind
migrations/       # Alembic
scripts/          # setup / start / stop / Worker 启停 / 环境检查 / 模型下载
workers/          # 阶段 2/3 的独立 Worker（不使用核心 venv）
tests/            # 后端测试与公开合成 fixture
config/           # 配置模板；实际 config.yaml 不入库
docs/
  README.md       # 文档索引
  ARCHITECTURE_NOTES.md
  MODELS.md
  requirements/   # 需求规格说明书
  reports/        # 阶段 0 POC 与阶段 2/3 报告
```

## 排错

- **启动后发现页面 404**：`frontend/dist/index.html` 不存在。运行 `scripts/setup.ps1` 或 `cd frontend; npm run build`。
- **端口被占用**：`start.ps1` 会输出占用 PID 与命令行，不会自动结束；确认后再处理。
- **真实模式提示 Worker 未连接**：先运行 `scripts/start_workers.ps1`，检查 `.run/workers/*.err.log` 与 8766/8767 健康接口。
- **首次设计/生成很慢**：Worker 按需加载模型；可用 `start_workers.ps1 -Preload`，但会提前占用显存。
- **生成报“参考音频缺失”**：候选文件被移动或未绑定成功；在声音库重新选择或重新设计。
- **删除音色被拒绝**：该音色仍是当前绑定项或已被片段使用；先绑定其他候选。
- **长片段无法生成**：单段超过 `tts.max_text_chars`（默认 1200 字），先拆分章节/片段。
- **停止失败**：先 `scripts/stop.ps1`；若服务无响应，可 `-Force`。脚本仍会校验进程身份。
- **数据目录位置**：`scripts/server_control.py info` 可打印当前 host/port/data_dir/frontend_dist。
- **查看日志**：应用 `.run/server.log`，Worker `.run/workers/`。

## 阶段边界与后续

- 阶段 1：导入 → 章节管理闭环。
- 阶段 2：本机 Ollama 分析 → 角色/片段审核闭环；低置信度与回退片段必须人工确认。
- 阶段 3：音色设计/绑定、逐段 WAV 生成、版本回退、Worker 启停与真实/模拟切换已实现。音质实验与复听记录见 [阶段 3 音质报告](docs/reports/STAGE3_AUDIO_QUALITY_REPORT.md)、[阶段 3B](docs/reports/STAGE3B_AUDIO_REPAIR_REPORT.md)、[阶段 3C](docs/reports/STAGE3C_SIBILANCE_REPORT.md)；应用验收见 [docs/reports/STAGE3_REPORT.md](docs/reports/STAGE3_REPORT.md)。
- 阶段 4 尚未实现：批量生成与任务队列、连续播放器、按章节/全书导出 M4B、工程混音与多版本管理。
- 阶段 0 的听感 gate 已由用户本人于 2026-11-25 有条件通过；sad 表现力、句尾气声与参考文本条件仍是质量待办。**许可证核对（含 `ttsfrd` / `wetext`）尚未完成**，不能视为已签收。

详细架构口径见 [docs/ARCHITECTURE_NOTES.md](docs/ARCHITECTURE_NOTES.md)，阶段 2 验收记录见 [docs/reports/STAGE2_REPORT.md](docs/reports/STAGE2_REPORT.md)，POC 实测见 [docs/reports/POC_REPORT.md](docs/reports/POC_REPORT.md)，完整需求见 [需求规格说明书 v1.0](docs/requirements/NovelVoice_Studio_需求规格说明书_v1.0.md)，全部文档见 [docs/README.md](docs/README.md)。