# P7 选中单元音频生成与试听实施报告

日期：2026-11-26
范围：v0.5.0 AI 生产链重构 P7
状态：已完成，等待完整流程评审；发布版本仍为 0.4.0

## 1. 目标与结论

P7 把“有声剧本”推进到可直接听的结果：在制作台「剧本」页，为选中的旁白、对白或内心独白单元同步生成 WAV，在浏览器内试听，并按单元保留版本与切回。

本次完成后，v0.5.0 主链 P1–P7 已可串起完整 Mock 流程：

```text
导入 TXT → 全书角色圣经 → 逐章分场 → 有声剧本 → 自动/手动选角
→ 选中剧本单元生成音频 → 直接试听 → 查看版本并切回
```

关键边界：

- 只对 `narration` / `dialogue` / `inner_monologue` 生成语音；`sfx` / `ambience` / `pause` 是 cue，不进入 TTS。
- 文本、情绪、强度、表演方式、语速或当前配音员/参考音变化后，已有音频标记 `stale`，旧版本仍保留并可试听。
- 音频仍按 `vNNN.wav` 落在数据目录，二进制不入 SQLite。
- 失败、缺声线或参考音不可用会进入异常收件箱；重试成功后自动收起对应异常。
- P7 不新增数据库迁移，数据库 head 仍为 `a4c1e8d29b73`。

## 2. 后端

### 2.1 API

新增路由：

- `POST /api/v1/script-units/{unit_id}/generate`：同步生成一条单元音频，返回当前资产与状态。
- `GET /api/v1/script-units/{unit_id}/audio-versions`：按版本倒序列出该单元历史音频。
- `GET /api/v1/script-units/{unit_id}/audio`：以内联 WAV 播放当前版本，`Cache-Control: no-store`。
- `POST /api/v1/script-units/{unit_id}/audio-versions/{asset_id}/restore`：切回指定历史版本。

剧本详情同时补充 `voice_actor_id` / `voice_actor_name`，前端无需逐单元再查声线；制作概览的 `audio_ready_units` / `audio_stale_units` 与 `next_action` 会反映真实音频进度。

### 2.2 生成语义

`UnitAudioService` 只接受当前有效剧本版本中的单元；历史版本返回 `409 script_version_inactive`。生成前按当前选角解析声线：

- 旁白使用 `narrator` 绑定；角色优先使用单元已绑定且匹配的 `character_stage`，否则回退到角色基础声线。
- 没有可用选角：`409 unit_voice_missing`。
- 配音员非 `ready`：`409 actor_not_ready`；参考音路径为空或文件缺失：`409 actor_reference_missing`，同时写入 `audio_voice_missing` 异常。
- cue 单元：`422 audio_cue_not_applicable`；空文本或其他非 TTS 类型同样拒绝。

生成与旧片段生成共用 `TTS_LOCK`，避免真实模式下单 GPU 并发抢占。

### 2.3 输入指纹与 stale

`render_hash_for` 对以下字段做稳定 SHA-256 指纹：

- 单元：`id`、`text`、`emotion`、`intensity`、`delivery`、`tts_instruction`、`speed`；
- 配音员：`id`、参考音路径、描述、`instruct_text`、`seed`、`language`、`status`。

读取剧本或制作概览时，已生成单元的指纹若与当前输入不一致，会被标为 `stale`。切回旧版本时，只有该版本记录的指纹与当前输入一致才恢复 `ready`，否则保持 `stale`，避免“切回即视为可用”的误判。

### 2.4 版本、文件与异常

- 版本号按单元内最大 `AudioAsset.version + 1` 递增，路径为 `books/<book>/audio/units/<unit>/vNNN.wav`。
- 新资产记录采样率、声道、位深、时长、大小、校验和、Provider/模型与生成参数；`current_audio_asset_id` 指向当前版本。
- WAV 落盘前校验 RIFF 与 `soundfile` 可解析性，空音频或非 WAV 返回 `502 invalid_audio`。
- 非 `AppError` 的生成失败统一转为 `502 unit_audio_failed`，单元置 `failed`，并写入 `audio_generation_failed` 警告异常；成功生成会关闭该单元的音频失败/缺声线异常。
- 旧片段音频资产继续由既有 `segment_id` 链路服务；单元资产通过泛化后的音频文件接口读取，不改旧数据。

## 3. 前端

- `ScriptPanel` 增加音频状态标签、声线名称与“未分配声线”提示。
- 单元级操作：`生成音频` / `重新生成` / `重试`；生成成功后单元内出现原生 `<audio>` 播放器，可直接试听。
- 章节头部显示“已生成 / 可配音总数”“待生成”“待重生成”“未选角”，并增加“待生成音频”筛选。
- `UnitAudioVersionsModal` 列出 `vNNN` 版本、时长、采样率、大小、声线、Provider/模型与时间；支持逐版本试听和“切回此版”，切回 stale 时给出明确提示。
- 生成或切回后刷新剧本、制作概览与章节音频进度；输入变化导致的 stale 会随剧本读取自动出现。
- 音效、环境与停顿单元不显示生成按钮，只显示 cue 内容和停顿信息。

## 4. 验证

自动化：

- 后端：`uv run pytest -q` → **157 passed**。
- 前端：`npm test -- --run` → **34 passed / 7 files**。
- 构建：`npm run build`（`vue-tsc --noEmit && vite build`）通过。

P7 专项测试 `tests/test_unit_audio_api.py` 覆盖 5 组场景：

1. 生成 → 流式播放 → 列表 → 再生成 v2 → 切回 v1 → 制作台计数更新。
2. 修改单元文本/情绪、更换配音员描述后标记 stale；切回旧指纹保持 stale，重生成后恢复 ready。
3. 缺选角返回 `unit_voice_missing`；cue 返回 `audio_cue_not_applicable`；未生成音频返回 404。
4. TTS 失败返回 `502 unit_audio_failed`，生成 `audio_generation_failed` 异常；重试成功自动关闭异常。
5. 配音员归档返回 `actor_not_ready`，写入 `audio_voice_missing`；恢复 ready 并生成成功后异常关闭。

Mock 手动冒烟（本机 `127.0.0.1:8765`，示例小说《雾港来信》4 章、45 个可配音单元、4 个配音绑定）：

- API 为首章旁白生成音频：返回 `audio_status=ready`，WAV 0.56s / 17,964 bytes；`GET .../audio` 为 `audio/wav` 且以 `RIFF` 开头。
- 再生成得到 v2，版本列表为 `[2, 1]`；切回 v1 后剧本单元恢复该资产并显示声线。
- 制作概览从 `audio_ready_units=0` 变为 `1`，`next_action` 保持 `audio`。
- 剧本页结构检查显示“已生成 1 / 3”、单元内播放器、“重新生成”和“版本”入口；版本弹窗显示 `v001`、当前使用、时长/采样率/大小与声线。
- 浏览器控制台无报错。

## 5. 明确限制

- 当前为同步单单元生成，没有批量队列、GPU 调度、WebSocket 进度、连续播放器与播放进度记忆；这些仍属 0.6.0。
- 本轮真实 TTS 音质未复评；阶段 0 听感 gate 的既有结论不因 P7 改变，Mock WAV 不能用于音质验收。
- 真实 Ollama 的 P4 剧本质量与真实声线选角效果仍未最终验收；本阶段只做 Mock/受控 Provider 验证。
- P7 不新增迁移；旧片段音频与单元音频并存，旧 0.4.0 工作区仍用于原文校对与旧链路。
- 本轮 UI 因当前浏览器工具无可见视口，采用可访问性结构、接口与构建检查，没有取得可视截图；仍需要用户按完整流程做视觉与听感评审。
- 发布版本保持 0.4.0；待全流程评审与必要验收通过后，再决定是否标记 0.5.0 发布。

## 6. 下一步

按完整流程进行评审：导入 → 分析角色 → 分场 → 生成剧本 → 自动/手动选角 → 选中单元生成音频 → 试听 → 版本切回 → 处理异常。

评审通过后，再决定 0.5.0 发布口径与下一阶段范围；批量生成、连续播放与 M4B 导出继续留在 0.6.0/0.7.0。