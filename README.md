# NovelVoice Studio

本地优先的 AI 多角色有声小说制作系统。当前发布版本 **0.4.0**，已完成 **阶段 1：骨架与 TXT 最小闭环**、**阶段 2：AI 小说分析与审核**、**阶段 3：固定音色与单段语音生成** 的应用链路；v0.5.0 AI 生产链重构 P1–P7 已实现，等待完整流程评审与验收。批量生成、连续播放与 M4B 导出仍未实现。

## 当前可用范围

- TXT 导入：GB18030 / UTF-8 等编码识别与低置信度确认、章节正则解析、预览、去重。
- 书库：书籍列表、详情、软删除/恢复、搜索。
- 章节工作区：正文渲染、重命名、拆分、合并、段落级操作入口。
- AI 分析（阶段 2）：本机 Ollama 结构化输出，提取角色、别名、关系、章节摘要与逐段说话人/情绪/演绎建议。
- 全书角色圣经（v0.5.0 P2）：章节摘要汇总、标准角色/别名、关系、人物弧、非线性剧情时间线与异常收件箱；支持选定章节重跑。
- 逐章分场与角色状态（v0.5.0 P3）：按角色圣经和剧情时间切分场景，记录地点、时间、视角、气氛、原文范围与出场角色状态；窗口覆盖校验、确定性回退、版本与取消恢复。
- 有声剧本（v0.5.0 P4）：基于场景与角色状态生成轻改编剧本单元，区分旁白、对白、内心、音效、环境与停顿；原文精确对齐、未覆盖部分确定性补齐、版本回退与开放疑点。
- 全局配音员库与自动选角（v0.5.0 P5）：跨书复用/收藏/试听配音员；按角色性别、年龄、标签与使用情况自动选角，缺声线时调用本机 VoiceDesign；支持旁白、角色基础声线与角色阶段声线，结果同步到剧本单元；失败与低置信匹配进入异常收件箱。
- 制作台与异常收件箱（v0.5.0 P6）：六页签制作台（方案/设定集/分场/剧本/选角/异常）、逐章推进与下一步建议；全局配音员库与跨书异常收件箱；支持接受、批量接受与填写原因后忽略。
- 选中单元音频与试听（v0.5.0 P7）：剧本页可按单元生成旁白/对白/内心独白 WAV，并在浏览器内直接试听；按单元保留 `vNNN` 版本并支持切回；文本、表演或声线变化自动标记待重新生成；音效、环境与停顿 cue 不进入 TTS；缺声线、参考音缺失或生成失败进入异常收件箱。当前为同步单单元生成，批量队列仍待 0.6.0。
- 审核工作区：片段筛选与批量修改、角色资料编辑/锁定/合并/拆分、别名候选确认、低置信度与回退片段强制复核。
- 全书分析任务：进度、取消、失败重试、启动时中断恢复；用户编辑字段在重跑时保留。
- 声音库（阶段 3）：按书为旁白/角色生成多个音色候选，试听、绑定、切换、删除未使用候选；参考音在绑定时自动去齿。
- 配音工作台（阶段 3）：按片段生成 WAV，逐条试听，保留历史版本并回退；未识别说话人或未绑定音色的片段会明确阻止生成。
- 引擎服务目录：`workers/` 下的 Qwen3-TTS VoiceDesign（8766）+ CosyVoice3 Voice Conversion（8767），独立 venv、只监听回环地址，由统一控制器启停。
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
- 阶段 3 真实语音：可用的 NVIDIA GPU、两个独立引擎服务 venv、ModelScope 模型缓存
- 可选：FFmpeg / ffprobe（阶段 4 音频导出；阶段 3 的 WAV 试听不需要）

Mock 模式不需要 GPU、不需要下载大模型，也不会自动安装引擎服务环境。

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

应用**不会自动下载模型，也不会自动拉起引擎服务**。`workers/` 是正式的引擎服务目录，服务清单、协议和新增服务步骤见 [workers/README.md](workers/README.md)。首次使用按以下顺序准备（会占用约 14 GB 磁盘，需你明确同意）：

```powershell
# 1. 从 ModelScope 国内源下载两阶段模型（显式运行，不自动触发）
pwsh.exe -File scripts/download_models.ps1

# 2. 准备两个独立引擎服务环境（不要装进核心 .venv）
cd workers/qwen_tts_worker
uv sync
cd ../cosyvoice_worker
uv sync
cd ../..

# 3. 启动引擎服务（模型默认按需加载，首次生成会明显变慢）
uv run novelvoice-studio worker start
# 可选：启动时即加载模型
uv run novelvoice-studio worker start --preload

# 4. 以真实模式启动应用
$env:NOVELVOICE_PROVIDERS__MODE = "real"
pwsh.exe -File scripts/start.ps1

# 5. 停止引擎服务（只处理本项目记录且身份可核实的进程）
uv run novelvoice-studio worker stop
```

也可以继续使用兼容入口 `scripts/start_workers.ps1 [-Preload]` 与 `scripts/stop_workers.ps1`；它们只转交参数，实际逻辑统一在 `src/novelvoice_studio/engine_services.py`。日常检查用 `uv run novelvoice-studio worker status`，日志用 `uv run novelvoice-studio worker logs`。

CosyVoice 引擎服务依赖 `workers/cosyvoice_worker/CosyVoice` 源码目录与模型缓存，准备方式见 [阶段 0 POC 报告](docs/reports/POC_REPORT.md)。各模型的 ID、ModelScope 链接与使用方式集中在 [模型清单](docs/MODELS.md)。真实模式要求 `voice_design.worker_url` 与 `tts.worker_url` 指向 `127.0.0.1` / `localhost` / `::1`，否则拒绝启动。

## 使用流程

v0.5.0 制作台（新书默认入口）：

1. 打开「书库」→「导入 TXT」，确认编码与章节结构。导入完成后直接进入制作台。
2. 在「方案」查看下一步；先运行「设定集」整理角色、别名、人物弧与剧情时间线。
3. 逐章运行「分场」，检查场景、原文范围与角色状态。
4. 逐章运行「剧本」，检查旁白/对白/内心/音效/环境/停顿单元与情绪、表演提示。
5. 在「选角」自动或手动绑定旁白与角色；需要新声音时先在「声音库」创建全局配音员。
6. 在「异常」处理阻断、警告与提示；也可从左侧「异常收件箱」跨书集中处理。
7. 在「剧本」为带声线的旁白/对白/内心单元点击「生成音频」，生成后可在单元内直接试听；「版本」查看 `vNNN` 历史并切回旧版本。
8. 文本、表演或声线变化后单元会标记「待重新生成」，旧音频仍可试听；失败与缺声线异常可在制作台或收件箱处理。批量生成与连续播放尚未实现。

0.4.0 书籍工作区（保留）：

1. 制作台右上角「原文与校对」仍可进入 `/books/:bookId`，用于正文校对、章节拆分/合并与旧片段审核。
2. 旧「配音」页签与按书音色候选仍保留在 `/books/:bookId/voices`，供已有 0.4.0 项目继续使用；新项目请使用制作台与全局配音员库。
3. 「任务队列」仍是阶段 4 占位页。

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
NOVELVOICE_PROVIDERS__MODE=real          # 本机 Ollama + 本机引擎服务
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
- 两个语音引擎服务同理，只接受本机回环地址。
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
  voice_actors/   # 全局配音员参考音：<actor_id>/reference[_deessed].wav
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

当前基线（2026-11-26）：后端 **157 passed**，前端 **34 passed / 7 files**，`vue-tsc + vite build` 通过。真实 Ollama 验收详情见 [docs/reports/STAGE2_REPORT.md](docs/reports/STAGE2_REPORT.md)，阶段 3 应用验收见 [docs/reports/STAGE3_REPORT.md](docs/reports/STAGE3_REPORT.md)，v0.5.0 P2 全书角色圣经见 [P2 实施报告](docs/reports/P2_BOOK_BIBLE_REPORT_2026-11-26.md)，P3 分场与角色状态见 [P3 实施报告](docs/reports/P3_SCENES_REPORT_2026-11-26.md)，P4 有声剧本见 [P4 实施报告](docs/reports/P4_SCRIPT_REPORT_2026-11-26.md)，P5 全局配音员与自动选角见 [P5 实施报告](docs/reports/P5_CASTING_REPORT_2026-11-26.md)，P6 制作台与异常收件箱见 [P6 实施报告](docs/reports/P6_CONSOLE_REPORT_2026-11-26.md)，P7 选中单元音频见 [P7 实施报告](docs/reports/P7_UNIT_AUDIO_REPORT_2026-11-26.md)，阶段 0–3 轻度验收方式见 [验收单](docs/acceptance/STAGE0_3_LIGHT_ACCEPTANCE_2026-11-26.md)。

## 本机安全边界

- 仅监听回环地址；CORS 不使用通配符。
- `TrustedHostMiddleware` 限制 Host；响应附带 `nosniff`、`DENY`、`no-referrer` 等头。
- 上传只接受 `.txt`，限制大小；路径使用受控 ID 与目录校验，防路径穿越。
- 引擎服务只接受回环请求，只交换 Base64/二进制音频，不接受任意文件路径，不自动下载模型；停止接口只在控制器注入令牌时启用。
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
  ai/             # Provider 注册表、Ollama / Mock、Qwen/CosyVoice 引擎服务客户端与 Prompt
frontend/         # Vue 3 + Vite + Naive UI + Tailwind
migrations/       # Alembic
scripts/          # setup / start / stop / 引擎服务启停兼容入口 / 环境检查 / 模型下载
workers/          # 引擎服务目录：services.yaml + 独立 venv + 回环 HTTP；experiments/ 归档历史实验
tests/            # 后端测试与公开合成 fixture
config/           # 配置模板；实际 config.yaml 不入库
docs/
  README.md       # 文档索引
  ARCHITECTURE_NOTES.md
  MODELS.md
  acceptance/     # 阶段验收方式与验收单
  design/         # v0.5.0 生产链与数据模型设计
  requirements/   # 需求规格说明书
  reports/        # 阶段 0 POC、阶段 2/3 与 v0.5.0 P2–P7 报告
```

## 排错

- **启动后发现页面 404**：`frontend/dist/index.html` 不存在。运行 `scripts/setup.ps1` 或 `cd frontend; npm run build`。
- **端口被占用**：`start.ps1` 会输出占用 PID 与命令行，不会自动结束；确认后再处理。
- **真实模式提示引擎服务未连接**：先运行 `uv run novelvoice-studio worker status`；启动用 `uv run novelvoice-studio worker start`（或 `scripts/start_workers.ps1`），检查 `.run/workers/*.err.log` 与 8766/8767 健康接口。
- **首次设计/生成很慢**：引擎服务按需加载模型；可用 `worker start --preload`（或 `start_workers.ps1 -Preload`），但会提前占用显存。
- **引擎服务日志**：`uv run novelvoice-studio worker logs`，文件在 `.run/workers/`。
- **生成报“参考音频缺失”**：候选文件被移动或未绑定成功；在声音库重新选择或重新设计。
- **删除音色被拒绝**：该音色仍是当前绑定项或已被片段使用；先绑定其他候选。
- **长片段无法生成**：单段超过 `tts.max_text_chars`（默认 1200 字），先拆分章节/片段。
- **停止失败**：先 `scripts/stop.ps1`；若服务无响应，可 `-Force`。脚本仍会校验进程身份。
- **数据目录位置**：`scripts/server_control.py info` 可打印当前 host/port/data_dir/frontend_dist。
- **查看日志**：应用 `.run/server.log`，引擎服务 `.run/workers/`。

## 阶段边界与后续

- 阶段 1：导入 → 章节管理闭环。
- 阶段 2：本机 Ollama 分析 → 角色/片段审核闭环；低置信度与回退片段必须人工确认。
- 阶段 3：音色设计/绑定、逐段 WAV 生成、版本回退、引擎服务启停与真实/模拟切换已实现。音质实验与复听记录见 [阶段 3 音质报告](docs/reports/STAGE3_AUDIO_QUALITY_REPORT.md)、[阶段 3B](docs/reports/STAGE3B_AUDIO_REPAIR_REPORT.md)、[阶段 3C](docs/reports/STAGE3C_SIBILANCE_REPORT.md)；应用验收见 [docs/reports/STAGE3_REPORT.md](docs/reports/STAGE3_REPORT.md)。
- 阶段 4 尚未实现：批量生成与任务队列、连续播放器、按章节/全书导出 M4B、工程混音与多版本管理。
- v0.5.0 重构：P1 数据层、P2 全书角色圣经、P3 逐章分场/角色状态、P4 有声剧本、P5 全局配音员/自动选角、P6 制作台/异常收件箱与 P7 选中单元音频生成/试听已完成，等待完整流程评审与验收。详情见 [v0.5.0 AI 生产链重构](docs/design/V0_5_0_AI_PRODUCTION_CHAIN_2026-11-26.md)、[P2 实施报告](docs/reports/P2_BOOK_BIBLE_REPORT_2026-11-26.md)、[P3 实施报告](docs/reports/P3_SCENES_REPORT_2026-11-26.md)、[P4 实施报告](docs/reports/P4_SCRIPT_REPORT_2026-11-26.md)、[P5 实施报告](docs/reports/P5_CASTING_REPORT_2026-11-26.md)、[P6 实施报告](docs/reports/P6_CONSOLE_REPORT_2026-11-26.md) 与 [P7 实施报告](docs/reports/P7_UNIT_AUDIO_REPORT_2026-11-26.md)。
- 阶段 0 的听感 gate 已由用户本人于 2026-11-25 有条件通过；sad 表现力、句尾气声与参考文本条件仍是质量待办。**许可证核对（含 `ttsfrd` / `wetext`）尚未完成**，不能视为已签收。

详细架构口径见 [docs/ARCHITECTURE_NOTES.md](docs/ARCHITECTURE_NOTES.md)，阶段 2 验收记录见 [docs/reports/STAGE2_REPORT.md](docs/reports/STAGE2_REPORT.md)，POC 实测见 [docs/reports/POC_REPORT.md](docs/reports/POC_REPORT.md)，完整需求见 [需求规格说明书 v1.0](docs/requirements/NovelVoice_Studio_需求规格说明书_v1.0.md)，全部文档见 [docs/README.md](docs/README.md)。
