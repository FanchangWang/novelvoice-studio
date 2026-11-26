# 架构决策与假设记录

本文件记录需求文档中未定义或存在冲突时的实施口径。所有口径以「简单、可测试、可替换」为原则。

## 1. 阶段编号冲突

- 文档 0.2 节把「项目骨架与最小闭环」称为阶段 0。
- 文档第 16 节把「技术可行性 POC」称为阶段 0，「骨架与 TXT 最小闭环」称为阶段 1。
- **采用第 16 节口径**：阶段 0 = POC，阶段 1 = 骨架闭环。已与用户确认。

## 2. 模型与配置

- 所有模型 ID 均为候选值，必须以 `docs/reports/POC_REPORT.md` 实测结果为准。
- 核心项目：uv project，Python 3.12（已确认）。
- CosyVoice 引擎服务：独立 Python 3.10 环境（官方建议版本），目录内 `pyproject.toml` 已限定 `requires-python = ">=3.10,<3.11"`；`uv run` 会自动选用 3.10，不与核心项目混用。
- Qwen3-TTS 引擎服务：独立 Python 3.12 环境，目录内同样有独立 `pyproject.toml`。
- 引擎服务的 `uv.lock` 纳入版本管理；历史实验脚本归档在 `workers/experiments/`，`.venv`、CosyVoice 第三方克隆、模型与输出文件不提交。生产服务清单见 `workers/services.yaml`，不把实验脚本当作服务入口。
- LLM 实测采用 ModelScope 文本版 GGUF，调用时必须 `think=false`，否则 `message.content` 为空。
- 大型模型下载走 ModelScope 国内源；Ollama 安装包走本机 GitHub 加速器。
- 不自动下载模型；`scripts/download_models.ps1` 仅在用户明确确认后运行。

## 3. Provider 接口增补

需求第 11 节的 Provider 接口缺少协作式取消与进度回调，而 FR-JOB-001 要求运行中取消与进度推送。实施时统一增加：

- `cancel()` / cancel token
- `progress_callback`

## 4. 数据口径

- 时间统一存 ISO-8601 UTC 字符串，前端转本地时区显示（SQLite 无原生带时区类型）。
- `stale` 只作为 `audio_status`；任务状态使用 `failed` / `cancelled`。
- `owner_type='narrator' AND owner_id IS NULL` 表示书籍级旁白音色档案。
- `source_text` 只读；用户编辑仅修改 `speech_text`。
- `book_id + chapter_id + sequence` 唯一约束需结合软删除（`deleted_at`）设计，避免与拖动排序冲突。
- LLM JSON 修复重试次数定为 1–2 次，并记录 `prompt_version` 与 `model`。

## 5. 验收口径

- 音色一致性为人工主观试听 gate，不可自动宣称通过。
- POC 报告只记录实测结果，未执行项保持「待验证」。

## 6. 阶段 1 实施口径

### 6.1 进程与启动

- 服务只绑定回环地址，启动时再次校验 `app.host`，配置非法直接拒绝启动。
- `scripts/server_control.py` 是启动/停止的唯一实现；PowerShell 脚本只做检查、调用和展示，避免逻辑分叉。
- 服务以分离进程运行，日志写入 `.run/server.log`，状态写入 `.run/server.json`。
- 每次启动生成随机 `NOVELVOICE_CONTROL_TOKEN`，只注入子进程；隐藏接口 `POST /api/v1/system/shutdown` 在无令牌时返回 404，令牌错误返回 403，通过 `uvicorn.Server.should_exit` 优雅退出。
- 停止流程：先请求接口并等待最多 15 秒；`-Force` 也必须在 PID + 可执行文件 + 创建时间匹配后才结束进程。Windows 上强制停止使用 `taskkill /T` 结束已核实的本项目进程树，避免 venv 启动器留下占端口的子进程。无状态文件但端口被占用时只报告，不误杀。
- `.run/` 含控制令牌，已加入 `.gitignore`，不得提交。

### 6.2 TXT 导入

- 只接受 `.txt`，扩展名与大小双重校验；上传内容先读入内存，再走同一个导入服务，避免临时文件泄漏。
- 编码识别给出候选、置信度与理由；低置信度必须人工确认，不静默猜测。
- 章节解析优先使用内置中文标题正则，也支持用户自定义正则；解析结果在预览阶段展示，不落库。
- 重复检测按文件 SHA-256；策略为 `new` / `reject` / `replace`，导入弹窗默认中止。
- 导入文件按受控书籍 ID 写入数据目录；所有 ID 经过校验，路径拼接后仍校验父目录，防路径穿越。
- 软删除书籍进入回收站语义，可恢复；删除不清空原文。

### 6.3 章节序号调整

SQLite 的 `(book_id, sequence)` 唯一约束立即检查，而批量 UPDATE 不保证按业务期望顺序执行。拆分/合并若直接对后续章节 `+1/-1`，会出现「先更新到仍被占用的序号」导致唯一约束失败的中间态。

解决方式：`ChapterService._shift_sequences` 先把整组序号抬到 `max(sequence) + count + 2` 的临时区间，`flush` 后再落到最终值，再次 `flush`。拆分与合并共用该 helper；回归测试 `test_split_then_merge_with_later_chapters` 覆盖「拆分后合并，且后面还有章节」的真实路径。

### 6.4 前端

- Vue 3 + Vue Router + Pinia + Naive UI，Tailwind 4 负责设计令牌与布局。
- 业务状态集中在 Pinia store，组件不直接拼接 API URL；请求统一走 `src/api/client.ts`，错误转换为可读中文。
- 书库搜索计数使用 `whitespace-nowrap`，避免窄屏 flex 挤压换行。
- AppShell 的 Provider 提示区分「后端未连接 / 状态检测中 / 模拟模式 / 真实 Provider」，避免加载中误报 Mock 说明。
- 生产构建由 FastAPI 托管；未知非 API 路径回退 `index.html`，带后缀的静态资源缺失返回 404，不把任意本地文件当静态资源。

### 6.5 配置与数据

- YAML + `.env` + 环境变量覆盖，统一由 pydantic-settings 合并；业务代码不读取散落的环境变量。
- SQLite 使用 WAL；启动时按配置自动迁移，迁移前备份到数据目录 `backups/`。
- 原始 TXT 保留；`source_text` 只读，编辑只影响 `speech_text`。
- 时间统一 UTC，前端本地化展示。
- 数据目录、日志、`.run/`、SQLite 文件、前端构建产物、引擎服务虚拟环境与模型均不提交。

## 7. 测试策略

- 后端 pytest 使用临时数据目录与独立 SQLite，覆盖迁移、编码识别、章节解析、导入、去重、软删除、拆分/合并、Mock Provider、系统状态与停机令牌。
- 前端 Vitest 覆盖 API 客户端、store 与工具函数；`npm run build` 同时执行 `vue-tsc` 类型检查。
- 端到端冒烟使用真实 HTTP + 浏览器走查：启动、健康检查、静态托管、导入、拆分/合并、停止、重启后持久化。
- 不在测试中加载真实模型或调用付费云服务；阶段 2 另用本机 Ollama 做人工验收，不进入自动化测试。

## 8. 阶段 2 实施口径

### 8.1 本地优先与结构化输出

- 真实 LLM 只允许本机 Ollama；`llm.base_url` 在配置层强制回环地址，HTTP 客户端 `trust_env=False`，避免代理把小说原文带离本机。
- 不使用云端 API、遥测或自动下载模型；模型缺失时明确提示用户执行 `ollama pull <模型ID>`。本项目实际使用 `modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest`，模型由 Ollama 管理在默认目录，不进入项目数据目录；清单、链接与使用方式见 [MODELS.md](MODELS.md)。
- 角色、片段、摘要分别使用独立 Prompt 版本（`stage2.v1.*`）与 Pydantic 结构校验；Ollama 请求带 `format=json`、`think=false`。
- JSON 解析失败时携带原输出与校验错误进行有限次修复重试（默认 2 次），仍失败则记录原始摘录并让本章失败，不写入半成品。

### 8.2 分析管道

- 单章分析同步执行：角色提取 → 片段分析 → 章节摘要，结果在同一事务中落库。
- 全书分析使用后台线程逐个章节执行，`GenerationJob` 记录进度、取消与错误；同一本书同时只允许一个分析任务。
- 启动时扫描 `running` 状态的任务与章节，标记为 `interrupted`，避免重启后永久卡住。
- 章节摘要会作为下一章的上下文传入；单章超过 `analysis.max_chapter_chars` 时明确报错并提示拆分，不静默截断原文。
- 片段必须与原文逐字对齐；AI 未覆盖的区间生成保底片段并保留原文，同时标记复核。对齐失败的 AI 片段丢弃并记录警告。
- 角色资料在未被用户锁定时，允许高置信度新章节刷新描述，修正早期低置信度偏差；用户锁定的角色完全跳过自动更新。
- 模型把同一关系正反各报一次时，同类型关系合并为一条并保留首次方向；方向语义不同的关系（如师徒）仍分别保留。

### 8.3 审核语义

- 以下情况片段进入 `needs_review`：说话人置信度低于阈值、说话人未知、片段类型/说话人歧义、情绪置信度低于阈值、系统回退片段。
- 说话人置信度与情绪置信度阈值分别为 `analysis.speaker_confidence_threshold`、`analysis.emotion_confidence_threshold`；别名候选阈值为 `analysis.alias_confidence_threshold`。
- 用户编辑片段或批量修改后，受影响字段写入 `user_locked_fields`；重跑只重建未锁定片段与字段，锁定片段保留原 ID 与人工内容。
- 别名候选在置信度不足或与其他角色冲突时不自动合并，必须人工确认；角色合并/拆分记录在 `merge_log`，便于追溯。
- Mock 模式同样遵守审核语义，但界面必须明确显示模拟模式，不能作为真实分析质量依据。

### 8.4 前端

- 工作区按「原文 / 片段审核 / 角色关系」分栏；桌面宽屏有右侧分析栏，窄屏通过「AI 分析」抽屉进入全书任务。
- 片段审核支持按状态/置信度筛选、逐条编辑与批量修改；角色面板支持资料编辑、锁定、合并、拆分和别名确认。
- 所有分析写操作通过 API 完成，前端不直接修改本地数据库或文件。

### 8.5 阶段 2 验收状态

- 自动化：后端 pytest 与前端 Vitest/构建通过。
- 真实模型：本机 Ollama 对测试小说完成 4/4 章分析，角色、别名、关系、摘要与片段均落库；真实模式下用户锁定字段重跑保留。
- 人工听感 gate 已于 2026-11-25 有条件通过（用户试听：音色差异 8/10、跨情绪 7/7/3、发音与杂音 6.5/10）；sad 表现力与句尾气声作为阶段 3 音质门槛处理，见 `docs/reports/STAGE3_AUDIO_QUALITY_PLAN.md`。
- 阶段 2 完成时尚余：阶段 0 的许可证核对与参考文本条件验证。阶段 3 的真实 TTS、音色库与音频资产随后已实现（见第 9 节）；导出仍未实现。
- 详细实测记录见 `docs/reports/STAGE2_REPORT.md`。

## 9. 阶段 3 实施口径

### 9.1 声音链路

- 情感源与最终音色分离：Qwen3-TTS VoiceDesign 按角色描述与情绪指令生成情感源，CosyVoice3 `inference_vc` 再转换到固定参考音。这样情绪变化不再直接改变角色身份。
- 音色设计一次产生多个候选（默认 3 个），只有用户绑定后才成为该 owner 的固定音色；候选与绑定状态分开存储。
- 参考音去齿只在**绑定候选**时执行，生成阶段不重复处理。阶段 3C 定稿参数为 4–10kHz 动态衰减、`threshold_ratio=0.24`、`full_ratio=0.62`、最多 `-5dB`；原始参考路径与 DSP 统计留在生成参数中。
- 单个引擎服务进程服务一个模型；应用侧用进程内锁串行化整条 Qwen + CosyVoice 链，避免同一 GPU 并发导致显存峰值叠加。
- Mock 模式返回确定性 WAV 与假数据，跳过真实引擎服务和 DSP；真实模式只允许两个服务 URL 指向回环地址。

### 9.2 音频资产与版本

- 阶段 3 复用初始 schema 的 `voice_profiles`、`audio_assets` 与片段音频字段，没有新增迁移。
- WAV 写入 `books/<book>/audio/<chapter>/<segment>/vNNN.wav`；SQLite 只保存相对路径、版本、校验和、Provider/模型与生成参数。
- 每次生成新增版本，不覆盖旧文件；回退只切换片段当前资产，并同步恢复该版本使用的音色档案。
- 生成结果先通过 RIFF/WAVE 解析校验再落库；失败时片段标记 `failed`，不会留下半成品当前版本。
- 修改台词、说话人、情绪、强度、演绎方式等影响音频的字段会使旧音频失效、要求重新生成；回退版本不能绕过该语义。

### 9.3 引擎服务协议与生命周期

- `workers/` 是正式的引擎服务目录：`services.yaml` 登记生产入口，两个子目录各自是独立 uv project（Python 3.12/torch cu128 与 Python 3.10/torch cu121），应用只通过回环 HTTP 调用，不导入其代码。`workers/experiments/` 归档阶段实验脚本，不属于服务协议。
- 两个引擎服务只监听 127.0.0.1：Qwen 8766 提供 `/health`、`/v1/generate`、`/v1/unload`；CosyVoice 8767 提供 `/health`、`/v1/vc`、`/v1/deess`、`/v1/unload`；两者都提供由控制器令牌保护的 `/v1/shutdown`。
- 音频以 Base64 JSON 输入、以音频字节输出，元数据放 `X-NVS-*` 响应头；服务不读取调用方路径，不执行任意文件访问，不自动下载模型。
- 统一控制命令为 `uv run novelvoice-studio worker list|status|start|stop|restart|logs`；`scripts/start_workers.ps1` 与 `stop_workers.ps1` 只是兼容入口。控制器从 `voice_design.worker_url` / `tts.worker_url` 解析端口，不重复配置。
- 状态和日志写入 `.run/workers`；状态同时记录启动器 PID 与 `/health` 回填的实际服务 PID。启动前检查 `/health` 与端口；停止时优先请求 `/v1/shutdown`（实例 ID 必须一致），`--force` 只在进程仍存活且命令行指向登记的 `server.py` 时结束进程，外部启动的服务只报告、不误杀。
- 模型按需加载，首次生成较慢；`--preload` 可提前加载，但会提前占用显存。服务环境与模型缓存不进入应用 venv，也不提交仓库。语音模型默认缓存到数据目录的 `cache/modelscope`；Ollama LLM 仍由 Ollama 管理在其默认目录（`%USERPROFILE%\.ollama\models`），不放进项目数据目录。

### 9.4 前端

- 「声音库」按书展示旁白与角色卡片、候选列表、绑定状态与参考音试听；「配音」页签按片段生成、试听、查看版本与回退。
- 前端不直接拼接文件系统路径；所有音频通过受控 API URL 获取，版本回退与绑定都经服务端校验。
- 未识别说话人或未绑定音色的片段禁止生成，并给出到声音库的入口；Mock 模式在页面明确标注。

### 9.5 阶段 3 验收状态

- 自动化：后端 pytest 102 passed；前端 Vitest 7 个文件 33 passed；`vue-tsc + vite build` 通过。
- Mock 浏览器走查：设计旁白/两个角色候选并绑定，生成旁白 v001/v002，回退 v001，改绑角色片段后生成成功。
- 真实引擎服务冒烟：Qwen 设计候选约 11.84 秒；绑定与参考去齿约 0.156 秒；Qwen + CosyVoice 单段生成约 31.9 秒，输出 24kHz 单声道 PCM WAV。
- 音质 gate 以阶段 3B/3C 复听记录为准；3B 通过，3C 接受 `refdeess + raw` 定稿，但“菜 / 三”擦音与句尾气声仍有残留。不能把应用功能验收等同于音质验收。
- 仍未完成：阶段 4 的批量队列、连续播放与 M4B 导出；阶段 0 的许可证核对（含 `ttsfrd` / `wetext`）与参考文本条件验证。
- 详细记录见 `docs/reports/STAGE3_REPORT.md`。

### 9.6 v0.5.0 生产链稳定化（2026-11-27）

- **全书设定集可续跑**：`start_book_bible` 在无显式选章时走 `_resume_bible`，挑覆盖最多且未跑满的历史版本作为底稿，只补缺失章节；任务进度以 `progress_base/progress_total` 为基线，重启或中断后不会回零。
- **单章失败隔离**：逐章抽取的 future 单独捕获异常，失败写入 `failed_chapters` 并计入 `failed_items`，其余章节继续；任务最终仍置 `completed`（数据库 CHECK 约束不允许 `completed_with_errors`），前端据此显示“部分完成（N 章待补）”。
- **提前收尾**：`POST /books/{id}/bible/analysis/finalize` 停止当前抽取，用已完成章节走 `finalize_only` 任务合并出可用版本；`stats` 增加 `covered_chapters` / `partial`，警告记录覆盖范围，版本状态为 `needs_review`，可直接进入选角。
- **提示词按需裁剪**：分场与剧本不再把全书角色注册表整表塞进 prompt；按本章原文姓名/别名命中过滤，只补主角/重要配角到 24 个，身份 60 字、音色 40 字、只留当前剧情时间的 1–2 条人物弧，整表预算 6000 字符；章节摘要与前后章摘要也加长度上限。人物归并改为按 payload 字符数打包（上限 5000 字符）并对每角色抽样 10 个章节，避免 122 章后 prompt 超过 12k 上下文触发 HTTP 400。
- **选角声线池**：旁白之外只为排名前 20 的角色单独设计声线，全书配音员上限 30；次要角色复用已有演员，要求性别/年龄兼容，且不得与已绑定角色同场景（无分场数据时按同章判断）出现。角色有多个年龄阶段时才建立阶段声线，基础声线复用对应阶段声线，避免同一角色多出一把嗓子。
- **引擎服务自动启动**：`scripts/server_control.py start`（`scripts/start.ps1` 调用）在健康检查通过后后台执行 `worker start`，日志写 `.run/workers/autostart.log`；`NOVELVOICE_SKIP_WORKERS=1` 可关闭，保留手工管理路径。
- 实施细节、实测数据与后续接手要点见 `docs/reports/STAGE4_PIPELINE_HARDENING_2026-11-27.md`。
