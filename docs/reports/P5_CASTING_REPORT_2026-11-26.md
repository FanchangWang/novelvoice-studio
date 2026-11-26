# P5 全局配音员库与自动选角实施报告

- 日期：2026-11-26
- 范围：v0.5.0 P5，后端 API、自动选角任务、书内选角计划与剧本单元同步
- 结论：**P5 后端完成并通过自动化测试；P5 未新增数据库迁移。** 真实 VoiceDesign 下的音色质量与选角听感仍需在 P7 流程评审时人工确认。

## 1. 目标与完成度

P5 把阶段 3“每本书临时设计音色”的模型，升级为**跨书全局配音员库**：

- 配音员是全局资产，可以收藏、筛选、试听、维护和跨书复用。
- 书内通过 `VoiceCast` 绑定旁白、角色基础声线与角色阶段声线。
- 自动选角任务优先复用合适的全局配音员；没有候选时才调用本机 VoiceDesign 生成。
- 选角结果同步到当前活动剧本版本的 `script_units.voice_cast_id`，为 P7 单元音频生成提供归属。
- 无法补齐、生成失败、低置信匹配、剧本单元无归属等情况写入异常收件箱。
- 支持手动改派；旧活动选角会停用，已生成单元音频会标记 `stale`。

## 2. 数据与存储

P5 复用 P1/P4 已经建立的表，不需要新迁移：

- `voice_actors`：全局配音员，状态 `draft/ready/failed/archived`，来源 `ai/legacy/user`。
- `voice_casts`：书内活动选角，作用域 `narrator/character/character_stage`。
- `script_units.voice_cast_id`：剧本单元到选角的绑定。
- `review_items`：选角异常进入统一收件箱。
- `generation_jobs`：自动选角任务 `job_type=book_casting`、`stage=casting`。

配音员参考音频存放在：

```text
<data_dir>/voice_actors/<actor_id>/reference.wav
<data_dir>/voice_actors/<actor_id>/reference_deessed.wav   # 真实 TTS 去齿成功后
```

音频二进制不入 SQLite。语音引擎不可用时，参考音预处理失败会保留原始 WAV，并由后续任务或人工处理。

## 3. 自动选角规则

### 3.1 选角目标

自动任务只处理尚未绑定的角色：

1. 缺少旁白选角时，先处理旁白。
2. 角色是否“必须选角”：`protagonist/major/minor`，或出现在当前活动分场中，或当前活动剧本中以该角色说话。
3. 角色存在多个不同年龄阶段（来自活动角色圣经人物弧与场景状态）时：
   - 基础选角 `character`；
   - 每个具体年龄阶段额外建立 `character_stage` 选角。
4. 已存在活动选角的 owner 不会重复生成。

### 3.2 复用优先

候选池是状态 `ready`、参考文件存在、且未在本书其他活动选角中使用的配音员。评分维度：

- 性别：明确男/女冲突直接拒绝；同性别、重大匹配；
- 年龄印象：差距超过两档拒绝；同阶段优先；
- 语言：相同语言加分；
- 音色/性格标签：重合比例加分；
- 收藏配音员有轻微优先级。

总分低于 `0.5` 视为不合适，才生成新配音员。自动生成使用基于 `book_id + character + stage` 的稳定种子，避免同一批角色全部得到同一参考音。

### 3.3 阶段声线与剧本单元同步

单元同步顺序：

1. 对白/独白/旁白优先匹配该单元 `character_state_id` 对应的具体年龄阶段选角；
2. 没有阶段选角时回退到角色基础选角；
3. 旁白单元绑定旁白选角；
4. 音效/环境/停顿不需要配音员。

选角变化会把已就绪单元音频标记为 `stale`，不会删除旧 WAV。

## 4. API

### 4.1 全局配音员

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/voice-actors` | 列表、搜索、收藏/状态过滤、分页 |
| POST | `/api/v1/voice-actors` | 用本机 VoiceDesign 创建配音员 |
| GET | `/api/v1/voice-actors/{actor_id}` | 详情与使用统计 |
| PATCH | `/api/v1/voice-actors/{actor_id}` | 改名、标签、收藏、归档/恢复 |
| GET | `/api/v1/voice-actors/{actor_id}/audio` | 参考音 WAV |
| POST | `/api/v1/voice-actors/{actor_id}/preview` | 用该配音员合成试听 |

归档仍被活动选角使用的配音员会返回 `409 actor_in_use`。

### 4.2 书内选角

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/books/{book_id}/casts` | 选角计划、缺失角色、异常数、准备状态 |
| GET | `/api/v1/books/{book_id}/casts/status` | 任务/生产状态 |
| POST | `/api/v1/books/{book_id}/casts/auto` | 启动后台自动选角（202） |
| POST | `/api/v1/books/{book_id}/casts/cancel` | 取消当前自动选角 |
| POST | `/api/v1/books/{book_id}/casts/assign` | 手动绑定旁白/角色/阶段声线 |

`GET /casts` 返回的 `ready` 需要满足：存在活动角色圣经、旁白已绑定、所有必须角色已有基础选角、没有 warning/blocking 选角异常，且当前没有运行中的自动选角任务。

## 5. 状态、异常与恢复

- 自动任务启动时书籍 `production_status=casting`。
- 完成后根据缺失角色、异常与活动选角计算 `ready/needs_review/failed`。
- 单个角色失败不会终止整个任务；全部目标失败时任务标记 `failed`。
- 启动时发现遗留 `pending/running` 的 `book_casting` 任务，会标记 `failed (interrupted)` 并写收件箱。
- 自动任务会忽略上一次遗留的选角异常；本次异常保留。
- 手动绑定会解决该 owner 的未决选角异常，并重新同步剧本单元。

异常编码：

| 编码 | 级别 | 含义 |
| --- | --- | --- |
| `cast_role_unfilled` | warning | 角色未能完成选角 |
| `cast_narrator_unfilled` | blocking/warning | 旁白未能完成选角 |
| `cast_low_confidence` | info | 复用候选分数偏低，建议试听 |
| `cast_script_unassigned` | warning | 活动剧本仍有可配音单元没有选角 |
| `cast_job_failed` | blocking | 自动选角任务异常终止 |

## 6. 自动化验证

新增 `tests/test_casting_api.py`，覆盖：

1. 全局配音员创建、详情、更新、收藏过滤、音频与试听、归档隐藏。
2. 自动选角：冲突保护、旁白与角色生成、剧本单元同步、书籍 `ready`。
3. 重复自动选角：无新增任务目标、不会产生重复活动选角。
4. 跨书复用旁白配音员。
5. 手动改派：旧选角停用、唯一活动选角、剧本单元改为新选角。
6. 人物弧与场景状态产生阶段选角，阶段配音员年龄印象正确。
7. 声音设计失败进入收件箱、任务与书籍失败状态正确。
8. 启动中断恢复：任务失败、书籍状态恢复、生成 `cast_job_failed`。

全量后端测试：**149 passed**。前端未修改，基线仍为 **33 passed / 7 files**。

## 7. 已知限制

- 选角匹配是规则评分，不是声线相似度模型；跨书复用只保证性别/年龄/标签不冒犯，最终听感需人工试听。
- 年龄差距仅按枚举档位计算，无法理解“同一演员分饰两角”的艺术需求；手动改派可覆盖。
- 阶段选角依赖活动角色圣经人物弧与当前场景状态；没有分场/剧本时只能建立基础选角。
- 已生成的配音员不会自动清理；归档仅阻止新绑定，已有选角保留。
- 真实 Qwen3 VoiceDesign 下的音色多样性、跨角色区分度与复用听感尚未做专门验收；P5 自动化测试使用 Mock Provider。
- 自动选角目前按顺序调用本机 VoiceDesign，未做显存并发控制之外的并发优化。
- P5 未提供 UI；制作台与异常收件箱在 P6 接入。

## 8. 后续

- P6：制作台 UI、书级进度聚合与异常收件箱操作。
- P7：按选中剧本单元和绑定选角生成 WAV、版本回退、试听与失效标记的用户闭环。
- P7 完成后进行一次完整流程评审：导入 → 全书角色圣经 → 分场 → 有声剧本 → 全局配音员/自动选角 → 单元音频试听。