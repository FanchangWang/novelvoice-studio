**本地 AI 多角色有声小说制作系统 - 产品需求与技术规格说明书**

**文档版本：** 1.0  
**编制日期：** 2026-11-25  
**产品形态：** Windows 11 本地 Web 应用（Local-first Web App）  
**文档用途：** 作为本地 AI 编程助手的项目实现依据  
**项目代号：** NovelVoice Studio

---

## 0. 给本地 AI 编程助手的执行说明

本文件是本项目的产品需求、技术约束和验收基准。开始编写代码前，必须完整阅读本文件。

### 0.1 实施原则

1. **按阶段实现，不要一次性生成整个项目。** 每阶段结束必须运行测试、修复问题并更新 README/开发日志。
2. **先完成可运行的端到端最小闭环。** 优先实现 TXT 导入 -> 生成结构化配音脚本 -> 手动修订 -> 生成一个 WAV -> 播放。闭环稳定后再扩展整本书处理。
3. **严格遵守本文件确定的技术方向。** 第一版是 Python 本地 Web 应用，不使用 Wails、Go、Electron、云端数据库或付费 AI API。
4. **模型必须通过 Adapter/Provider 接口接入。** 业务层不得直接依赖某个模型的 Python 类、参数或返回对象。允许替换 LLM、TTS 和音频处理实现，而不重写业务逻辑。
5. **模型版本不得写死在业务代码中。** 模型 ID、模型目录、设备、精度、端口、上下文长度等通过配置文件或环境变量管理。
6. **不能假装 AI 结果必然正确。** 说话人、角色别名、性别/年龄和情绪均需有置信度或证据；不确定时进入待审核状态，不得静默猜测。
7. **后台生成任务不能依赖浏览器页面存活。** 关闭标签页、刷新页面或浏览器崩溃，不得取消已经提交的生成任务。
8. **不要让多个重量级模型默认同时占用 GPU。** 按任务类型进行显存调度，初始 GPU 并发数为 1。
9. **不自动下载大型模型。** 首次运行显示模型安装/下载说明和空间预估，由用户明确点击后再下载。已经下载的模型可离线使用。
10. **不要把完整音频二进制存进 SQLite。** 数据库只保存元数据和文件相对路径；音频保存在项目文件夹。
11. **任何重要操作都应可恢复。** 导入、分析、音频生成和导出要有任务状态、日志和错误信息；生成失败不应损坏已有文件。
12. **不允许只交付静态页面或假数据。** 每个已完成阶段必须具备真实后端 API 和最小自动化测试。开发初期可使用 Mock Provider，但 UI 必须明确显示“模拟模式”。

### 0.2 第一轮工作要求

阅读完成后，先输出：

- 需求理解摘要；
- 推荐代码目录结构；
- 分阶段实施计划；
- 已识别的技术风险；
- 第一阶段要创建的文件和测试。

然后只实施“阶段 0：项目骨架与最小闭环”，不要跳到所有 AI 功能。若遇到硬件、模型 API 或依赖兼容性不确定，先建立可运行的 POC 和验证记录，不要伪造成功结果。

### 0.3 需求冲突处理优先级

优先级依次为：

1. 本文件中的明确要求；
2. 用户后续明确提出的修改；
3. 本地 AI 编程助手的通用最佳实践。

若某个细节未定义，优先采用简单、可测试、可替换的实现，并在 README 中记录假设。只有涉及不可逆数据变更、明显增加成本或安全边界时才暂停并询问。

---

# 1. 产品概述

## 1.1 产品定位

NovelVoice Studio 是一款运行在用户 Windows 11 电脑上的本地 AI 多角色有声小说制作工具。用户导入小说文本后，系统利用本地大语言模型分析小说结构、角色、对话归属和情绪，为旁白及每个角色设计独立声音，之后按段落生成独立音频文件，并支持编辑、重试、续播和导出带章节导航的 M4B 有声书。

软件以浏览器作为界面，以本地 Python 服务作为后端。所有小说文本、人物资料、音色档案、生成 WAV 和导出文件默认保存在本机。除模型首次下载和用户主动检查更新外，核心功能不依赖互联网服务。

## 1.2 目标用户与使用环境

- 单机个人用户，主要处理中文长篇小说。
- 操作系统：Windows 11 64 位。
- 浏览器：Microsoft Edge 或 Google Chrome 最新稳定版。
- 目标硬件：Intel Core i7-14700K、64GB RAM、NVIDIA GeForce RTX 4070 Ti。按普通 RTX 4070 Ti **12GB 显存**规划；如实际为 4070 Ti SUPER 16GB，可调整模型调度参数。
- 使用模式：本机浏览器访问 `http://127.0.0.1:8765`。
- 核心目标：不购买云端 AI API，不租 GPU 服务器；优先使用可本地运行的开放模型。

## 1.3 产品目标

1. 将 TXT 小说解析为章节、段落和可编辑的语音片段。
2. 自动识别人物、别名、性别、年龄范围、人物关系及人物特点。
3. 自动识别每段文字属于旁白还是角色对话，并推断说话角色。
4. 自动识别情绪和表达方式，同时允许用户手动修正。
5. 旁白使用独立、固定的声音，不与角色音色混用。
6. 通过 AI 根据角色设定生成固定角色音色；每个角色可以生成多个候选声音并试听选择。
7. 相同角色在不同台词中保持同一声音身份，并允许每段使用不同情绪。
8. 每个语音片段单独生成 WAV，文本、角色、情绪、音色与音频一一对应。
9. 生成队列支持暂停、继续、失败重试、单段重生成和程序重启后的恢复。
10. 支持任意段落播放、章节连续播放、记忆播放位置。
11. 将章节/整本小说合并并导出带章节导航的 M4B。

## 1.4 非目标（第一版不做）

- 云端账号体系、多人协作、在线同步。
- 付费云端大模型或付费 TTS API。
- 用户声音克隆训练、模型微调或 LoRA 训练。
- 背景音乐自动编排、环境音效和复杂混音工程。
- 视频生成、字幕视频或有声书封面 AI 绘图。
- PDF 扫描件 OCR。
- 逐字级强制对齐、卡拉 OK 字幕时间轴和专业 DAW 波形编辑。
- Android、iOS 客户端。
- 远程公网访问。

---

# 2. 用户已确认的产品决策

以下为已确认要求，开发时不得自行改成其他默认方案：

| 决策项 | 已确认方案 |
|---|---|
| 应用形态 | Python 本地 Web 应用，不先做桌面端 |
| 旁白 | 使用单独旁白音色，和角色声音分离 |
| 情绪 | AI 自动判断；用户可以逐段手动修改 |
| 角色声音来源 | AI 根据角色人物设定生成声音，不要求用户提供真人录音 |
| 内部音频 | 每个片段单独保存 WAV |
| 最终导出 | 合并生成带章节导航的 M4B |
| 处理方式 | 结构化分段后逐段生成，不把整本小说一次性交给 TTS |
| 数据存储 | SQLite 保存元数据；JSON 保存配置；音频作为独立文件保存 |
| 网络依赖 | 核心流程本地运行；不调用付费云端 AI API |

---

# 3. 技术架构

## 3.1 总体架构

架构按职责分为以下几层：

- **浏览器层：** Edge/Chrome 访问 Vue 3 前端。
- **API 层：** FastAPI 监听 `127.0.0.1:8765`，提供 REST API 和 WebSocket 事件。
- **业务层：** 管理项目、章节、角色、声音档案、语音片段和导出。
- **数据层：** SQLite + Alembic，音频与原始小说以文件形式保存在本机项目目录。
- **任务层：** SQLite 持久化队列 + GPU 模型调度器，初始 GPU 并发数为 1。
- **LLM 层：** Ollama 本机 API，通过 LLM Adapter 调用 Qwen 系列模型。
- **Voice Design 层：** Qwen3-TTS VoiceDesign 候选，通过独立 Provider 生成参考声音。
- **TTS 层：** CosyVoice 3 候选，通过独立 Provider 生成逐段语音；允许替换其他兼容模型。
- **音频层：** WAV 读写、FFmpeg/ffprobe 合并与校验 M4B。

## 3.2 技术选型

| 层级 | 首选技术 | 说明 |
|---|---|---|
| 前端框架 | Vue 3 + TypeScript + Vite | 单页应用 |
| UI | Naive UI + Tailwind CSS | 桌面尺寸优先，浅色/深色主题 |
| 路由 | Vue Router | 书库、书籍工作区、声音库、设置 |
| 状态管理 | Pinia | 当前项目、选中片段、播放状态、任务状态 |
| 后端 | Python + FastAPI | 本地 REST API 和 WebSocket |
| 数据访问 | SQLAlchemy 2.x | ORM 与事务 |
| 数据迁移 | Alembic | SQLite schema 版本管理 |
| 数据库 | SQLite | 单用户本地项目数据 |
| LLM 服务 | Ollama 本机服务 | 通过 HTTP API 调用，不把模型代码嵌入业务层 |
| LLM 候选 | Qwen3.5 4B / 9B 量化版 | 先从 4B 量化模型验证，9B 作为质量选项；模型名可配置 |
| 音色设计候选 | Qwen3-TTS 1.7B VoiceDesign | 根据自然语言描述生成角色参考语音 |
| 主 TTS 候选 | Fun-CosyVoice3-0.5B-2512 | 参考音色合成、情绪/语速/音量等指令；必须先做硬件与声音一致性 POC |
| 音频读写 | soundfile / librosa（按需） | WAV 读写、时长和采样信息 |
| 音频封装 | FFmpeg + ffprobe | 音频拼接、AAC 编码、章节元数据与校验 |
| Python 依赖管理 | uv 或 venv；模型环境隔离 | 核心后端与模型运行时隔离 |
| 自动化测试 | pytest + Vitest | 后端与前端测试 |

### 3.2.1 模型版本与兼容性说明

模型属于可替换依赖，不是业务逻辑的一部分。建议初始验证组合：

- LLM：Ollama 中的 `qwen3.5:4b` 量化版先验证；需要更高分析质量时测试 `qwen3.5:9b` Q4 量化版。9B 模型文件大小不等于实际 GPU 显存需求，实际占用还包括 KV Cache、上下文和运行时开销。
- Voice Design：`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`。
- TTS：`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`。
- 语音模型的跨模型音色迁移必须经过 POC：先用 Qwen3-TTS 生成参考语音，再把该语音及其参考文本交给 CosyVoice 3，验证音色稳定性、中文发音和情绪变化。**不可在未实测前宣称音色完全一致。**
- 若跨模型组合质量不达标，应允许替换为单一模型的声音设计/声音克隆/合成组合，或由 TTS Provider 实现替代方案。业务数据结构不得依赖某模型独有参数。
- Qwen3-TTS 官方示例环境与 CosyVoice 官方安装环境的 Python 版本要求不同（当前官方说明分别建议 Python 3.12 与 Python 3.10），因此必须使用独立 Python 环境或独立 Worker 进程，禁止把二者强行塞进同一个 Python 环境。
- 先在 Windows 原生环境验证；如特定依赖无法在 Windows 稳定安装，再将对应 TTS Worker 单独放入 WSL2。FastAPI 主服务和 Web 前端仍可运行在 Windows。不得把 WSL2 设为硬性前提。
- Windows Ollama 支持 NVIDIA GPU；模型可通过本机 HTTP API 调用，并可按需卸载。GPU 调度层应利用模型加载/卸载能力减少显存冲突。

### 3.2.2 GPU 与内存策略

目标硬件按 RTX 4070 Ti 12GB VRAM 设计：

1. GPU 重任务默认串行执行，初始 `max_gpu_concurrency = 1`。
2. 不同时运行 LLM 分析和 TTS 合成。
3. 任务队列按类别切换模型：LLM 分析 -> 卸载 LLM -> 音色设计 -> 卸载 VoiceDesign -> TTS 批量生成。
4. Ollama 支持通过 `keep_alive=0` 请求卸载模型；TTS Worker 应提供显式 `unload`，若框架无法安全卸载则通过结束/重启 Worker 进程释放显存。
5. GPU 调度必须有互斥锁，避免两个请求同时加载模型。
6. 模型加载失败、CUDA OOM 或驱动异常时，任务标记失败并记录错误，不允许服务整体崩溃。
7. 设置页展示 GPU 名称、显存总量/使用量（可读取时）、当前占用模型和任务状态；读取失败时显示“不可用”，不可伪造数值。
8. 允许用户设置 LLM 量化模型、上下文长度和 TTS 模型路径；默认配置以稳定为优先，不以最大上下文为目标。

## 3.3 运行模式与启动方式

- 开发模式：前端 Vite 开发服务器；FastAPI 独立运行。
- 使用模式：FastAPI 提供 API，并托管构建后的前端静态文件；用户访问 `http://127.0.0.1:8765`。
- 提供 `start.ps1`：检查依赖、启动后端、等待健康检查通过后打开默认浏览器。
- 提供 `stop.ps1`：优雅停止后端服务；不能强制结束无关 Python 进程。
- 关闭浏览器不得停止任务。后端服务仍运行时，队列持续执行。
- 电脑关机或后端进程退出时无法继续实时推理，但必须保存任务状态；下次启动后恢复未完成任务。
- 第一版不做 Windows 服务、系统托盘或开机自启；可以后续增加。

## 3.4 推荐代码目录

```text
NovelVoiceStudio/
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── assets/
│   │   ├── components/
│   │   ├── layouts/
│   │   ├── router/
│   │   ├── stores/
│   │   ├── types/
│   │   └── views/
│   │       ├── LibraryView.vue
│   │       ├── BookWorkspaceView.vue
│   │       ├── CharactersView.vue
│   │       ├── VoicesView.vue
│   │       ├── StudioView.vue
│   │       ├── PlayerView.vue
│   │       └── SettingsView.vue
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   ├── core/
│   │   ├── db/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── repositories/
│   │   ├── services/
│   │   ├── ai/
│   │   │   ├── llm/
│   │   │   ├── voice_design/
│   │   │   └── tts/
│   │   ├── jobs/
│   │   ├── audio/
│   │   └── exporter/
│   ├── migrations/
│   └── tests/
├── workers/
│   ├── qwen_tts_worker/
│   └── cosyvoice_worker/
├── scripts/
│   ├── setup.ps1
│   ├── start.ps1
│   ├── stop.ps1
│   ├── check_environment.ps1
│   └── download_models.ps1
├── config/
│   └── config.example.yaml
├── docs/
├── .env.example
├── pyproject.toml
├── package-lock.json
└── README.md
```

目录结构可以在实现时微调，但必须保持前后端、业务服务、模型 Adapter、后台任务和音频处理职责清晰。

---

# 4. 用户角色与核心使用流程

## 4.1 用户角色

第一版只有一种用户：本机所有者（Local User）。无需账号、登录和权限系统，但需保护本地文件访问边界。

## 4.2 端到端流程

1. 用户启动本地服务并打开浏览器。
2. 创建有声书项目，导入 TXT 小说。
3. 系统识别编码、解析章节、保留原始文本。
4. 用户启动 AI 分析；系统提取人物、别名、人物画像和关系。
5. 系统按章节分析段落，识别旁白、对话、说话人、情绪和演绎方式。
6. 用户在审核界面修正不确定的角色和情绪。
7. 系统为旁白和角色生成多个音色候选。
8. 用户试听并选择固定声音，将声音绑定到旁白或角色。
9. 用户选择章节/范围，启动 WAV 批量生成。
10. 任务队列逐段合成，记录进度与失败原因。
11. 用户在播放器中从任意片段播放，继续编辑和重生成。
12. 用户选择章节或整本书，导出带章节导航的 M4B。

## 4.3 核心设计原则

- 原始小说文本永远保留，不因分段、编辑和分析被覆盖。
- AI 分析文本与用户最终朗读文本分开保存。
- 每段片段的文本、说话人、情绪、声音档案和 WAV 必须能追溯。
- 旁白是系统级特殊说话人 `narrator`，不计入小说人物。
- 角色音色与情绪分离：音色表达“是谁在说”，情绪表达“这句话怎么说”。
- 用户手动修改优先于 AI 自动结果。
- 修改一段只使该段及必要的导出结果失效，不影响其他片段音频。

---

# 5. 功能需求

优先级定义：**P0 = MVP 必须有；P1 = MVP 后续小版本；P2 = 远期增强。**

## 5.1 项目书库与导入

### FR-BOOK-001：创建项目（P0）

用户可以创建小说项目，字段包括：书名、作者（可空）、简介（可空）、原始语言（默认中文）、项目目录、创建时间、更新时间。

### FR-BOOK-002：导入 TXT（P0）

- 支持 `.txt`。
- 自动检测 UTF-8、UTF-8 BOM、GB18030/GBK 等常见中文编码。
- 检测不确定时允许用户在预览界面手动选择编码。
- 保存原始文件副本，不修改用户源文件。
- 导入后显示文件名、编码、字符数量、章节数量和解析警告。
- 同一文件重复导入时提示是否作为新项目或替换原文；禁止静默覆盖已有项目。

### FR-BOOK-003：章节识别（P0）

- 支持常见章节标题格式：第 X 章、卷 X、序章、楔子、Chapter X，以及用户自定义正则规则。
- 如果无法识别章节，整本文本作为一个默认章节，不得导入失败。
- 用户可以手动新建、重命名、拆分、合并章节和调整顺序。
- 章节顺序以用户确认的顺序为准。

### FR-BOOK-004：段落解析（P0）

- 保留原文段落顺序和原始文本。
- 清理明显多余空白时必须保留可还原的原始文件。
- 不得删除正文内容或自动改写小说。
- 记录章节及原文段落序号，便于追溯。

### FR-BOOK-005：其他格式导入（P1）

- EPUB：提取正文与章节结构。
- DOCX：提取段落和标题层级。
- Markdown：提取标题、段落和引用正文。
- PDF/OCR 不属于当前版本。

## 5.2 AI 小说分析与角色管理

### FR-ANALYSIS-001：全书角色提取（P0）

识别有明确叙事作用的主要人物和重要配角。避免把每个临时路人、群体称谓或普通名词都创建成角色。

每个角色至少包含：

- `character_id`：稳定 ID。
- 标准姓名。
- 别名/称谓列表。
- 性别：male / female / unknown。
- 年龄段：child / teenager / young_adult / adult / middle_aged / elderly / unknown。
- 人物类型：human / animal / non_human / unknown。
- 人物身份/职业。
- 性格特征。
- 与其他角色的关系。
- 声音设计描述。
- 分析置信度。
- 依据类型：explicit（原文明确）、inferred（上下文推断）、unknown（未知）。
- 首次出现章节与原文证据位置。

禁止仅凭姓名刻板印象推断性别或年龄。证据不足时使用 unknown。

### FR-ANALYSIS-002：别名归并（P0）

- 识别全名、简称、称谓、绰号等可能指向同一人物的名称。
- 自动合并必须记录合并依据与置信度。
- 低置信度时进入人工确认列表。
- 用户可手动合并人物，也可拆分错误合并的人物。
- 合并/拆分后必须更新相关片段的候选 speaker 引用，且在修改前提供确认。

### FR-ANALYSIS-003：逐章分析（P0）

不能把整本长篇小说一次性塞入 LLM。以章节或可控文本窗口为单位，携带：

- 全书已确认角色注册表；
- 相关别名；
- 最近章节摘要；
- 本章前后文窗口；
- 当前待分析段落。

分析结果写入数据库，支持按章节重跑。重跑时不得静默覆盖用户已确认的人工修改。

### FR-ANALYSIS-004：对话与说话人识别（P0）

每段识别类型：

- `narration`：叙述旁白；
- `dialogue`：直接对话；
- `inner_monologue`：内心独白；
- `unknown`：无法判断。

规则：

- 普通叙述默认分配给 `narrator`。
- 直接对话尽可能识别真实说话角色。
- 内心独白默认使用旁白；用户可改成角色声音。
- 多人对话需按发言轮次拆分。
- 说话人无法可靠判断时使用 `unknown`，状态设为 `needs_review`，不能随意猜角色。
- 必须记录 `speaker_confidence` 和简单证据/理由。

### FR-ANALYSIS-005：情绪识别（P0）

每段可以有一个主情绪标签，默认值 `neutral`。第一版情绪枚举：

- neutral：平静/正常；
- happy：开心；
- excited：兴奋；
- angry：愤怒；
- sad：悲伤；
- fearful：恐惧；
- surprised：惊讶；
- doubtful：疑惑；
- sarcastic：嘲讽；
- tender：温柔；
- embarrassed：尴尬；
- determined：坚定；
- tired：疲惫。

额外字段：`emotion_confidence`、`intensity`（1-3）、`delivery`（表达方式）。

情绪与表达方式分开：例如 `emotion=angry` + `delivery=shouting`。表达方式可包括 normal、whispering、shouting、crying、laughing、breathless、slow、hesitant 等。

### FR-ANALYSIS-006：人工审核（P0）

- 可筛选“说话人待确认”“情绪低置信度”“角色待合并”“无可用声音”等状态。
- 可修改文本、段落类型、说话人、情绪、强度、表达方式、TTS 指令。
- 支持批量选择多段并统一修改角色/情绪，但修改前显示影响范围。
- 用户人工确认的字段标记为 `user_locked=true`，后续 AI 重跑不得覆盖。
- 可查看原文上下文，避免只凭单句判断。

## 5.3 角色和旁白声音管理

### FR-VOICE-001：独立旁白声音（P0）

- 每本书必须存在一个 `narrator` 说话人配置。
- 旁白声音与任何小说角色分离。
- 旁白可以 AI 设计、试听、重新设计和绑定。
- 没有配置旁白音色时，生成任务不得静默使用角色声音；应提示用户先创建/选择旁白声音。

### FR-VOICE-002：AI 角色声音设计（P0）

- 根据角色性别、年龄段、声音特点、人物性格和用户编辑的自然语言描述生成音色候选。
- 每个角色默认生成 2-3 个候选（可在设置中调整）。
- 用户可以试听候选并选择一个绑定。
- 设计参考音频时必须同时记录对应的参考文本 `reference_text`。
- 保存音色描述、设计模型、模型版本、生成时间、参考 WAV 路径和候选状态。
- 不要求用户上传真人声音。

### FR-VOICE-003：音色一致性（P0）

- 角色选定声音后，后续所有该角色片段使用同一个 Voice Profile。
- TTS 合成时使用固定参考音频/参考提示特征，不得每句话重新随机设计音色。
- 支持角色更换音色；更换时提示将影响哪些片段，并允许只重生成选中章节/范围。
- 旁白同样使用固定档案。

### FR-VOICE-004：试听与音色管理（P0）

- 试听文本可编辑，默认使用固定测试文本。
- 候选可重命名、收藏、删除、重新生成。
- 删除正在被片段引用的 Voice Profile 时必须阻止或要求先重新绑定，不得造成悬空引用。
- 同一个 Voice Profile 可以被多个角色复用，但默认每个角色单独绑定。

### FR-VOICE-005：合法使用提示（P1）

- 未来若增加真人参考声音导入，必须提醒用户确认其拥有录音使用和合成授权。
- 第一版仅用 AI 生成的虚构音色作为默认流程。

## 5.4 语音片段与编辑工作台

### FR-SEG-001：片段切分（P0）

- 对话按一次完整发言切分，避免把同一角色一句话拆成过多碎片。
- 叙述按语义和标点切分，默认每段约 1-3 句。
- 建议默认中文长度范围约 20-150 字，但属于软阈值，不能为了满足字数切坏语义。
- 超长片段按句号、问号、感叹号、逗号和语义断点拆分。
- 不应把引号中的连续发言任意拆开。
- 片段顺序必须稳定，支持拖动调整或插入/删除片段。
- 每段记录在原文中的章节和来源范围。

### FR-SEG-002：文本分层（P0）

每段同时保存：

- `source_text`：原始提取文本，不可被编辑覆盖；
- `speech_text`：实际送入 TTS 的文本，可编辑；
- `text_version`：文本版本号。

文本规范化、数字读法、英文缩写、特殊符号的处理不能修改 source_text。

### FR-SEG-003：片段配音属性（P0）

每段至少包含：

- 类型；
- speaker_id；
- voice_profile_id；
- emotion；
- intensity（1-3）；
- delivery；
- `tts_instruction`（可以被用户编辑）；
- speed（默认 1.0）；
- pause_after_ms（默认按片段类型生成）；
- 分析置信度；
- 审核状态；
- 音频状态。

### FR-SEG-004：单段编辑与重生成（P0）

- 可编辑文本和配音属性。
- 点击“重新生成”只重新生成当前片段。
- 其他片段 WAV 不得被覆盖。
- 每次生成建立新的 audio asset 版本，默认将新成功版本设为当前版本；旧版本保留，可回滚。
- 如果文本、角色、声音档案或 TTS 指令发生变化，当前音频标记为 `stale`，UI 明确提醒需要重新生成。

### FR-SEG-005：批量编辑（P1）

支持对多段批量设置角色、情绪、语速、音色档案和生成状态筛选。

## 5.5 语音合成与任务队列

### FR-JOB-001：持久化任务队列（P0）

- 生成任务必须写入 SQLite。
- 浏览器关闭、刷新或前端断线不影响已提交任务。
- 默认同一时刻最多运行一个 GPU 推理任务。
- 支持按章节、选中片段、整本书批量生成。
- 支持暂停（停止领取新任务，正在运行任务安全完成后暂停）和继续。
- 支持取消待执行任务；正在执行任务取消时尽可能中止，至少防止结果写入当前音频版本。
- 支持失败重试，默认最多自动重试 2 次；GPU OOM 等错误可提示缩小上下文/重启模型。

### FR-JOB-002：任务状态（P0）

建议状态：

- pending：待执行；
- queued：排队中；
- preparing：准备模型/参考音色；
- running：正在生成；
- post_processing：音频后处理；
- completed：成功；
- failed：失败；
- cancelled：已取消；
- stale：参数变更，音频过期。

每个任务记录创建时间、开始/结束时间、重试次数、耗时、错误摘要、模型名称/版本和输出文件。

### FR-JOB-003：断点恢复（P0）

- 后端启动时扫描上次异常退出时处于 running/preparing 的任务。
- 验证临时文件和最终音频；不完整文件不能标记成功。
- 默认将未完成任务恢复为 pending，用户可在设置中关闭自动恢复。
- 已完成音频不得重复生成。
- 队列按项目/章节/片段顺序处理。

### FR-JOB-004：原子写文件（P0）

- 输出先写入 `.tmp` 文件。
- WAV 校验通过后再原子重命名为最终文件。
- 失败时保留日志，清理或标记临时文件，不删除旧版本有效音频。

### FR-JOB-005：进度通知（P0）

- WebSocket 向前端推送任务创建、开始、阶段变化、完成、失败、进度和错误事件。
- 前端断线后通过 REST API 查询当前任务快照并恢复显示。
- WebSocket 仅用于实时通知，数据库才是任务状态的权威来源。

## 5.6 播放器

### FR-PLAYER-001：段落播放（P0）

- 点击片段播放对应 WAV。
- 按片段顺序自动播放下一段。
- 支持暂停/继续、上一段、下一段、进度条和音量。
- 支持 0.75x、1.0x、1.25x、1.5x、1.75x、2.0x 倍速。
- 播放时高亮当前章节和片段。
- 支持“从当前片段播放本章/整本书”。

### FR-PLAYER-002：播放进度记忆（P0）

保存每本书最近一次播放的章节、片段 ID 和片段内播放位置（毫秒）。重新打开项目后可继续播放。

### FR-PLAYER-003：预播放空缺处理（P0）

- 未生成的片段显示待生成状态。
- 播放到未生成片段时，提示用户选择跳过或等待生成；不应静默跳过。
- 生成音频替换当前版本时，正在播放的旧音频应安全播放结束或在用户确认后切换。

### FR-PLAYER-004：播放列表（P1）

支持按章节、角色或用户选择范围创建临时播放列表。

## 5.7 M4B 导出

### FR-EXPORT-001：导出范围（P0）

支持导出单章或整本书。

### FR-EXPORT-002：导出检查（P0）

开始导出前检查：

- 所选范围是否全部有可用 WAV；
- 是否存在 stale 或 failed 音频；
- 章节顺序是否确定；
- 书名/作者/章节标题等元数据是否完整。

如果存在缺失，展示列表并允许取消或选择跳过；不能静默漏段。

### FR-EXPORT-003：M4B 生成（P0）

- 使用 FFmpeg/ffprobe。
- 按章节和片段顺序拼接。
- 章节标题写入容器章节元数据。
- 音频编码建议 AAC，参数可配置；默认以语音清晰、文件较小为目标。
- 记录每章的起止时间，时间单位和章节索引保持一致。
- 输出 `.m4b`，并使用 ffprobe 验证音频流和章节数量。
- 默认章节间插入可配置静音（例如 700-1000ms）；片段间停顿来自 `pause_after_ms`，但原始单段 WAV 不被修改。
- 导出过程不修改源 WAV。

### FR-EXPORT-004：导出元数据（P0）

包含书名、作者、描述（如有）、语言、章节标题。封面图片作为 P1。

### FR-EXPORT-005：重复导出（P0）

重复导出时创建新输出文件或明确覆盖确认。不得覆盖源 WAV 或原始小说。

## 5.8 设置与状态监控

### FR-SETTINGS-001：应用设置（P0）

- 语言：第一版简体中文。
- 主题：浅色/深色，默认深色或跟随系统。
- 数据目录：默认用户文档目录下的 NovelVoiceStudio，可修改。
- 模型目录/缓存目录：允许设置。
- LLM 模型名称与 Ollama URL。
- TTS Provider、模型路径、运行环境/Worker URL。
- 最大 GPU 并发数初始固定为 1；高级设置可开放。
- 自动恢复任务开关。
- 默认语速和停顿参数。

### FR-SETTINGS-002：系统健康检查（P0）

展示：FastAPI 状态、数据库状态、Ollama 可用性、LLM 模型是否存在、TTS Worker 可用性、FFmpeg/ffprobe 是否可用、GPU 状态（可读取时）、数据目录剩余空间。

某项检查失败时显示可操作的错误说明，不影响不依赖该组件的页面打开。

---

# 6. 页面与交互规格

## 6.1 全局布局

桌面浏览器优先，建议最小支持宽度 1100px；不以手机端为第一目标。

- 左侧固定导航：书库、声音库、任务队列、设置。
- 顶部显示当前项目名称、全局生成状态、GPU 状态（若可取）。
- 主内容区根据页面展示表格、卡片、编辑器或播放器。
- 默认采用专业、简洁的深色工作台风格；支持浅色主题。
- 避免过度动画，优先保证长列表、编辑器和播放控件响应流畅。

## 6.2 页面列表

| 页面 | 主要内容 | MVP |
|---|---|---|
| 小说书库 | 创建/导入/打开/删除/备份小说 | P0 |
| 小说工作区 | 章节树、解析信息、分析状态 | P0 |
| 角色管理 | 角色画像、别名、关系、审核 | P0 |
| 声音管理 | 旁白/角色音色设计、候选试听、绑定 | P0 |
| 配音工作台 | 片段列表、文本和情绪编辑、单段试听、生成队列 | P0 |
| 播放器 | 章节/片段播放、跳转、倍速、记忆进度 | P0 |
| 导出 | M4B 导出、章节校验、导出历史 | P0 |
| 设置 | 模型配置、数据目录、环境检查、日志 | P0 |

## 6.3 配音工作台交互细节

建议采用三栏结构：

- 左栏：章节列表、章节分析状态、筛选器。
- 中栏：片段列表，按顺序展示文本、角色、情绪、音频状态。
- 右栏：当前片段详细编辑区，包括文本、段落类型、角色、情绪、强度、表达方式、语速、TTS 指令、声音档案和试听播放器。

片段状态用文字和图标表达，不只依赖颜色。列表应支持按状态过滤：全部、待审核、待生成、生成中、已完成、失败、音频过期。

批量生成按钮需提示将处理的片段数量、预计磁盘需求（能估算时）以及 GPU 串行生成说明。

---

# 7. AI 分析规范

## 7.1 分析输出必须结构化

LLM 必须按 JSON Schema 输出。后端使用 Pydantic 验证；若输出不合法，最多做有限次数修复重试，仍失败则任务报错并保留原文，不可静默解析不完整 JSON。

## 7.2 角色分析原则

- 角色事实与推断分开保存。
- 性别、年龄、人物类型允许 unknown。
- 角色名字、别名必须附带章节/原文证据位置。
- 人物关系允许 uncertain，不可把推测当成事实。
- 角色合并时保留来源 ID 和变更记录。
- 对没有名字但有稳定叙事身份的人物（如“掌柜”“老和尚”）可以先作为临时角色，但需标记 provisional。

## 7.3 情绪分析原则

- 默认情绪为 neutral。
- 只有文本本身或上下文有足够依据时才标注强烈情绪。
- 不根据感叹号单独判断愤怒；感叹号也可能是兴奋、惊讶或强调。
- 旁白情绪要克制，避免整段小说都被夸张演绎。
- LLM 输出“情绪标签”和“自然语言 TTS 指令”，但两者必须分别保存。
- 允许用户覆盖 AI 判断并锁定。

## 7.4 上下文和记忆策略

- 按章节分批分析，不能整本小说放进单次上下文。
- 为每章生成摘要，包括人物变化、地点、事件、角色新关系和未解决的代词指代。
- 下一章分析可引用上一章摘要和全书角色表。
- 不把摘要当作原文证据；关键 speaker 判断仍需结合片段附近原文。
- 用户修改角色或说话人后，后续分析应优先使用已确认角色注册表。

## 7.5 Prompt 管理

- Prompt 模板保存在 `backend/app/ai/prompts/`。
- 每套 Prompt 有版本号。
- 数据库记录每次分析使用的 Prompt 版本和模型名称。
- 更改 Prompt 不应自动覆盖之前已确认的分析。
- 测试样本包含至少 3 个角色、别名、交替对话、旁白、内心独白、歧义 speaker 和多种情绪。

---

# 8. 数据库设计

SQLite 是业务数据唯一事实来源。第一版至少建立以下表；字段可扩展，但不得丢失这些语义。

## 8.1 `books`

- `id` TEXT PRIMARY KEY
- `title` TEXT NOT NULL
- `author` TEXT NULL
- `description` TEXT NULL
- `language` TEXT DEFAULT 'zh-CN'
- `source_file_path` TEXT
- `project_dir` TEXT NOT NULL
- `status` TEXT
- `created_at` DATETIME
- `updated_at` DATETIME

## 8.2 `chapters`

- `id` TEXT PRIMARY KEY
- `book_id` TEXT REFERENCES books(id)
- `title` TEXT
- `sequence` INTEGER
- `source_start_offset` INTEGER NULL
- `source_end_offset` INTEGER NULL
- `summary` TEXT NULL
- `analysis_status` TEXT
- `created_at` DATETIME
- `updated_at` DATETIME

## 8.3 `characters`

- `id` TEXT PRIMARY KEY
- `book_id` TEXT REFERENCES books(id)
- `canonical_name` TEXT NOT NULL
- `aliases_json` TEXT
- `gender` TEXT DEFAULT 'unknown'
- `gender_basis` TEXT DEFAULT 'unknown'
- `age_range` TEXT DEFAULT 'unknown'
- `age_basis` TEXT DEFAULT 'unknown'
- `character_type` TEXT DEFAULT 'unknown'
- `identity_description` TEXT
- `personality_json` TEXT
- `voice_description` TEXT
- `confidence` REAL NULL
- `evidence_json` TEXT
- `is_provisional` BOOLEAN DEFAULT 0
- `user_locked` BOOLEAN DEFAULT 0
- `created_at` DATETIME
- `updated_at` DATETIME

## 8.4 `character_relationships`

- `id` TEXT PRIMARY KEY
- `book_id` TEXT
- `source_character_id` TEXT
- `target_character_id` TEXT
- `relationship_type` TEXT
- `description` TEXT
- `confidence` REAL NULL
- `evidence_json` TEXT

## 8.5 `voice_profiles`

- `id` TEXT PRIMARY KEY
- `book_id` TEXT REFERENCES books(id)
- `owner_type` TEXT：`narrator` / `character`
- `owner_id` TEXT NULL：旁白可为空
- `name` TEXT NOT NULL
- `voice_description` TEXT
- `design_model` TEXT
- `design_model_version` TEXT
- `tts_provider` TEXT
- `tts_model` TEXT
- `reference_audio_path` TEXT
- `reference_text` TEXT
- `reference_sample_rate` INTEGER NULL
- `provider_prompt_cache_path` TEXT NULL
- `status` TEXT
- `is_selected` BOOLEAN DEFAULT 0
- `created_at` DATETIME
- `updated_at` DATETIME

## 8.6 `segments`

- `id` TEXT PRIMARY KEY
- `book_id` TEXT REFERENCES books(id)
- `chapter_id` TEXT REFERENCES chapters(id)
- `sequence` INTEGER
- `source_text` TEXT NOT NULL
- `speech_text` TEXT NOT NULL
- `segment_type` TEXT
- `speaker_id` TEXT NULL：角色 ID 或固定值 `narrator` / `unknown`
- `voice_profile_id` TEXT NULL
- `emotion` TEXT DEFAULT 'neutral'
- `emotion_confidence` REAL NULL
- `intensity` INTEGER DEFAULT 1
- `delivery` TEXT DEFAULT 'normal'
- `tts_instruction` TEXT
- `speed` REAL DEFAULT 1.0
- `pause_after_ms` INTEGER DEFAULT 250
- `speaker_confidence` REAL NULL
- `review_status` TEXT
- `user_locked_fields_json` TEXT
- `text_version` INTEGER DEFAULT 1
- `audio_status` TEXT
- `current_audio_asset_id` TEXT NULL
- `source_start_offset` INTEGER NULL
- `source_end_offset` INTEGER NULL
- `created_at` DATETIME
- `updated_at` DATETIME

## 8.7 `audio_assets`

- `id` TEXT PRIMARY KEY
- `segment_id` TEXT REFERENCES segments(id)
- `version` INTEGER
- `file_path` TEXT NOT NULL
- `format` TEXT DEFAULT 'wav'
- `sample_rate` INTEGER
- `channels` INTEGER
- `sample_width_bits` INTEGER
- `duration_ms` INTEGER
- `file_size_bytes` INTEGER
- `checksum_sha256` TEXT
- `tts_provider` TEXT
- `tts_model` TEXT
- `generation_params_json` TEXT
- `created_at` DATETIME
- `is_valid` BOOLEAN DEFAULT 1

## 8.8 `generation_jobs`

- `id` TEXT PRIMARY KEY
- `book_id` TEXT
- `job_type` TEXT：analysis / voice_design / tts / export
- `target_id` TEXT NULL
- `status` TEXT
- `priority` INTEGER DEFAULT 100
- `progress` REAL DEFAULT 0
- `attempt_count` INTEGER DEFAULT 0
- `max_attempts` INTEGER DEFAULT 2
- `error_code` TEXT NULL
- `error_message` TEXT NULL
- `payload_json` TEXT
- `result_json` TEXT
- `started_at` DATETIME NULL
- `finished_at` DATETIME NULL
- `created_at` DATETIME
- `updated_at` DATETIME

## 8.9 `playback_states`

- `id` TEXT PRIMARY KEY
- `book_id` TEXT UNIQUE
- `chapter_id` TEXT NULL
- `segment_id` TEXT NULL
- `position_ms` INTEGER DEFAULT 0
- `playback_rate` REAL DEFAULT 1.0
- `updated_at` DATETIME

## 8.10 数据一致性约束

- 对 `book_id + chapter_id + sequence` 建唯一约束（按实现情况调整）。
- `audio_assets` 与 `segments` 使用外键。
- 删除书籍必须先确认；默认采用软删除或移动到回收站，提供恢复窗口。
- 文件路径以项目根目录为基准的相对路径存储，避免移动数据目录后数据库失效。
- SQLite 开启适当的 foreign_keys、WAL 和 busy_timeout；长任务期间不要持有数据库事务。
- 所有时间使用带时区的 UTC 时间存储，前端按本地时区显示。

---

# 9. 本地文件目录规范

默认数据目录：`%USERPROFILE%\Documents\NovelVoiceStudio\`，用户可以在设置中修改。

```text
NovelVoiceStudioData/
├── app.db
├── backups/
├── logs/
├── books/
│   └── {book_id}/
│       ├── source/
│       │   └── original.txt
│       ├── parsed/
│       │   ├── chapters.json
│       │   └── analysis.json
│       ├── voices/
│       │   ├── narrator/
│       │   │   └── candidate_01.wav
│       │   └── {character_id}/
│       │       └── candidate_01.wav
│       ├── audio/
│       │   └── {chapter_id}/
│       │       └── {segment_id}/
│       │           ├── v001.wav
│       │           └── v002.wav
│       └── exports/
│           ├── book.m4b
│           └── export_manifest.json
└── cache/
```

要求：

- 项目文件路径可随数据目录整体迁移。
- 临时文件放在对应目标目录下的 `.tmp` 或独立临时目录。
- 不把模型权重默认复制进每本小说目录。
- 删除项目时单独确认是否同时删除磁盘文件。
- 提供数据库和项目文件备份功能；第一版至少支持手动备份。

---

# 10. API 设计（建议基线）

API 统一前缀：`/api/v1`。响应使用 JSON，时间字段使用 ISO 8601。

## 10.1 健康检查

- `GET /api/v1/health`：服务运行状态。
- `GET /api/v1/system/status`：Ollama、TTS Worker、FFmpeg、GPU、数据目录状态。

## 10.2 小说与章节

- `GET /api/v1/books`
- `POST /api/v1/books`
- `POST /api/v1/books/import`（multipart/form-data）
- `GET /api/v1/books/{book_id}`
- `PATCH /api/v1/books/{book_id}`
- `DELETE /api/v1/books/{book_id}`
- `GET /api/v1/books/{book_id}/chapters`
- `PATCH /api/v1/chapters/{chapter_id}`
- `POST /api/v1/chapters/{chapter_id}/split`
- `POST /api/v1/chapters/merge`

## 10.3 分析

- `POST /api/v1/books/{book_id}/analysis`：创建全书分析任务。
- `POST /api/v1/chapters/{chapter_id}/analysis`：重跑单章分析。
- `GET /api/v1/books/{book_id}/analysis/status`
- `GET /api/v1/books/{book_id}/characters`
- `PATCH /api/v1/characters/{character_id}`
- `POST /api/v1/characters/merge`
- `POST /api/v1/characters/split`

## 10.4 声音

- `GET /api/v1/books/{book_id}/voices`
- `POST /api/v1/books/{book_id}/voices/design`：创建音色设计任务。
- `POST /api/v1/voices/{voice_id}/preview`：使用指定文本生成试听音频。
- `PATCH /api/v1/voices/{voice_id}`
- `POST /api/v1/voices/{voice_id}/select`
- `DELETE /api/v1/voices/{voice_id}`

## 10.5 片段与音频

- `GET /api/v1/chapters/{chapter_id}/segments`
- `GET /api/v1/segments/{segment_id}`
- `PATCH /api/v1/segments/{segment_id}`
- `POST /api/v1/segments/{segment_id}/generate`
- `POST /api/v1/segments/batch-generate`
- `GET /api/v1/audio/{asset_id}`：支持 HTTP Range，便于浏览器音频播放和拖动进度。
- `GET /api/v1/segments/{segment_id}/audio-versions`
- `POST /api/v1/segments/{segment_id}/audio-versions/{asset_id}/restore`

## 10.6 任务队列

- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/retry`
- `POST /api/v1/jobs/{job_id}/cancel`
- `POST /api/v1/jobs/pause`
- `POST /api/v1/jobs/resume`

## 10.7 播放器与导出

- `GET /api/v1/books/{book_id}/playback-state`
- `PUT /api/v1/books/{book_id}/playback-state`
- `POST /api/v1/books/{book_id}/export/m4b`
- `GET /api/v1/exports/{export_id}`
- `GET /api/v1/exports/{export_id}/download`

## 10.8 WebSocket

端点：`/api/v1/ws/events`。

事件结构示例：

```json
{
  "event": "job.progress",
  "timestamp": "2026-11-25T08:00:00Z",
  "data": {
    "job_id": "job_abc123",
    "status": "running",
    "progress": 0.42,
    "current": 42,
    "total": 100,
    "message": "正在生成第 42 段"
  }
}
```

事件类型至少包括：`job.created`、`job.started`、`job.progress`、`job.completed`、`job.failed`、`job.cancelled`、`gpu.model_loading`、`gpu.model_unloaded`、`system.warning`。

---

# 11. Provider 接口约定

业务代码只依赖抽象接口。真实模型接入前先实现 Fake Provider，确保业务流程可在无 GPU 环境下测试。

## 11.1 LLM Provider

建议接口：

- `health_check()`
- `list_models()`
- `analyze_characters(text, known_registry, context) -> CharacterAnalysisResult`
- `analyze_segments(text, known_registry, context) -> SegmentAnalysisResult[]`
- `summarize_chapter(text, known_registry) -> ChapterSummary`
- `unload_model()`

必须支持结构化输出、超时、重试、模型名称和调用统计记录。

## 11.2 Voice Design Provider

建议接口：

- `health_check()`
- `design_voice(description, reference_text, language) -> VoiceCandidate`
- `preview_voice(candidate, text) -> AudioResult`
- `unload_model()`

## 11.3 TTS Provider

建议接口：

- `health_check()`
- `load_voice_profile(profile)`
- `synthesize(text, voice_profile, emotion, intensity, delivery, instruction, speed) -> AudioResult`
- `unload_model()`

AudioResult 至少包含音频文件/音频数组、sample_rate、channels、duration_ms 和 provider metadata。

## 11.4 GPU Scheduler

- 所有需要 GPU 的 Provider 必须通过统一 Scheduler 获取 GPU Lease。
- 初始串行执行。
- 支持任务优先级：用户单段试听/重生成可以高于整书批量任务，但不得破坏当前生成文件。
- 调度器记录当前持有 GPU 的任务和模型。
- 服务异常退出后，启动流程需检查遗留 Worker 并避免多个 Worker 同时抢占 GPU。

---

# 12. AI 任务输出 JSON 示例

## 12.1 角色分析

```json
{
  "characters": [
    {
      "character_id": "char_001",
      "canonical_name": "李寻欢",
      "aliases": ["小李探花", "李探花"],
      "gender": "male",
      "gender_basis": "explicit",
      "age_range": "adult",
      "age_basis": "inferred",
      "character_type": "human",
      "identity_description": "江湖人物，主角",
      "personality": ["沉稳", "温和", "忧郁"],
      "voice_description": "成年男性，中低音，沉稳、清晰，略带疲惫感",
      "confidence": 0.91,
      "evidence": [
        {
          "chapter_id": "chapter_001",
          "quote": "……",
          "reason": "上下文明确指出该称谓指向李寻欢"
        }
      ]
    }
  ]
}
```

## 12.2 片段分析

```json
{
  "segments": [
    {
      "segment_id": "seg_000042",
      "source_text": "你竟然敢骗我！",
      "speech_text": "你竟然敢骗我！",
      "segment_type": "dialogue",
      "speaker_id": "char_001",
      "speaker_confidence": 0.94,
      "emotion": "angry",
      "emotion_confidence": 0.88,
      "intensity": 3,
      "delivery": "shouting",
      "tts_instruction": "愤怒而激动地大声喊出，语速略快，但保持角色固定音色。",
      "review_status": "needs_review"
    }
  ]
}
```

以上数据仅为结构示例。真实实现必须使用稳定 ID，并由后端校验引用是否存在。

---

# 13. 错误处理与恢复

| 场景 | 系统行为 |
|---|---|
| TXT 编码无法判断 | 显示预览并允许用户选择编码 |
| 章节标题识别失败 | 作为单章节导入，允许人工分章 |
| LLM 输出 JSON 不合法 | 限制修复重试；失败时保存错误，不覆盖已有分析 |
| 说话人无法判断 | speaker=unknown，进入待审核 |
| 没有旁白/角色声音 | 阻止对应 TTS 任务并提示创建声音 |
| TTS 模型未安装 | 显示配置/下载说明，不调用云端替代 |
| GPU OOM | 释放模型、记录错误，提示改用小模型或降低参数；不得无限重试 |
| 音频生成中断 | 使用临时文件；无效文件不标记完成 |
| 浏览器关闭 | 后端任务继续；重新打开后拉取状态 |
| 后端异常退出 | 下次启动检查任务和文件，恢复未完成任务 |
| 磁盘空间不足 | 批量生成/导出前检查可用空间；过程中失败时保留已完成文件 |
| M4B 导出失败 | 保留原 WAV，显示 ffmpeg 日志，允许重试 |
| 数据库升级失败 | 启动前自动备份数据库；升级失败回滚并提示 |

日志不得记录不必要的整本小说内容。普通日志记录 job_id、book_id、segment_id 和异常信息即可；调试模式才允许选择性保存 Prompt/模型返回内容，且必须告知用户。

---

# 14. 安全、隐私与本地化要求

1. FastAPI 默认只监听 `127.0.0.1`，不得默认监听 `0.0.0.0`。
2. 仅允许预期的 localhost Origin/Host；禁止 `Access-Control-Allow-Origin: *`。
3. 文件 API 必须限制在配置的数据根目录内，防止路径穿越、任意文件读取/写入。
4. 上传文件需校验扩展名、大小、内容和目标路径；不能仅依赖浏览器传来的 MIME。
5. 不允许浏览器直接访问任意本地路径；通过后端受控文件 API 提供音频与下载。
6. 不向第三方传输小说文本、音频或角色资料。
7. 不加入默认遥测、崩溃上传或自动云端备份。
8. 模型下载仅访问用户明确选择的官方模型来源；下载前显示模型名称、磁盘空间和来源。
9. 模型代码、模型权重、Python 包和 FFmpeg 的许可证必须分别记录；不能仅凭仓库开源就推断所有权重、依赖和输出均无使用限制。
10. 用户应对导入小说的版权和使用权限负责；软件仅作为本地个人制作工具。

---

# 15. 非功能需求

## 15.1 稳定性

- 关闭浏览器不影响运行中的后台生成任务。
- 后端重启后可恢复队列。
- 已完成 WAV 不因重试或新版本生成而被删除。
- 数据库操作使用短事务，禁止在 TTS 生成期间长时间占用事务锁。

## 15.2 性能

- 页面列表使用分页或虚拟列表，避免一次性渲染数千段。
- 对章节文本和角色搜索建立合理索引。
- WebSocket 事件只推送变化，不频繁发送整个章节内容。
- 音频按需加载，单段播放支持 HTTP Range。
- 首版生成速度不设固定硬指标，因为它依赖模型、采样长度、显存和硬件环境；必须记录实际耗时和 RTF（Real-Time Factor），方便后续优化。

## 15.3 可维护性

- 所有模型 Provider 独立封装。
- 配置不得散落在业务代码中。
- 数据库使用迁移机制。
- API 使用 Pydantic Schema。
- 后端类型标注完整，关键逻辑有单元测试。
- 错误信息区分用户可读摘要和开发者日志。
- README 包含环境安装、模型下载、启动、停止、备份和排错说明。

## 15.4 可移植性

- 项目数据与源代码分离。
- 数据库保存相对文件路径。
- 用户可以将数据目录移动到其他磁盘。
- 将来允许复用 Vue 前端并封装桌面端，但第一版不实现 Wails。

---

# 16. MVP 分阶段实施与验收

## 阶段 0：技术可行性 POC（优先级最高）

目标：在目标电脑上验证本地模型链路，不先开发完整 UI。

任务：

1. 确认显卡型号、显存、驱动和 CUDA/PyTorch 环境。
2. Ollama 调用 Qwen3.5 4B，测试中文结构化 JSON 输出；可选再测 9B。
3. Qwen3-TTS VoiceDesign 根据 2-3 种人物描述生成角色参考声音。
4. CosyVoice 3 使用参考 WAV + 参考文本生成同一角色的多种情绪语音。
5. 比较声音一致性、中文发音、情绪可控性和生成时长。
6. 测试 GPU 模型卸载与重新加载，确认不会同时占用全部显存。
7. Windows 原生环境先验证；失败时记录依赖原因，再单独试 WSL2 Worker。

阶段 0 验收：

- 可生成至少 3 个音色不同的虚构声音；
- 同一个角色至少生成 neutral、angry、sad 三种情绪样本；
- 同一角色不同情绪仍可辨认为同一声音（由用户主观试听确认）；
- 目标硬件上不出现持续性 CUDA OOM；
- 所有依赖、模型 ID、环境版本和运行命令记录在 `docs/reports/POC_REPORT.md`。

若“Qwen3-TTS VoiceDesign -> CosyVoice 3”声音迁移不够稳定，必须调整模型组合并记录结论，不得跳过验证。

## 阶段 1：项目骨架与 TXT 最小闭环

开发：

- Vue 3 + Vite + Naive UI 基础布局；
- FastAPI 服务和健康检查；
- SQLite/SQLAlchemy/Alembic；
- 书库、创建项目、TXT 导入、章节解析；
- Mock LLM/TTS Provider；
- 项目数据持久化。

验收：导入一份 TXT，看到章节和正文；关闭并重新启动服务后数据仍存在；不依赖 AI 模型也能运行基础功能。

## 阶段 2：AI 小说分析与审核

开发：

- Ollama Provider；
- 角色提取、别名归并、人物画像；
- 逐章节摘要；
- 片段切分；
- speaker/emotion/delivery 分析；
- 角色审核和片段审核。

验收：对包含至少 3 个角色、别名、交替对话、旁白和内心独白的测试小说，系统能生成可编辑结构化结果；低置信度进入待审核；用户修改后不被重跑覆盖。

## 阶段 3：声音设计与单段 TTS

开发：

- Voice Design Provider；
- TTS Provider；
- 旁白和角色音色管理；
- 音色候选试听和绑定；
- 单段 WAV 生成；
- 音频版本管理。

验收：旁白和至少两个角色使用不同固定音色；同一角色可生成不同情绪；每个片段 WAV 可单独播放和重新生成。

## 阶段 4：批量任务与播放器

开发：

- SQLite 持久化队列；
- GPU 串行调度；
- WebSocket 进度；
- 暂停、继续、取消和重试；
- 重启恢复；
- 段落/章节播放器和进度记忆。

验收：批量生成中关闭浏览器，服务继续运行；停止并重启后未完成任务可恢复；已完成音频不重复生成；播放器可从任意片段连续播放。

## 阶段 5：M4B 导出与完善

开发：

- FFmpeg 拼接；
- 章节元数据；
- 导出任务进度；
- ffprobe 导出校验；
- 错误日志与重试。

验收：整本测试书可导出 M4B；ffprobe 检测到正确的音频流和章节数；章节顺序、标题、起止时间正确；原 WAV 不受导出影响。

## 16.1 第一版最终验收清单

- [ ] 能导入 TXT 并保留原文。
- [ ] 能解析或手动维护章节。
- [ ] 能识别角色、别名、性别、年龄范围和人物关系。
- [ ] 不确定的人物事实可标记未知/推断。
- [ ] 能区分旁白和角色对话。
- [ ] 旁白使用独立固定音色。
- [ ] 能通过 AI 生成角色声音候选并试听。
- [ ] 同一个角色跨段落保持固定音色。
- [ ] AI 自动判断情绪并允许人工修改。
- [ ] 能逐段生成独立 WAV。
- [ ] 能单段重新生成并保留历史版本。
- [ ] 后台队列支持暂停、继续、重试和断点恢复。
- [ ] 关闭浏览器不会中断队列。
- [ ] 可从任意片段开始播放并连续播放章节。
- [ ] 可记忆上次播放位置。
- [ ] 可导出带章节导航的 M4B。
- [ ] 全流程不需要付费云端 AI API。
- [ ] 默认服务只监听 localhost，小说内容不出本机。

---

# 17. 开发测试要求

## 17.1 自动化测试

后端 pytest 至少覆盖：

- TXT 编码识别和异常编码；
- 章节解析与无章节回退；
- 分段顺序稳定性；
- Pydantic JSON 输出校验；
- 角色合并/拆分关联更新；
- 用户锁定字段不被 AI 覆盖；
- 文件路径越权拦截；
- 音频临时文件原子提交；
- 队列状态迁移和恢复；
- 数据库迁移；
- M4B 导出元数据构建（可用 FFmpeg fixture 或 Mock）。

前端 Vitest 至少覆盖：

- 片段列表与筛选；
- 角色/情绪编辑；
- 待生成/生成中/成功/失败状态展示；
- WebSocket 断开后的 REST 状态恢复；
- 播放器段落切换和进度保存。

## 17.2 测试数据

创建一份人工编写的短篇虚构小说 fixture，至少包含：

- 3 个角色；
- 2 个以上别名；
- 旁白；
- 角色交替对话；
- 内心独白；
- 愤怒、开心、悲伤、疑惑等情绪；
- 一个说话人不明确的片段；
- 一个没有明确年龄/性别证据的人物。

不要把受版权保护的真实长篇小说正文提交到公开测试仓库。

## 17.3 手动真实模型测试

自动化 CI 不要求下载大模型或使用 GPU。真实模型测试单独记录：模型 ID、模型文件哈希（可获取时）、驱动版本、Python 版本、Torch/CUDA 版本、显存峰值、生成耗时、试听结论和问题。

---

# 18. 安装、配置与运行要求

## 18.1 首次安装

提供清晰的 Windows PowerShell 脚本和说明：

1. 检查 Python、Node.js、npm、FFmpeg、ffprobe、Ollama 是否存在。
2. 创建核心后端虚拟环境。
3. 安装 Python 依赖。
4. 安装前端依赖。
5. 创建数据目录和配置文件。
6. 检查 Ollama 本地 API。
7. 显示推荐模型 ID 和下载方式，但不得未确认就下载大型模型。
8. 模型 Worker 环境单独安装，分别记录依赖锁定文件。

## 18.2 启动

用户运行 `scripts/start.ps1` 后：

- 检查端口 8765 是否被占用；
- 若占用，明确提示端口和进程信息，不要直接杀进程；
- 启动 FastAPI；
- 等待 `/api/v1/health` 成功；
- 打开 `http://127.0.0.1:8765`。

## 18.3 配置文件示例

```yaml
app:
  host: "127.0.0.1"
  port: 8765
  language: "zh-CN"
  data_dir: ""

llm:
  provider: "ollama"
  base_url: "http://127.0.0.1:11434"
  model: "qwen3.5:4b"
  context_length: 8192
  keep_alive: "5m"

voice_design:
  provider: "qwen3_tts"
  model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
  worker_url: "http://127.0.0.1:8766"

tts:
  provider: "cosyvoice3"
  model_id: "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
  worker_url: "http://127.0.0.1:8767"
  default_speed: 1.0

jobs:
  max_gpu_concurrency: 1
  max_retries: 2
  auto_resume: true

export:
  audio_codec: "aac"
  audio_bitrate: "96k"
  chapter_gap_ms: 800
```

注意：配置中的模型名称和 Worker 地址只是初始候选值。必须由真实 POC 成功后确认；不得将示例路径或参数视为保证兼容的最终配置。配置文件应使用小写 `tts` 等统一命名并通过 Pydantic 校验。

---

# 19. 风险清单与解决策略

- **12GB 显存不足：** 影响：模型加载失败或 OOM；解决策略：单 GPU 任务调度，先测 4B LLM，模型分时加载，Worker 可重启。
- **模型环境依赖冲突：** 影响：安装/升级困难；解决策略：Qwen3-TTS 与 CosyVoice 使用独立 Python 环境。
- **角色说话人误判：** 影响：有声书内容错误；解决策略：提供置信度、待审核状态、上下文窗口和用户锁定。
- **角色音色漂移：** 影响：角色听起来不一致；解决策略：固定参考音频或缓存提示特征，固定模型版本与参数，并进行回归试听。
- **VoiceDesign 跨模型迁移效果不稳定：** 影响：音色不一致；解决策略：POC 验证，Provider 可替换，并准备同模型替代流程。
- **句子太短/太长：** 影响：音频不自然或不便编辑；解决策略：对话按发言轮次切分，旁白按语义分段，并配置软阈值。
- **情绪指令效果不明显：** 影响：演绎单调；解决策略：将情绪和 delivery 分离，支持试听、重试和 TTS 手工指令。
- **长书分析上下文超限：** 影响：角色和事件遗忘；解决策略：分章节分析、章节摘要和全书角色注册表。
- **浏览器关闭误以为任务停止：** 影响：任务丢失；解决策略：数据库持久化 Worker 队列，前端只订阅状态。
- **磁盘占用过大：** 影响：生成中断；解决策略：展示 WAV 空间、检查剩余空间并提供清理策略。
- **M4B 章节兼容性：** 影响：章节跳转失败；解决策略：使用 ffprobe 检查，并用目标播放器进行实际播放测试。

---

# 20. 官方项目与参考文档

以下为编写本需求文档时参考的官方资料。模型仓库和依赖会更新，实际开发时应再次确认版本、安装方式和许可证。

1. Ollama Windows 文档：<https://github.com/ollama/ollama/blob/main/docs/windows.mdx>
2. Ollama API 文档：<https://github.com/ollama/ollama/blob/main/docs/api.md>
3. Ollama Qwen3.5 模型库：<https://ollama.com/library/qwen3.5>
4. Qwen3-TTS 官方仓库：<https://github.com/QwenLM/Qwen3-TTS>
5. Qwen3-TTS VoiceDesign 模型：<https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign>
6. CosyVoice 官方仓库：<https://github.com/QwenAudio/CosyVoice>
7. Fun-CosyVoice3-0.5B-2512 模型：<https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512>
8. FFmpeg 文档：<https://ffmpeg.org/documentation.html>
9. FFmpeg Metadata 文档：<https://ffmpeg.org/ffmpeg-formats.html#Metadata-1>
10. FastAPI 文档：<https://fastapi.tiangolo.com/>
11. Vue 3 文档：<https://vuejs.org/>

**许可证注意：** CosyVoice 仓库与所列模型页面标注 Apache-2.0；Qwen3-TTS 仓库/模型页面也标注 Apache-2.0。仍需逐一核对实际下载的具体 checkpoint、第三方依赖、二进制工具和未来替换模型的许可证及使用限制。不得将“免费开源”理解成所有模型、依赖、数据和用途都自动拥有相同授权。

---

# 21. 项目最终交付物

第一版交付应包含：

1. 可运行的 Vue 3 + FastAPI 源代码；
2. SQLite 数据库初始化与 Alembic 迁移；
3. Mock LLM/TTS Provider，用于无模型开发和测试；
4. 真实 Ollama LLM Provider；
5. 真实 Voice Design 与 TTS Provider；
6. 持久化任务队列与 GPU 调度；
7. WAV 音频管理、播放器和 M4B 导出；
8. Windows 安装、启动、停止和环境检查脚本；
9. README（中文安装、模型配置、运行、排错、备份）；
10. 自动化测试和 POC 报告；
11. 示例配置文件和人工编写的虚构测试小说。

## 21.1 第一版完成定义（Definition of Done）

只有当第 16.1 节最终验收清单全部通过，且没有阻断级数据丢失、音频覆盖、路径越权或后台任务丢失问题时，才可以标记 MVP 完成。

**最终核心闭环：** 导入 TXT -> AI 识别角色及旁白 -> AI 自动生成/分配声音 -> AI 判断每段情绪 -> 人工审核 -> 逐段生成 WAV -> 任意段落播放 -> 导出带章节导航 M4B。
