# workers/：引擎服务目录

`workers/` 是 NovelVoice Studio 的**引擎服务目录**，不是应用内部的 Python 包。
每个子目录都是一个独立 uv project，拥有自己的 Python 版本、虚拟环境、模型依赖、
启动入口和回环 HTTP 协议。应用只通过 HTTP 调用它们，不导入其代码。

这样做的直接原因是依赖冲突：

| 项目 | Python | torch | 说明 |
|---|---:|---|---|
| 核心应用 `.venv/` | 3.12 | 无 | FastAPI、SQLite、Ollama 客户端；保持轻量 |
| `workers/qwen_tts_worker/` | 3.12 | `2.11.0+cu128` | Qwen3-TTS VoiceDesign |
| `workers/cosyvoice_worker/` | 3.10 | `2.3.1+cu121` | CosyVoice3 VC + 去齿 DSP |

一个 uv workspace 或一个带多个 CLI 入口的项目无法同时容纳 Python 3.12/3.10
和两套 torch，因此独立 venv + 独立进程是运行边界，而不是临时脚本。

## 目录结构

```text
workers/
├── services.yaml              # 生产引擎服务注册表（唯一清单）
├── README.md                  # 本文件：边界、协议、启停与新增服务
├── experiments/               # 历史实验脚本与复现数据（不参与运行）
│   ├── README.md              # 归档说明与旧路径映射
│   ├── stage3_audition.py     # 阶段 3 试听页生成器
│   ├── qwen_tts_worker/       # Qwen VoiceDesign 阶段实验
│   └── cosyvoice_worker/      # CosyVoice 阶段实验与 cases JSON
├── qwen_tts_worker/
│   ├── pyproject.toml         # 独立 Python 3.12 / torch cu128
│   ├── uv.lock
│   └── server.py              # 生产入口，由控制器启动
└── cosyvoice_worker/
    ├── pyproject.toml         # 独立 Python 3.10 / torch cu121
    ├── uv.lock
    ├── server.py              # 生产入口，由控制器启动
    └── stage3c_deess.py       # server.py 的去齿依赖，必须留在原位
```

`services.yaml` 只登记**生产入口**。`workers/experiments/` 中的 `*_poc.py`、
`build_stage3*.py`、`stage3_*.py` 是阶段实验/复现脚本，应用不会调用，也不应加进
注册表。历史证据保留在归档目录，生产服务目录只留运行所需文件。

## 服务清单

端口和模型 ID 不在 `services.yaml` 重复配置。控制器从应用配置读取：

| 服务 | 应用配置字段 | 默认地址 | 默认模型 |
|---|---|---|---|
| `qwen_tts` | `voice_design.worker_url` / `voice_design.model_id` | `http://127.0.0.1:8766` | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` |
| `cosyvoice` | `tts.worker_url` / `tts.model_id` | `http://127.0.0.1:8767` | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` |

修改端口时，同时修改 `config/config.yaml`（或环境变量）并重启对应服务。控制器会
拒绝非回环地址、带路径/认证信息/查询串的地址，以及没有显式端口的地址。

## 统一控制命令

在项目根目录，使用核心 venv 的模块入口（不需要手动激活服务 venv）：

```powershell
# 查看注册表里的生产服务
uv run novelvoice-studio worker list

# 查看地址、venv、PID、健康状态；--json 便于脚本消费
uv run novelvoice-studio worker status
uv run novelvoice-studio worker status --json

# 启动全部；也可只启动指定服务
uv run novelvoice-studio worker start
uv run novelvoice-studio worker start qwen_tts
uv run novelvoice-studio worker start --preload   # 提前加载模型，更慢且更占显存

# 查看日志尾部（默认每个文件 80 行）
uv run novelvoice-studio worker logs
uv run novelvoice-studio worker logs cosyvoice --lines 120

# 停止；只处理 .run/workers 中记录且身份可核实的进程
uv run novelvoice-studio worker stop
uv run novelvoice-studio worker stop --force

# 重启
uv run novelvoice-studio worker restart
```

PowerShell 兼容入口仍然保留，它们只转交参数，不再重复实现进程管理：

```powershell
pwsh.exe -File scripts/start_workers.ps1 [-Preload]
pwsh.exe -File scripts/stop_workers.ps1 [-Force]
```

应用**不会自动启动引擎服务**。真实模式启动应用前，先确认：

```powershell
uv run novelvoice-studio worker status
```

## 运行时协议

所有服务只绑定 `127.0.0.1` / `localhost` / `::1`，不监听局域网。当前协议：

| 服务 | 健康检查 | 业务接口 | 生命周期 |
|---|---|---|---|
| `qwen_tts` | `GET /health` | `POST /v1/generate` | `POST /v1/unload`、`POST /v1/shutdown` |
| `cosyvoice` | `GET /health` | `POST /v1/vc`、`POST /v1/deess` | `POST /v1/unload`、`POST /v1/shutdown` |

共同约束：

- 音频以 Base64 JSON 输入、以音频字节输出；元数据放在 `X-NVS-*` 响应头。
- 服务不读取调用方提供的任意文件路径，不执行任意文件访问，不自动下载模型。
- 模型按需加载；`--preload` 只是提前加载，失败仍会在首次请求时重试。
- `/health` 返回 `worker`、`pid`、`instance_id`、`model_loaded`、`load_error`、
  `torch` 与 GPU 摘要。`instance_id` 只用于控制面识别本次启动实例。
- `/v1/shutdown` 仅在控制器注入 `NOVELVOICE_ENGINE_CONTROL_TOKEN` 时启用；
  必须带 `X-NVS-Engine-Token`。未注入令牌时该接口返回 404，手工启动的服务不可被
  任意本机请求关闭。

## 进程与状态

- 状态文件：`.run/workers/<service-id>.json`，含启动器 PID、实例 ID、控制令牌、端口和日志路径；服务就绪后会回填 `/health` 报告的实际 `worker_pid`。
- 日志：`.run/workers/<service-id>.out.log` / `.err.log`。
- `.run/` 已被 `.gitignore` 忽略，含控制令牌，不能提交。
- Windows 上 venv 的 `python.exe` 启动器可能再拉起的服务子进程；`worker status`
  优先显示实际服务 PID，并在不同时附启动器 PID。强制停止优先结束启动器进程树。
- 启动前检查 `/health` 和端口：
  - 已有同一服务且健康：跳过启动；
  - 端口被其他服务占用或健康身份不匹配：拒绝启动，不结束未知进程；
  - 已有启动记录但健康未就绪：不重复启动，提示检查日志。
- 停止时优先请求 `/v1/shutdown`，要求 `/health` 的 `instance_id` 与状态文件一致。
- `--force` 只在状态文件中的启动器/服务工作进程仍存活、且命令行仍指向登记的
  `server.py` 时结束进程；实例 ID 匹配不足以无条件杀进程。外部启动的服务、实例
  不匹配或无法核实命令行身份时只报告，不结束进程。

## 新增引擎服务

1. 在 `workers/<service_id>/` 创建独立 uv project，固定 Python/torch 版本和
   `uv.lock`；不要把依赖装进核心 `.venv`。
2. 提供 `server.py`，支持 `--host`、`--port`、`--preload`，只允许回环地址。
3. 实现 `GET /health`，返回至少 `ok`、`worker`、`pid`、`instance_id`、
   `model_loaded`、`load_error`；`worker` 是稳定的服务标识。
4. 实现 `POST /v1/shutdown`：仅在 `NOVELVOICE_ENGINE_CONTROL_TOKEN` 存在时启用，
   校验 `X-NVS-Engine-Token`，通过 uvicorn `should_exit` 优雅退出。
5. 在 `workers/services.yaml` 增加条目，填写 `config_url` / `config_model` /
   `health_worker`；项目路径必须位于 `workers/` 内。
6. 在 `config/config.example.yaml`、`.env.example`、`docs/MODELS.md` 与本文补充
   配置和模型来源；不要硬编码本机绝对路径。
7. 加入 `test_engine_services.py` 的注册表/配置测试；实际模型加载仍按阶段验收
   单独记录，不能用静态检查代替真实生成。

## 与其他文档的关系

- 应用如何选择 Mock/Real Provider：`docs/ARCHITECTURE_NOTES.md` 第 9 节。
- 模型来源、缓存位置与下载命令：`docs/MODELS.md`。
- 应用启停与数据目录：项目根目录 `README.md`。
- 历史实验脚本与旧路径映射：`experiments/README.md`。
- 阶段 3 音质与复听结论：`docs/reports/STAGE3*.md`。