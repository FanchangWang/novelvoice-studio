# 引擎服务正式化与仓库清理记录

日期：2026-11-26

项目版本：0.4.0

范围：`workers/` 引擎服务边界、统一控制命令、实验脚本归档、临时文件清理与回归验证

> **结论：`workers/` 已按“引擎服务目录”正式化，生产入口仍只有 Qwen3-TTS 与 CosyVoice3 两个回环 HTTP 服务；阶段实验脚本统一归档到 `workers/experiments/`，不再混在生产目录中。仓库临时数据与未采用的 Mizuoto 评估草稿已清理，正式依赖与运行时行为未改变。后端 114 项测试、前端 33 项测试与生产构建均通过。**

---

## 1. 边界与未改变项

本轮没有修改：

- 模型文件、模型缓存位置或下载方式；
- SQLite schema、Alembic 迁移或音频存储规则；
- Provider 选择逻辑、Qwen → CosyVoice VC 生成链路；
- 引擎服务的业务接口与音频格式；
- 阶段 3 的音质结论，也没有把已知毛刺说成已解决。

`workers/` 仍然是独立运行边界，而不是应用内部 Python 包：

| 项目 | Python | torch | 作用 |
|---|---:|---|---|
| 核心应用 `.venv/` | 3.12 | 无 | FastAPI、SQLite、Ollama 客户端 |
| `workers/qwen_tts_worker/` | 3.12 | `2.11.0+cu128` | Qwen3-TTS VoiceDesign |
| `workers/cosyvoice_worker/` | 3.10 | `2.3.1+cu121` | CosyVoice3 VC 与参考音去齿 |

独立 venv 的原因是两套 torch 与 Python 版本无法共存于一个 workspace；应用只通过回环 HTTP 调用服务，不导入 worker 代码。

---

## 2. 引擎服务正式化

### 2.1 唯一服务清单

新增 `workers/services.yaml`，只登记生产入口：

| 服务 | 项目 | 默认地址 | 应用配置 |
|---|---|---|---|
| `qwen_tts` | `workers/qwen_tts_worker` | `http://127.0.0.1:8766` | `voice_design.worker_url` / `voice_design.model_id` |
| `cosyvoice` | `workers/cosyvoice_worker` | `http://127.0.0.1:8767` | `tts.worker_url` / `tts.model_id` |

端口和模型 ID 不再在清单中重复配置。清单校验会拒绝：

- 项目路径位于 `workers/` 之外；
- entrypoint 不存在；
- 非回环地址、带路径/认证/查询串或缺少显式端口的 URL；
- 未知配置字段。

### 2.2 统一控制命令

生产启停统一为：

```powershell
uv run novelvoice-studio worker list
uv run novelvoice-studio worker status [--json]
uv run novelvoice-studio worker start [qwen_tts|cosyvoice] [--preload]
uv run novelvoice-studio worker stop [--force]
uv run novelvoice-studio worker restart
uv run novelvoice-studio worker logs [服务名] [--lines N]
```

`scripts/start_workers.ps1`、`scripts/stop_workers.ps1` 只保留为兼容薄封装，实际逻辑在核心应用中，不需要手动激活服务 venv。

### 2.3 生命周期与安全

- 服务只绑定 `127.0.0.1` / `localhost` / `::1`。
- `/health` 返回 `worker`、`pid`、`instance_id`、`model_loaded`、`load_error`、`torch` 与 GPU 摘要。
- `/v1/shutdown` 仅在控制器注入 `NOVELVOICE_ENGINE_CONTROL_TOKEN` 时启用；必须带匹配的 `X-NVS-Engine-Token`。未启用返回 404，令牌错误返回 403。
- 状态文件 `.run/workers/<id>.json` 同时记录启动器 PID 与 `/health` 回填的实际服务 PID；`.run/` 已忽略且含控制令牌，不提交。
- 停止优先请求 `/v1/shutdown`，要求 `instance_id` 一致；`--force` 还必须核实进程命令行仍指向登记的 `server.py`，否则只报告、不误杀未知进程。

### 2.4 真实启停验证

本轮在同一代码状态下实际验证过：

- `list`、`status`、`start`、`stop`、`restart`、`logs` 均可用；
- 启动后两个 `/health` 正常返回 PID、torch 与 GPU 信息；
- 未授权 shutdown 返回 403，带状态令牌的优雅停止返回 200；
- 故意使用错误令牌后，`--force` 通过命令行核实结束启动器进程树；
- 停止后状态文件被移除，8766/8767 端口关闭。

清理完成后再次确认两个服务均为“未运行”，端口 8766/8767 无监听。

---

## 3. 仓库清理

### 3.1 删除内容

| 路径 | 内容 | 处理 |
|---|---|---|
| `.tmp/` | 约 9.6 GB 临时数据：Mizuoto 数据/venv/源码、历史 JSON/日志、旧文档备份、一次性脚本 | 已删除 |
| `.run/` | 应用与服务运行日志 | 已删除；运行时自动重建 |
| `docs/assessment.md` | 外部工具给出的 Mizuoto 改造任务草稿，不是项目正式文档 | 已删除 |
| `docs/reports/MIZUOTO_REVIEW_REPORT_2026-11-26.md` | 未采用的 Mizuoto 长篇评估报告 | 已删除；结论见本报告第 5 节 |
| `__pycache__/`、`.pytest_cache/` | Python/测试缓存 | 已删除 |

保留：

- `.venv/`、`frontend/node_modules/`：依赖环境，不属于提交内容；
- `frontend/dist/`：本地构建产物，已被 `.gitignore` 忽略；删除会破坏直接启动后的页面访问；
- 模型缓存与 `poc_output` 历史音频：位于用户数据目录，不在仓库内。

### 3.2 实验脚本归档

新建 `workers/experiments/`，把阶段 0/3A/3B/3C 的 POC、A/B、指标和试听页脚本从生产目录移出：

- `workers/experiments/qwen_tts_worker/`：5 个 Qwen VoiceDesign 实验脚本；
- `workers/experiments/cosyvoice_worker/`：20 个 CosyVoice 实验脚本、2 个 cases JSON 与 POC requirements；
- `workers/experiments/stage3_audition.py`：阶段 3 试听页生成器。

生产目录现在只保留：

```text
workers/qwen_tts_worker/{pyproject.toml,uv.lock,server.py}
workers/cosyvoice_worker/{pyproject.toml,uv.lock,server.py,stage3c_deess.py}
```

`stage3c_deess.py` 没有移动，因为生产 `server.py` 的 `/v1/deess` 直接导入它。归档脚本通过相对位置读取该模块和 `workers/cosyvoice_worker/CosyVoice` 源码；旧路径到新路径的映射写在 `workers/experiments/README.md`，历史报告原文不改写。

归档验证：

- 26 个归档 Python 文件通过 `py_compile`；
- CosyVoice 20 个、Qwen 5 个模块在各自 venv 中完成导入检查（不执行 `main`）；
- 实验依赖的历史 `poc_output` 音频未提交，缺少数据时只能复核脚本与方法，不能完整重放当时的听感。

### 3.3 未归档的一次性工具

`.tmp` 中的 `check_links.py`、`normalize_eol.py`、`update_docs.py`、`move_ollama_models.ps1` 是本次仓库整理的一次性辅助脚本，不作为产品能力保留，已随 `.tmp` 删除。后续如需文档链接检查或换行符检查，应重新实现为带测试的正式脚本，而不是继续积累临时工具。

---

## 4. 文档同步

已同步更新：

- `README.md`：引擎服务启停、目录结构、故障排查；
- `workers/README.md`：服务边界、协议、PID 语义、实验归档位置；
- `workers/experiments/README.md`：归档说明、运行方式、旧路径映射；
- `docs/README.md`：文档索引；
- `docs/ARCHITECTURE_NOTES.md`：引擎服务与实验脚本边界；
- `docs/MODELS.md`、`.env.example`、`config/config.example.yaml`：启动命令与字段说明；
- 前端设置页、音色页、分析摘要与试听弹窗：统一使用“引擎服务”文案。

---

## 5. Mizuoto 评估结论

本轮评估过 Mizuoto 0.2.38 作为统一 TTS Runtime 的可行性，结论是**不采用、不写入正式依赖**：

- 按草稿的 100 分准入表评分 **76/100**，低于 80 分门槛；
- Qwen VoiceDesign 与 CosyVoice 输出和现有 Worker 高度等价（波形相关约 0.99998 / 0.99750），替换没有音质收益；
- Socket.IO 默认反射任意 Origin，与本项目“绝不使用 `Access-Control-Allow-Origin: *`”的硬约束冲突；
- 默认浏览根目录过大、默认无 token，需要额外安全加固；
- 自带 voices/outputs/jobs 状态源，与现有 SQLite、`books/` 存储、去齿 DSP 和 Qwen→Cosy 编排重复；
- 0.2.x API 快速变化，社区与发布成熟度不足。

因此继续使用现有 Qwen3-TTS + CosyVoice3 两个引擎服务，主线工作转向阶段 4–5。原始长篇报告与外部任务草稿已按清理要求删除；本报告保留决策与理由。

---

## 6. 回归验证

| 检查 | 结果 |
|---|---|
| 后端测试 | **114 passed, 1 warning**（`uv run pytest -o addopts="" --disable-warnings`，10.11 s） |
| 前端测试 | **7 个测试文件、33 项测试通过**（Vitest） |
| 前端生产构建 | `vue-tsc --noEmit && vite build` 通过，2723 个模块 |
| 归档脚本 | `py_compile` 与模块导入检查通过 |
| 引擎服务 | `worker list` / `status` 正常；两个服务未运行；8766/8767 空闲 |
| 差异检查 | `git diff --check` 通过 |

警告仍是 FastAPI/Starlette `TestClient` 使用 `httpx` 的弃用提示，不影响本轮测试结果。

---

## 7. 遗留与下一步

仍未完成或不能宣称通过：

- 阶段 0 的模型许可证核对（`ttsfrd` / `wetext` 等）；
- 阶段 3 音质仍有句中毛刺、齿音与情感表现力限制；
- 阶段 4：SQLite 持久化队列、GPU 串行调度、WebSocket 进度、暂停/继续/取消/重试、重启恢复、段落/章节播放器与进度记忆；
- 阶段 5：FFmpeg 拼接、章节元数据、导出进度、ffprobe 校验、错误日志与重试。

历史报告中的脚本路径保持原样，查找实际文件时以 `workers/experiments/README.md` 的映射为准。