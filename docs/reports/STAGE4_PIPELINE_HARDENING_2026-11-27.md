# v0.5.0 生产链稳定化报告（2026-11-27）

本报告记录 2026-11-26 至 11-27 期间，用真实 557 章小说跑通「设定集 → 分场 → 剧本 → 选角」时暴露的问题与修复。结论都以本机真实任务为准，不是 Mock 推演。

## 1. 测试对象

| 项 | 值 |
| --- | --- |
| 书籍 | 《大明：朕的太子一家不对劲》 |
| book_id | `book_ad2f133288244e35` |
| 规模 | 557 章、平均 3422 字、合计约 190 万字 |
| LLM | `modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest`（Ollama，Q4_K_M，100% GPU，ctx 12288） |
| 语音引擎 | Qwen3-TTS VoiceDesign `127.0.0.1:8766` + CosyVoice3 `127.0.0.1:8767` |
| 硬件 | RTX 4070 Ti 12GB |

## 2. 问题与修复

### 2.1 设定集全有或全无、重跑归零

**现象**：任务跑到 102/557 时单章结构校验失败，整单 `failed`；重新生成从 0 开始，已完成的 102 章白跑。

**修复**：

- `start_book_bible` 无显式选章时调用 `_resume_bible()`，在历史版本里挑「覆盖章节最多且未跑满」的版本作为底稿，只补缺失章节，进度以 `progress_base/progress_total` 为基线。
- `_run_job` 对每章 future 单独 try/except：失败章节写 `failed_chapters`、`failed_items += 1`，其余章节继续；最终任务仍为 `completed`（`generation_jobs.status` 的 CHECK 约束不接受 `completed_with_errors`），前端显示「部分完成（N 章待补）」。
- 新增 `POST /books/{book_id}/bible/analysis/finalize` 与 `finalize_partial()/_run_finalize_job()`：停止抽取，用已完成章节合并出 `finalize_only` 版本，`stats` 带 `covered_chapters/partial`，状态 `needs_review`，选角立即可用。

**实测**：102 → 108 → 114 三次重启续跑均不回零；在 122 章提前收尾生成 v8，146 角色 / 190 段人物弧 / 174 条关系，`needs_review`，选角解锁。

### 2.2 小模型逐章 JSON 复读

- Ollama 侧新增 `num_predict=6144`、`repeat_penalty=1.12`、`repeat_last_n=256`；遇到 `prediction aborted, token repeat limit reached` 自动换 seed 重试（最多 2 次）。
- 逐章抽取改为 `ThreadPoolExecutor` 批处理（LLM 在线程、DB 写入回主线程按序）；`jobs.bible_parallel_chapters=2`，`OLLAMA_NUM_PARALLEL=2`。
- 结果：约 20–30 秒/章，显存 7.3–8.3GB。

### 2.3 设定集合并 prompt 超上下文（122 章时）

**现象**：`角色圣经归并批次失败：HTTP 400 prompt is longer than the context length`。

**修复**：

- 每角色候选只抽样 10 个章节（等距采样，`mention_chapters` 仍保留真实次数）。
- 合并批次按 payload 字符数打包（`MERGE_BATCH_CHARS=5000`），不再固定 20 个角色一批。

### 2.4 分场/剧本 prompt 超上下文

**现象**：第1章分场直接 HTTP 400。原因是 prompt 带了全书 146 个角色的完整注册表（身份、音色、每条人物弧 voice_hint），122 章时有 4 万多字符。

**修复**：

- `scenes.py::_known_payload` / `scripts.py::_load_characters`：按本章原文姓名/别名命中过滤，不足 8 个再补主角/重要配角到 24 个；字段压缩为身份 60 字、音色 40 字、只留覆盖当前剧情时间的 1–2 条人物弧，整表预算 `KNOWN_CHARACTER_CHARS=6000`。
- 本章摘要限 1200 字，前后章摘要各 500 字。
- **实测**：第1章分场 20–40 秒成功（1 场景、4 个角色、带证据引文）；前言与第2章均成功；第1章剧本生成 149 个单元正常。

### 2.5 自动选角给每个角色生成声线

**现象**：142 个目标各自调用 VoiceDesign，约 40 秒/角色，一本书要 1.5 小时以上，声线数量远超电视剧实际用人数。

**修复（声线池）**：

- 旁白之外只为排名前 `MAX_DEDICATED_VOICES = 20` 的角色单独设计声线；全书配音员上限 `MAX_VOICE_ACTORS_PER_BOOK = 30`。
- 次要角色复用已有演员：要求 `_score_actor` 性别/年龄兼容，且不得与已绑定角色同场景出现（有分场数据按 `SceneCharacter` 判断，没有则按同一章判断）。
- 角色存在多个年龄阶段时才生成阶段声线；基础声线（`scope=character`）在阶段声线之后复用对应阶段演员，避免同一角色多出一把嗓子。

**实测**：

- 清理前：143 个目标、143 个独立声线。
- 清理后重跑：114 个待选目标，`generated=0`、`reused=114`、0 失败，3.8 秒完成。
- 结果：88 个需要出声的角色共用 39 条声线；其中主角年龄阶段声线占增量；按章节核验共用声线的角色同章冲突为 0。

### 2.6 语音引擎未启动导致选角失败

**现象**：先报 8766 未启动，补起后又被 8767 拦住（自动选角要「设计 → 去齿」两步）。

**修复**：

- `scripts/server_control.py start` 在后端健康检查通过后，后台执行 `worker start`（两个服务一起），日志写 `.run/workers/autostart.log`；`ALREADY_RUNNING` 分支也会补启动。
- `NOVELVOICE_SKIP_WORKERS=1` 可关闭自动启动。

## 3. 改动文件

| 文件 | 内容 |
| --- | --- |
| `src/novelvoice_studio/ai/ollama.py` | options、复读换 seed 重试、结构化输出 |
| `src/novelvoice_studio/ai/registry.py` | 传递 LLM 新参数 |
| `src/novelvoice_studio/config.py` | `llm.num_predict/repeat_penalty/repeat_last_n`、`jobs.bible_parallel_chapters` |
| `src/novelvoice_studio/services/bible.py` | 并发抽取、失败隔离、断点续跑、提前收尾、合并分批、partial 统计 |
| `src/novelvoice_studio/api/v1/bible.py` | finalize 路由 |
| `src/novelvoice_studio/services/scenes.py` | 角色表按章裁剪、摘要限长、窗口生成 |
| `src/novelvoice_studio/services/scripts.py` | 角色表按章裁剪、字段压缩 |
| `src/novelvoice_studio/services/casting.py` | 声线池、场景/章节冲突判断、基础声线与阶段声线合并 |
| `scripts/server_control.py` | 引擎服务自动启动 |
| `frontend/src/api/client.ts` | `finalizeBible` |
| `frontend/src/components/production/BiblePanel.vue` | 「先收尾出可用版」「继续生成」「部分完成」文案 |
| `tests/test_bible_api.py` | 单章失败隔离 + 断点续跑回归 |

`config/config.yaml` 为本地配置（gitignored），关键值：`context_length: 12288`、`num_predict: 6144`、`jobs.bible_parallel_chapters: 2`。

## 4. 验证

- 后端：`uv run pytest` 全量通过（含新增回归 `test_book_bible_chapter_failure_is_isolated_and_resumable`）。
- 前端：`npm run test -- --run` 38 passed；`npm run build` 通过。
- 真实链路：设定集提前收尾 → 选角解锁 → 分场/剧本单章真实生成 → 自动选角复用 114 个目标。

## 5. 已知限制

1. 设定集「提前收尾」后，版本只覆盖已完成章节，UI 会显示 `部分完成（已覆盖 X/Y 章）`；后续补章需要再点「继续生成」。
2. 共用声线的冲突判断在没有分场数据的章节只能按「同一章」近似，比真实同场更严格；这会导致少量声线无法复用，属于安全侧偏差。
3. 39 条声线高于 30 的预算，原因是主角童年/少年/成年阶段必须分开；若后续要压到 30 以内，只能减少主要角色数量或接受跨阶段共用。
4. 自动选角没有逐目标断点，只是「已有绑定则跳过」，中途取消后重跑会跳过已完成角色，但进度条会显示本轮目标数。
5. 归并/时间线的部分失败只写 warning，不会阻断版本；出现 `HTTP 400` 警告时应检查是否又引入过大的 prompt。

## 6. 后续接手要点

- 数据目录：`C:\Users\guyue\Documents\NovelVoiceStudio`（`app.db` + `voice_actors/`）。
- 服务：`python scripts\server_control.py start|stop|status`；前端 `cd frontend; npm run build`；引擎 `uv run novelvoice-studio worker status|logs`。
- 当前书籍：`book_ad2f133288244e35`，active bible 为 v8（122/557 章，`needs_review`），143 条 cast、39 个配音员。
- 继续生成设定集会从第 123 章补，不会重跑前 122 章。
- 不要回退 `KNOWN_CHARACTER_CHARS` 裁剪逻辑，否则 122 章以上必然再次 400。
- 选角常量在 `services/casting.py` 顶部：`MAX_DEDICATED_VOICES`、`MAX_VOICE_ACTORS_PER_BOOK`。

