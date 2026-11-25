# v0.5.0 数据模型与迁移设计

- 日期：2026-11-26
- 基线版本：0.4.0
- 状态：P1 数据层已实施（迁移 `9f3c2a7b5d10`，测试通过）
- 关联文档：[AI 生产链重构](V0_5_0_AI_PRODUCTION_CHAIN_2026-11-26.md)

> 目标：在不删除原文、不删除旧 WAV、不破坏旧数据的前提下，新增 0.5.0 的
> 角色圣经、分场状态、有声剧本、全局配音员与异常收件箱数据结构，并把现有
> 角色、片段、音色和音频资产安全映射到新模型。

---

## 1. 迁移原则

1. **只增不删。** 0.5.0 不删除 `segments`、`voice_profiles`、`audio_assets` 和旧 WAV；
   旧表迁移后转为只读/兼容用途。
2. **原文不可变。** 新剧本只新增版本与单元，不回写小说原文。
3. **音频不进 SQLite。** 继续沿用文件路径 + 校验和；迁移只复制参考音，不移动旧文件。
4. **确定性 ID。** 迁移生成的主键使用 UUID5 派生值并加上现有 ID 前缀，保证重复执行不会产生重复数据。
5. **可审计。** 每次迁移前使用现有 `backup_sqlite()` 备份数据库；迁移结果写入报告。
6. **可回滚。** 以数据库备份恢复为主；旧表保留，WAV 不删除。0.5.0 不依赖
   Alembic downgrade 做自动回滚。
7. **安全边界不变。** 所有路径必须是数据目录下的相对路径；不接受用户传入任意
   本地路径；不读取数据目录之外的文件。

现有启动迁移已经具备自动备份：

- `src/novelvoice_studio/db/migrate.py::run_migrations()`
- 备份目录：`<data_dir>/backups/app-<timestamp>.db`
- Alembic 已启用 `render_as_batch=True`，适合 SQLite 表重建。

---

## 2. 现状摘要

| 表 | 现状用途 | 0.5.0 处理 |
|---|---|---|
| `books` | 书目、源文件、状态 | 增加圣经/生产状态字段；已有空书保留 |
| `chapters` | 章节、摘要、片段分析状态 | 增加分场/剧本状态；旧 `analysis_status` 保留 |
| `characters` | 逐章增量角色 | 增加角色层级、人物弧摘要、剧情时间字段 |
| `character_relationships` | 角色关系 | 保留；关系证据与置信度继续使用 |
| `segments` | 片段、说话人、情绪、音频状态 | 迁移为剧本单元；旧表保留，不再写入 |
| `audio_assets` | 片段音频版本 | 增加剧本单元/配音员关联；旧记录保留 |
| `voice_profiles` | 书内音色候选与绑定 | 选中的迁移为全局配音员 + 选角；旧表保留 |
| `generation_jobs` | 章节分析任务 | 扩展为通用 AI 管线任务 |
| `playback_states` | 未使用 | 0.6.0 播放器使用，本期不动 |

现有 `segments.source_start_offset` / `source_end_offset` 使用**全书归一化文本的绝对偏移**。
新 `script_units` 沿用同一语义，避免章节内/全书偏移混用。

---

## 3. 目标关系

```text
books
 ├─ chapters
 │   ├─ scenes (analysis_version)
 │   │   └─ scene_characters ── characters
 │   └─ script_versions
 │       └─ script_units ── audio_assets
 ├─ book_bibles
 │   └─ character_arcs ── characters
 └─ voice_casts ── voice_actors

review_items ──(多态)──> bible / character / scene / script_unit / voice_cast / job
```

层级约束：

- 一本书可有多个 `book_bibles` 版本，只有一个 `is_active=1`。
- 一章可有多个分场版本，`chapters.scene_version` 指向当前版本。
- 一章可有多个 `script_versions`，只有一个 `is_active=1`。
- 一个剧本单元只属于一个剧本版本；一个音频资产只属于一个片段或一个剧本单元。
- 一个配音员可被多本书、多个角色复用；一个选角记录只属于一本书的一个 owner。

---

## 4. 既有表变更

### 4.1 `books`

新增：

- `bible_status` `String(24)`，默认 `not_started`
  - 取值：`not_started` / `running` / `needs_review` / `approved` / `failed`
- `production_status` `String(24)`，默认 `not_started`
  - 取值：`not_started` / `analyzing` / `scripting` / `casting` / `needs_review` / `ready` / `generating` / `failed`
- `meta_json` `JSON`，默认 `{}`
  - 迁移标记、旧数据说明、最近一次方案摘要等非关系型元数据。

约束：

- 两个状态字段加 CheckConstraint。
- `status` 仍保留 `empty/ready/deleted` 语义，不合并。

迁移后：

- 有旧角色的书：`bible_status=approved`（`meta_json.legacy_migrated=true`）。
- 有旧剧本/音频的书：`production_status=ready`。
- 空书：`bible_status=not_started`、`production_status=not_started`。

### 4.2 `chapters`

新增：

- `scene_status` `String(24)`，默认 `not_started`
  - 取值：`not_started` / `running` / `completed` / `needs_review` / `failed`
- `scene_version` `Integer`，默认 `0`
  - 当前有效的分场版本；0 表示没有分场结果。
- `script_status` `String(24)`，默认 `not_started`
  - 取值：`not_started` / `running` / `completed` / `needs_review` / `failed`

保留：

- `analysis_status`、`summary`、`summary_json`、`analysis_meta_json` 继续用于旧数据与上下文。
- 新管线不再用 `analysis_status` 表示分场/剧本状态。

### 4.3 `characters`

新增：

- `role_tier` `String(16)`，默认 `unknown`
  - 取值：`protagonist` / `major` / `minor` / `extra` / `unknown`
- `arc_summary` `Text`，可空
- `story_time_start` `String(120)`，可空
- `story_time_end` `String(120)`，可空
- `story_order_start` `Integer`，可空
- `story_order_end` `Integer`，可空
- `source` `String(16)`，默认 `ai`
  - 取值：`ai` / `legacy` / `user`

说明：

- `age_range` 继续作为“基础年龄/默认年龄”，具体阶段由 `character_arcs` 和
  `scene_characters` 覆盖。
- `merged_into_id` 合并逻辑不变；合并后需同步停用旧选角并建立新选角。

### 4.4 `segments`（旧表，迁移后只读）

新增：

- `migrated_script_unit_id` `String(32)`，可空，普通索引
  - 指向迁移生成的 `script_units.id`；用于审计与音频映射。

迁移后：

- 新管线不新增/修改 `segments`。
- 旧审核页可保留只读查看，0.5.0 不再把它作为主要工作区。
- 旧 `audio_assets` 仍可通过 `segment_id` 追溯。

### 4.5 `audio_assets`

需要重建表（SQLite 用 Alembic batch）：

新增：

- `script_unit_id` `String(32)`，可空，FK `script_units.id` `ON DELETE SET NULL`
- `voice_actor_id` `String(32)`，可空，FK `voice_actors.id` `ON DELETE SET NULL`

变更：

- `segment_id` 改为可空。
- 保留 `file_path`、`checksum_sha256`、版本号、生成参数、`is_valid` 等字段。
- 增加 CheckConstraint：`segment_id IS NOT NULL OR script_unit_id IS NOT NULL`。

索引/唯一约束：

- 部分唯一索引 `(segment_id, version) WHERE segment_id IS NOT NULL`
- 部分唯一索引 `(script_unit_id, version) WHERE script_unit_id IS NOT NULL`
- `voice_actor_id` 普通索引。

迁移后：

- 旧音频行同时保留 `segment_id` 和新映射的 `script_unit_id`。
- 新生成音频只写 `script_unit_id`，`segment_id` 为空。
- 文件路径不变，不复制、不移动 WAV。

### 4.6 `voice_profiles`（旧表，迁移后只读）

新增：

- `migrated_voice_actor_id` `String(32)`，可空，唯一索引（部分索引，非空时唯一）

迁移后：

- 选中的档案迁移为 `voice_actors`，并建立 `voice_casts`。
- 未选中的候选保留在旧表，不进入全局池；后续可提供手动导入。
- 旧设计/试听接口保留兼容，但 0.5.0 新 UI 使用全局配音员库。

### 4.7 `generation_jobs`

新增：

- `stage` `String(24)`，可空
  - 例如 `chapter_digest` / `book_bible` / `scene_analysis` / `script_adaptation` / `voice_casting`
- `parent_job_id` `String(32)`，可空，普通索引
- `target_type` `String(24)`，可空
  - 例如 `book` / `chapter` / `scene` / `script_version` / `voice_actor`

说明：

- `job_type` 继续自由字符串；新增管线任务复用现有状态机。
- `recover_interrupted_analysis()` 泛化为 `recover_interrupted_jobs()`，按 `job_type`
  恢复/标记中断任务。
- 0.5.0 的音频生成仍以同步调用为主；批量队列在 0.6.0 接入。

---

## 5. 新增表

通用约定：

- 主键均为 `String(32)`；时间字段使用 `UTCDateTime`，`created_at`/`updated_at` 默认 `utc_now`。
- JSON 字段默认空列表/空字典，不使用 `NULL`。
- 所有多态引用不做数据库外键，由服务层校验。
- `source` 统一取值：`ai` / `legacy` / `user` / `system`。

### 5.1 `book_bibles`

全书角色圣经版本。

- `id` PK
- `book_id` FK `books.id` ON DELETE CASCADE，索引
- `version` Integer，非空
- `status` String(24)，默认 `draft`
  - `draft` / `running` / `needs_review` / `approved` / `superseded` / `failed`
- `source` String(16)，默认 `ai`
- `prompt_version` String(64)，可空
- `model` String(255)，可空
- `summary` Text，可空
- `content_json` JSON，默认 `{}`
  - 原始归一化角色/关系/世界观结果，便于审计与重建
- `timeline_json` JSON，默认 `{}`
  - 剧情时间线、叙事模式、关键事件
- `confidence` Float，默认 0.0
- `is_active` Boolean，默认 `false`
- `approved_at` UTCDateTime，可空
- `created_at` / `updated_at`

约束：

- `UNIQUE(book_id, version)`
- 部分唯一索引 `(book_id) WHERE is_active = 1`
- `confidence` 0–1。

### 5.2 `character_arcs`

角色在全书中的阶段/人物弧。

- `id` PK
- `book_id` FK `books.id` ON DELETE CASCADE，索引
- `bible_id` FK `book_bibles.id` ON DELETE CASCADE，索引
- `character_id` FK `characters.id` ON DELETE CASCADE，索引
- `arc_index` Integer，默认 0
- `label` String(120)，例如“幼年”“黑化后”
- `age_stage` String(24)，默认 `unknown`
  - 取值同现有年龄阶段：`child` / `teenager` / `young_adult` / `adult` / `middle_aged` / `elderly` / `unknown`
- `chapter_start_id` FK `chapters.id` ON DELETE SET NULL，可空
- `chapter_end_id` FK `chapters.id` ON DELETE SET NULL，可空
- `story_time_start` / `story_time_end` String(120)，可空
- `story_order_start` / `story_order_end` Integer，可空
- `summary` Text，可空
- `traits_json` JSON，默认 `[]`
- `appearance_json` JSON，默认 `{}`
- `voice_traits_json` JSON，默认 `{}`
- `confidence` Float，默认 0.0
- `evidence_json` JSON，默认 `[]`
- `source` String(16)，默认 `ai`
- `created_at` / `updated_at`

约束：

- `UNIQUE(bible_id, character_id, arc_index)`
- 索引 `(character_id, arc_index)`。

### 5.3 `scenes`

按分场版本保存场景，不覆盖旧版本。

- `id` PK
- `book_id` FK `books.id` ON DELETE CASCADE，索引
- `chapter_id` FK `chapters.id` ON DELETE CASCADE，索引
- `analysis_version` Integer，非空
- `scene_index` Integer，非空
- `title` String(255)，可空
- `summary` Text，可空
- `location` String(255)，可空
- `story_time_label` String(120)，可空
- `story_order` Integer，可空
- `time_mode` String(24)，默认 `present`
  - `present` / `flashback` / `flashforward` / `dream` / `unknown`
- `pov_character_id` FK `characters.id` ON DELETE SET NULL，可空
- `mood` String(64)，可空
- `source_start_offset` Integer，非空
- `source_end_offset` Integer，非空
- `analysis_status` String(24)，默认 `draft`
  - `draft` / `running` / `completed` / `needs_review` / `failed`
- `confidence` Float，默认 0.0
- `evidence_json` JSON，默认 `[]`
- `meta_json` JSON，默认 `{}`
- `source` String(16)，默认 `ai`
- `created_at` / `updated_at`

约束：

- `UNIQUE(chapter_id, analysis_version, scene_index)`
- 检查 `source_start_offset >= 0`、`source_end_offset > source_start_offset`。

### 5.4 `scene_characters`

场景内角色状态快照。

- `id` PK
- `book_id` FK `books.id` ON DELETE CASCADE，索引
- `scene_id` FK `scenes.id` ON DELETE CASCADE，索引
- `character_id` FK `characters.id` ON DELETE CASCADE，索引
- `state_phase` Integer，默认 0
  - 预留同一场景内状态变化；0.5.0 通常为 0
- `presence` String(16)，默认 `present`
  - `speaker` / `present` / `mentioned`
- `age_stage` String(24)，默认 `unknown`
- `physical_state` String(255)，可空
- `emotional_state` String(255)，可空
- `relationship_state` String(255)，可空
- `knowledge_state` Text，可空
- `disguise_state` String(255)，可空
- `voice_note` String(255)，可空
- `state_tags_json` JSON，默认 `[]`
- `confidence` Float，默认 0.0
- `evidence_json` JSON，默认 `[]`
- `source` String(16)，默认 `ai`
- `created_at` / `updated_at`

约束：

- `UNIQUE(scene_id, character_id, state_phase)`
- `confidence` 0–1。

说明：

- 0.5.0 每个角色每个场景通常一条；未来可在同一场景用多个 `state_phase` 表示情绪转折。
- `scene_characters` 是选角与表演指令的状态依据，不替代 `characters` 的全局身份。

### 5.5 `script_versions`

一章一个有声剧本版本。

- `id` PK
- `book_id` FK `books.id` ON DELETE CASCADE，索引
- `chapter_id` FK `chapters.id` ON DELETE CASCADE，索引
- `version` Integer，非空
- `status` String(24)，默认 `draft`
  - `draft` / `running` / `needs_review` / `approved` / `superseded` / `failed`
- `adaptation_level` String(16)，默认 `light`
  - `light` / `standard` / `strong` / `legacy`
- `source_scene_version` Integer，非空
- `source_hash` String(64)，可空
  - 章节原文 + 关键设置哈希，用于判断是否需要重跑
- `prompt_version` String(64)，可空
- `model` String(255)，可空
- `summary_json` JSON，默认 `{}`
  - 单元数、cue 数、改动统计、异常数
- `is_active` Boolean，默认 `false`
- `approved_at` UTCDateTime，可空
- `source` String(16)，默认 `ai`
- `created_at` / `updated_at`

约束：

- `UNIQUE(chapter_id, version)`
- 部分唯一索引 `(chapter_id) WHERE is_active = 1`。

### 5.6 `script_units`

有声剧本单元，也是 TTS 的生成单位。

- `id` PK
- `book_id` FK `books.id` ON DELETE CASCADE，索引
- `chapter_id` FK `chapters.id` ON DELETE CASCADE，索引
- `script_version_id` FK `script_versions.id` ON DELETE CASCADE，索引
- `scene_id` FK `scenes.id` ON DELETE SET NULL，可空，索引
- `unit_index` Integer，非空
- `unit_type` String(24)，非空
  - `narration` / `dialogue` / `inner_monologue` / `sfx` / `ambience` / `pause` / `unknown`
- `speaker_type` String(16)，默认 `unknown`
  - `narrator` / `character` / `unknown` / `none`
- `speaker_id` String(32)，可空，普通索引（逻辑引用 `characters.id`）
- `speaker_name_snapshot` String(120)，可空
- `character_state_id` FK `scene_characters.id` ON DELETE SET NULL，可空
- `voice_cast_id` FK `voice_casts.id` ON DELETE SET NULL，可空
- `source_text` Text，非空
- `text` Text，默认 `""`
  - 实际送入 TTS 的文本；cue/pause 为空
- `display_text` Text，可空
  - UI 展示文本；例如“惊堂木重拍（啪）”
- `source_start_offset` Integer，非空
- `source_end_offset` Integer，非空
- `emotion` String(32)，默认 `neutral`
- `intensity` Integer，默认 1
- `delivery` String(24)，默认 `normal`
- `tts_instruction` Text，可空
- `speed` Float，默认 1.0
- `pause_before_ms` Integer，默认 0
- `pause_after_ms` Integer，默认 250
- `sfx_json` JSON，默认 `{}`
  - `{label, description, timing, intensity}`
- `review_status` String(24)，默认 `auto`
  - `auto` / `needs_review` / `approved`
- `confidence` Float，默认 0.0
- `audio_status` String(24)，默认 `none`
  - `not_applicable` / `none` / `stale` / `queued` / `generating` / `ready` / `failed`
- `current_audio_asset_id` String(32)，可空
- `render_hash` String(64)，可空
  - TTS 相关字段 + 配音员参数的哈希；变化时旧音频标 stale
- `user_locked_fields_json` JSON，默认 `[]`
- `evidence_json` JSON，默认 `[]`
- `source` String(16)，默认 `ai`
- `created_at` / `updated_at`

约束/索引：

- `UNIQUE(script_version_id, unit_index)`
- 索引 `(script_version_id, unit_type)`
- 索引 `(book_id, audio_status)`
- CheckConstraint：
  - `unit_type` / `speaker_type` / `emotion` / `delivery` / `intensity` / `speed` /
    `review_status` / `audio_status` 取值合法；
  - TTS 单元（`narration` / `dialogue` / `inner_monologue`）`length(trim(text)) > 0`；
  - cue/pause 单元 `audio_status='not_applicable'`。

迁移旧数据时：

- `unit_type=unknown` 的旧片段保留为 `unknown`，并写入异常收件箱。
- 旧 `segment_type=unknown` 不强行猜成旁白。

### 5.7 `voice_actors`

全局配音员。

- `id` PK
- `display_name` String(120)，非空
- `description` Text，可空
- `gender_presentation` String(16)，默认 `unknown`
  - `male` / `female` / `neutral` / `unknown`
- `age_impression` String(24)，默认 `unknown`
  - 同年龄阶段枚举
- `language` String(16)，默认 `zh-CN`
- `timbre_tags_json` JSON，默认 `[]`
- `personality_tags_json` JSON，默认 `[]`
- `reference_text` Text，可空
- `reference_audio_path` Text，可空
  - 相对数据目录；固定为 `voice_actors/<id>/reference.wav`
- `instruct_text` Text，可空
- `seed` Integer，可空
- `provider` String(64)，可空
- `model` String(255)，可空
- `generation_params_json` JSON，默认 `{}`
- `sample_rate` Integer，可空
- `duration_ms` Integer，可空
- `status` String(16)，默认 `draft`
  - `draft` / `ready` / `failed` / `archived`
- `source` String(16)，默认 `ai`
  - `ai` / `legacy` / `user`
- `source_book_id` FK `books.id` ON DELETE SET NULL，可空
- `is_favorite` Boolean，默认 `false`
- `legacy_voice_profile_id` String(32)，可空，部分唯一索引
  - 迁移幂等与追溯；不要求旧表仍在
- `created_at` / `updated_at`

索引：

- `(status, language)`
- `(age_impression, gender_presentation)`
- `legacy_voice_profile_id` 非空时唯一。

说明：

- 参考音文件复制到全局目录，不移动旧书目录中的文件。
- 配音员删除采用归档；被选角或音频引用的配音员不得物理删除。

### 5.8 `voice_casts`

书/角色/阶段到配音员的选角。

- `id` PK
- `book_id` FK `books.id` ON DELETE CASCADE，索引
- `character_id` FK `characters.id` ON DELETE CASCADE，可空，索引
  - 旁白为 NULL
- `voice_actor_id` FK `voice_actors.id` ON DELETE RESTRICT，不可空，索引
- `scope` String(16)，非空
  - `narrator` / `character` / `character_stage`
- `age_stage_condition` String(24)，可空
  - `character_stage` 用；例如 `child` / `elderly`
- `state_condition` String(255)，可空
  - 预留特殊形态/伪装状态
- `priority` Integer，默认 0
- `source` String(16)，默认 `auto`
  - `auto` / `manual` / `legacy`
- `confidence` Float，默认 0.0
- `is_active` Boolean，默认 `true`
- `reason` Text，可空
  - 匹配理由/人工备注
- `legacy_voice_profile_id` String(32)，可空
- `created_at` / `updated_at`

约束：

- 部分唯一索引：
  - 旁白：`(book_id) WHERE scope='narrator' AND is_active=1`
  - 角色基础：`(book_id, character_id) WHERE scope='character' AND is_active=1`
  - 阶段：`(book_id, character_id, age_stage_condition) WHERE scope='character_stage' AND is_active=1`
- 检查：
  - `scope='narrator'` 时 `character_id IS NULL`；
  - `scope='character'` 时 `character_id IS NOT NULL` 且 `age_stage_condition IS NULL`；
  - `scope='character_stage'` 时 `character_id IS NOT NULL` 且 `age_stage_condition IS NOT NULL`。

说明：

- 旧选角通过 `legacy_voice_profile_id` 追溯。
- 换角不删除旧记录，置 `is_active=0`。
- 删除配音员受 RESTRICT 保护；先停用选角，再归档。

### 5.9 `review_items`

异常收件箱。

- `id` PK
- `book_id` FK `books.id` ON DELETE CASCADE，索引
- `entity_type` String(32)，非空
  - `book_bible` / `character` / `character_arc` / `scene` / `scene_character` /
    `script_version` / `script_unit` / `voice_actor` / `voice_cast` / `generation_job`
- `entity_id` String(32)，可空
- `code` String(64)，非空
  - 例如 `alias_conflict` / `speaker_unknown` / `source_span_mismatch` /
    `voice_missing` / `audio_failed` / `legacy_migration`
- `severity` String(16)，非空
  - `blocking` / `warning` / `info`
- `title` String(255)，非空
- `message` Text，可空
- `payload_json` JSON，默认 `{}`
- `status` String(16)，默认 `open`
  - `open` / `resolved` / `ignored`
- `source` String(16)，默认 `ai`
- `resolution_note` Text，可空
- `resolved_at` UTCDateTime，可空
- `created_at` / `updated_at`

索引：

- `(book_id, status, severity)`
- `(entity_type, entity_id)`
- 部分唯一索引 `(book_id, entity_type, entity_id, code) WHERE status='open'`
  - 防止同一实体的同一问题重复刷屏；实体 ID 为空时由服务层去重。

说明：

- 迁移旧数据时只为每本书创建一条 `legacy_migration` info，不把旧片段逐条变成异常。
- `blocking` 只阻塞对应片段/角色生成，不阻塞整本书。

---

## 6. 旧数据回填规则

### 6.1 全书圣经

迁移范围覆盖所有书籍，包括软删除书籍；软删除书的旧数据照常保留，恢复后可直接使用。
空书不生成圣经、场景或剧本。

对每本有角色的书：

1. 创建 `book_bibles` v1：
   - `status=approved`、`source=legacy`、`is_active=1`；
   - `content_json` 写入旧角色、关系、来源说明；
   - `timeline_json` 为空，`confidence=1.0` 仅表示“结构完整”，不表示 AI 质量。
2. 为每个旧角色创建 `character_arcs`：
   - `arc_index=0`、`label="未分阶段"`；
   - `age_stage=character.age_range`；
   - `chapter_start_id=first_chapter_id`；
   - `voice_traits_json` 取 `voice_description`；
   - `source=legacy`。
3. `books.bible_status=approved`，`meta_json.legacy_migrated=true`。
4. 没有角色的书不创建圣经，状态保持 `not_started`。

### 6.2 分场

对每一章：

- 有旧片段：
  - 创建 `scenes`，`analysis_version=1`、`scene_index=0`、覆盖整章；
  - `analysis_status=completed`、`source=legacy`、`confidence=1.0`；
  - `chapters.scene_version=1`、`scene_status=completed`。
- 无旧片段：
  - `chapters.scene_version=0`、`scene_status=not_started`。

为每个出现过的说话人创建 `scene_characters`：

- `presence=speaker`；
- `age_stage` 取角色基础年龄；
- `emotional_state` 取该角色在片段中最常见的情绪，仅作旧数据展示；
- `confidence` 取说话人置信度均值/最低值；
- `source=legacy`；
- `state_tags_json` 保留来源说明，例如 `["legacy_approximation"]`。

### 6.3 有声剧本

对每一章有旧片段的章节：

1. 创建 `script_versions` v1：
   - `status=approved`、`adaptation_level=legacy`、`source=legacy`；
   - `source_scene_version=1`、`is_active=1`；
   - `source_hash` 计算章节原文哈希；
   - `summary_json` 写入旧片段数、映射说明。
2. 按 `segment_index` 顺序创建 `script_units`：
   - `unit_index` 重新从 0 连续编号；
   - `unit_type=segment.segment_type`；`unknown` 保留；
   - `speaker_type=segment.speaker_type`；`speaker_id`、名称快照照抄；
   - `source_text=segment.source_text`；
   - `text=segment.speech_text`；`display_text=segment.speech_text or segment.source_text`；
   - `source_start_offset` / `source_end_offset` 照抄；
   - 情绪、强度、演绎、指令、语速、停顿、锁定字段、证据照抄；
   - `review_status=segment.review_status`；
   - `audio_status=segment.audio_status`；
   - `current_audio_asset_id=segment.current_audio_asset_id`；
   - `source=legacy`。
3. 迁移健壮性：
   - 旧 `speech_text` 为空时，用 `source_text` 回填，并把单元置为 `needs_review`，不得让迁移失败；
   - 旧片段缺少偏移时，按所在段落/章节范围回填，并置为 `needs_review`；
   - 无法确定偏移的片段跳过剧本单元创建，写迁移 warning，不删除旧片段；
   - 旧 `unknown` 单元保留原类型并写 warning。
4. `chapters.script_status=completed`。
4. 不把旧 `needs_review` 逐条转成异常；保留单元状态即可。

### 6.4 配音员与选角

默认只迁移 `voice_profiles` 中 `is_selected=1` 且 `status='ready'` 的档案：

1. 创建 `voice_actors`：
   - `display_name=profile.name`；
   - 描述、参考文本、指令、seed、参数、采样率、时长照抄；
   - `status=ready`、`source=legacy`；
   - `source_book_id` 由 owner 推导：
     - narrator：`owner_id` 即书籍 ID；
     - character：查询 `characters.book_id`；
   - `legacy_voice_profile_id=profile.id`；
   - 参考音复制到 `voice_actors/<new_id>/reference.wav`，校验和写入元数据。
2. 创建 `voice_casts`：
   - narrator：`scope=narrator`、`character_id=NULL`；
   - character：`scope=character`、`character_id=owner_id`；
   - `source=legacy`、`is_active=1`、`confidence=1.0`；
   - `legacy_voice_profile_id=profile.id`。
3. 回写 `voice_profiles.migrated_voice_actor_id`。
4. 同一 owner 有多个 selected 档案时：
   - 选 `updated_at` 最新且 `quality_score` 最高者作为 active；
   - 其余也创建演员，但选角 `is_active=0`，并写 warning 供审计。
5. 参考音缺失或无法读取时：
   - 不创建 active 选角，避免生成阶段才失败；
   - 跳过该档案并写 `voice_reference_missing` warning 到迁移报告；
   - 保留旧 `voice_profiles` 行，便于人工修复后手动导入。

未选中的候选不进入全局池，避免把每本书的试音候选都变成正式配音员。

### 6.5 音频资产

对每个旧 `audio_assets`：

- 保持 `id`、`file_path`、`version`、校验和、参数不变；
- 通过 `segments.migrated_script_unit_id` 填 `script_unit_id`；
- 通过该单元的选角填 `voice_actor_id`；找不到则留空；
- `segment_id` 保留；
- 校验文件存在与 SHA-256；缺失/不一致写入迁移报告，不删除记录。

### 6.6 迁移报告

生成一份 JSON/Markdown 报告，至少包含：

- 迁移前后各表行数；
- 创建/跳过的圣经、场景、剧本版本、单元、配音员、选角数量；
- 音频文件存在/缺失/校验失败数量；
- 冲突与跳过原因；
- 迁移耗时与 Alembic 版本。

报告写入 `<data_dir>/logs/migration-v05-<timestamp>.json`，不写入数据库。

---

## 7. 确定性 ID 与幂等

迁移使用 UUID5（`uuid.NAMESPACE_URL`，名称空间字符串 `nvs05:<key>`）派生
ID。为避免破坏现有 `validate_id` 规则（`^[a-z]{2,8}_[0-9a-f]{16}$`），
取 UUID5 十六进制前 16 位并加上表前缀，例如 `bible_3f2a...`。

| 对象 | 前缀 | 名称空间 key |
|---|---|---|
| 圣经 | `bible_` | `book:{book_id}:1` |
| 角色弧 | `arc_` | `{book_id}:{character_id}:0` |
| 场景 | `scene_` | `{chapter_id}:1:0` |
| 场景角色 | `schar_` | `{scene_id}:{character_id}:0` |
| 配音员 | `va_` | `profile:{profile_id}` |
| 选角 | `cast_` | `profile:{profile_id}` |
| 剧本版本 | `sv_` | `{chapter_id}:1` |
| 剧本单元 | `su_` | `segment:{segment_id}` |
| 迁移提示 | `rv_` | `legacy:{book_id}` |

规则：

- 迁移开始前先查确定性 ID；存在则更新/跳过，不重复插入。
- 音频资产沿用原 ID，只做字段更新。
- 迁移脚本可重复执行；重复执行的行数应保持不变。
- 未来运行时新建对象仍使用现有 ID 生成器，不要求 UUID5。

---

## 8. 迁移步骤

### M0 预检

- 确认数据目录、数据库文件、Alembic 当前版本。
- 统计旧表行数、软删除书籍数、空书数。
- 检查 `source_file_path`、WAV/参考音是否存在。
- 检查磁盘空间：至少为数据库大小 + 参考音复制预留空间。
- 调用 `backup_sqlite()` 生成备份；记录备份路径。
- 只读审计命令输出 dry-run 报告；不写业务表。

### M1 建表与改列

- 新增 9 张表。
- 修改 `books`、`chapters`、`characters`、`segments`、`voice_profiles`、`generation_jobs`。
- 重建 `audio_assets`，保留数据与索引。
- 创建约束与部分唯一索引。
- 使用 Alembic batch 模式；失败则整个迁移回滚并保留备份。

### M2 圣经与角色弧

- 按 6.1 回填。
- 更新 `books.bible_status` 与 `meta_json`。

### M3 分场与状态

- 按 6.2 回填。
- 更新 `chapters.scene_version` / `scene_status`。
- 为旧说话人创建状态快照。

### M4 剧本与单元

- 按 6.3 回填。
- 回写 `segments.migrated_script_unit_id`。
- 更新 `chapters.script_status`。

### M5 配音员与选角

- 按 6.4 回填。
- 复制参考音，校验和。
- 回写 `voice_profiles.migrated_voice_actor_id`。

### M6 音频映射

- 按 6.5 更新 `audio_assets`。
- 校验所有旧 WAV 仍存在且校验和一致。
- 输出缺失/失败清单。

### M7 迁移标记与收件箱

- 每本成功迁移的书创建一条 `legacy_migration` info：
  - 标题：“旧版数据已迁移”
  - 消息：“角色、片段和音色已映射到 0.5.0 模型；建议重新生成角色圣经与有声剧本。”
- 不逐条迁移旧 `needs_review`。

### M8 验证与切换

- 执行第 9 节验证查询。
- 将成功迁移且有剧本的书 `production_status` 设为 `ready`。
- 新代码读写新表；旧表只读。
- 启动日志输出版本、迁移耗时、报告路径。

---

## 9. 迁移验证

### 9.1 数量

- `books` / `chapters` / `characters` / `segments` / `audio_assets` 行数在迁移前后一致。
- `script_units` 数 = 有迁移价值（旧片段）的数量。
- `audio_assets` 中 `segment_id` 非空行数不变。
- 参考音复制数量 = 迁移的选中 `voice_profiles` 数量。

### 9.2 完整性

```sql
-- 没有任何归属的音频资产
SELECT COUNT(*) FROM audio_assets
WHERE segment_id IS NULL AND script_unit_id IS NULL;

-- 没有版本归属的剧本单元
SELECT COUNT(*) FROM script_units u
LEFT JOIN script_versions v ON v.id = u.script_version_id
WHERE v.id IS NULL;

-- 选角指向不存在或未就绪的配音员
SELECT COUNT(*) FROM voice_casts c
JOIN voice_actors a ON a.id = c.voice_actor_id
WHERE c.is_active = 1 AND a.status != 'ready';

-- 激活版本重复（应为 0）
SELECT book_id, COUNT(*) FROM book_bibles
WHERE is_active = 1 GROUP BY book_id HAVING COUNT(*) > 1;

SELECT chapter_id, COUNT(*) FROM script_versions
WHERE is_active = 1 GROUP BY chapter_id HAVING COUNT(*) > 1;
```

### 9.3 文件校验

- 旧 WAV：路径存在、大小与 `file_size_bytes` 一致、SHA-256 一致。
- 参考音：新全局路径存在、校验和与旧文件一致。
- 不修改、不移动、不删除旧文件。

### 9.4 幂等

- 在同一备份上重复执行迁移回填逻辑，所有行数不变。
- 重复执行不产生重复圣经、场景、剧本单元、配音员、选角。

---

## 10. 回滚

1. 停止应用。
2. 用 `<data_dir>/backups/app-<timestamp>.db` 覆盖 `app.db`。
3. 保留新建的 `voice_actors/` 参考音副本；它们未被旧版本引用，不影响旧版本。
4. 旧 WAV、旧书目录、旧表均未被删除，旧版本可直接启动。
5. 0.5.0 不提供自动 Alembic downgrade；如必须降级，按备份恢复是唯一受支持路径。

迁移失败时：

- 应用不得以“部分迁移成功”状态启动。
- 记录错误、报告路径和备份路径。
- 用户恢复备份后重新启动。

---

## 11. 接口与兼容

### 11.1 新接口

- `POST /api/v1/books/{book_id}/bible`：启动全书圣经任务。
- `GET /api/v1/books/{book_id}/bible`：读取当前/指定版本圣经、角色弧、时间线。
- `PATCH /api/v1/books/{book_id}/bible`：编辑已确认字段（写锁定）。
- `POST /api/v1/chapters/{chapter_id}/scenes`：启动分场分析。
- `GET /api/v1/chapters/{chapter_id}/scenes`：读取分场与角色状态。
- `POST /api/v1/chapters/{chapter_id}/script`：生成有声剧本版本。
- `GET /api/v1/chapters/{chapter_id}/script/versions`：版本列表。
- `POST /api/v1/script-versions/{version_id}/approve`：设为 active。
- `POST /api/v1/script-versions/{version_id}/rollback`：回退。
- `GET /api/v1/voice-actors`：全局配音员库。
- `POST /api/v1/voice-actors/generate`：生成配音员。
- `POST /api/v1/books/{book_id}/casting/auto`：自动选角。
- `PATCH /api/v1/voice-casts/{cast_id}`：人工调整。
- `GET /api/v1/books/{book_id}/review-items`：异常列表。
- `POST /api/v1/review-items/{item_id}/resolve`：解决/忽略。
- `POST /api/v1/books/{book_id}/production-plan/approve`：接受方案。
- `POST /api/v1/script-units/{unit_id}/generate`：单段生成。

### 11.2 兼容接口

- 旧 `segments`、`voice_profiles` 相关接口保留，标记 deprecated。
- 旧 `/analysis` 章节分析接口保留到 0.6.0，避免旧测试立即失效。
- 新 UI 不再调用旧片段审核与按角色确认接口。
- 旧数据迁移后，旧接口返回只读结果；不保证新字段。

### 11.3 删除接口

- 0.5.0 不删除旧表、不删除旧 WAV、不删除旧书目录。
- 删除书籍仍为软删除；新数据随 `ON DELETE CASCADE` 清理，但文件保留在回收站策略中。

---

## 12. 测试计划

### 12.1 模型与约束

- 新表可创建，CheckConstraint 生效。
- 部分唯一索引阻止重复 active 圣经/剧本/选角。
- `audio_assets` 至少有一个归属。
- TTS 单元必须非空文本；cue 单元 `audio_status=not_applicable`。

### 12.2 迁移夹具

准备包含以下数据的临时库：

- 1 本正常书、1 本空书、1 本软删除书；
- 2 章、3 个片段（含 1 个 unknown）；
- 2 个角色、1 条关系；
- 2 个 voice_profiles（1 个选中、1 个未选中）；
- 2 个音频资产，文件校验和已知。

断言：

- 迁移前后旧表行数一致；
- 圣经/角色弧/场景/状态/剧本/单元/配音员/选角数量符合预期；
- 音频资产映射正确，WAV 校验和不变；
- 未选中音色不进入全局池；
- 重复执行不增加行数；
- 迁移报告生成。

### 12.3 API

- 新接口返回 0.5.0 结构；旧接口仍可用。
- 锁定字段重跑不被覆盖。
- 异常项解决/忽略后不再出现在 open 列表。

### 12.4 UI

- 旧书显示“旧版数据”标记与重新生成入口。
- 空书显示导入/删除。
- 配音员库跨书复用计数正确。

---

## 13. 待定项

以下不属于 0.5.0 阻塞项，实施时再定：

- `segments` / `voice_profiles` 的最终下线版本（建议 0.6.0 之后再评估）。
- 配音员去重策略（标签 + 参考音指纹 + 人工合并）。
- 同一场景内多状态转折是否启用 `state_phase>0`。
- 角色圣经是否引入向量检索；0.5.0 先用结构化标签与文本匹配。
- 音效 cue 在 0.7.0 的实际素材接入方式。

---

## 14. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-26 | 初版：新增圣经、角色弧、场景、状态、剧本、配音员、选角、异常表；定义旧数据回填与幂等迁移 |
| 2026-11-26 | P1 实施：Alembic 迁移 `9f3c2a7b5d10`、回填模块 `db/v05_backfill.py`、迁移测试 `tests/test_v05_migration.py`；确定性 ID 调整为「前缀 + UUID5 前 16 位」 |

### 14.1 实施核对

- 迁移文件：`migrations/versions/20261126_9f3c2a7b5d10_v0_5_0_pipeline_schema.py`
- 回填模块：`src/novelvoice_studio/db/v05_backfill.py`
- 测试：`tests/test_v05_migration.py`
  - 正常书、空书、软删除书；
  - 角色合并、缺失参考音、未选中音色；
  - 剧本单元回退偏移与异常审核；
  - 音频资产/配音员映射、WAV 原文件保留；
  - 重复回填不增加行数；
  - downgrade 恢复旧表结构且不删除 WAV。
- 真实数据副本验证：软删除书可迁移，旧 `audio_assets` 5 条全部映射，迁移期间无警告。
- 已知偏差：设计稿原写“UUID5 32 位”，实际为兼容 `validate_id` 改为
  “前缀 + UUID5 十六进制前 16 位”。