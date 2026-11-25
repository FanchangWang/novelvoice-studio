# workers/experiments：历史实验归档

本目录收录阶段 0、阶段 3A/3B/3C 的音频实验、A/B 对比和试听页生成脚本。
它们**不属于生产引擎服务协议**，不会由 `services.yaml` 或应用控制器启动；
保留目的是让历史报告中的实验可以复现、复核。

生产入口只有：

- `workers/qwen_tts_worker/server.py`
- `workers/cosyvoice_worker/server.py`
- `workers/cosyvoice_worker/stage3c_deess.py`（生产去齿依赖，留在原位）

脚本仍使用原来的两个独立 venv，不需要为归档目录新建环境：

```powershell
# Qwen 实验
workers/qwen_tts_worker/.venv/Scripts/python.exe workers/experiments/qwen_tts_worker/<script>.py

# CosyVoice 实验
workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/<script>.py

# 试听页（只读已生成的 WAV/JSON，不生成音频）
uv run python workers/experiments/stage3_audition.py
```

实验输入/输出默认仍在：

```text
~/Documents/NovelVoiceStudio/cache/poc_output/
```

归档脚本会按相对位置找到 `workers/cosyvoice_worker/CosyVoice` 源码和
`workers/cosyvoice_worker/stage3c_deess.py`；不要把这两个路径移到本目录。

## 目录

```text
workers/experiments/
├── stage3_audition.py
├── qwen_tts_worker/
│   ├── voice_design_poc.py
│   ├── voice_design_stage3.py
│   ├── voice_design_stage3b.py
│   ├── stage3b_emotion_sources.py
│   └── stage3c_qwen_sources.py
└── cosyvoice_worker/
    ├── cosyvoice_poc.py
    ├── requirements-poc.txt
    ├── stage3_ab.py
    ├── stage3_cases.json
    ├── stage3_prepare_refs.py
    ├── stage3_speaker_metrics.py
    ├── stage3b_*.py
    ├── stage3b_cases.json
    ├── stage3c_final_try.py
    ├── stage3c_probe.py
    ├── stage3c_vc_ab.py
    └── build_stage3*.py
```

## 旧路径映射

历史报告按当时的路径记录命令，本目录不修改历史报告原文。对应关系如下：

| 旧路径 | 新路径 |
|---|---|
| `workers/stage3_audition.py` | `workers/experiments/stage3_audition.py` |
| `workers/qwen_tts_worker/voice_design_poc.py` | `workers/experiments/qwen_tts_worker/voice_design_poc.py` |
| `workers/qwen_tts_worker/voice_design_stage3*.py` | `workers/experiments/qwen_tts_worker/voice_design_stage3*.py` |
| `workers/qwen_tts_worker/stage3b_emotion_sources.py` | `workers/experiments/qwen_tts_worker/stage3b_emotion_sources.py` |
| `workers/qwen_tts_worker/stage3c_qwen_sources.py` | `workers/experiments/qwen_tts_worker/stage3c_qwen_sources.py` |
| `workers/cosyvoice_worker/<实验脚本>` | `workers/experiments/cosyvoice_worker/<实验脚本>` |

`workers/cosyvoice_worker/stage3c_deess.py` **没有移动**；它由生产
`server.py` 的 `/v1/deess` 使用。

## 边界

- 归档脚本可能读取本机模型缓存和 `poc_output` 历史音频；缺少这些数据时无法完整复现。
- 归档脚本不是应用依赖，不参与 `pytest`、前端测试或引擎服务启停。
- 新实验应先在本目录建立说明，不要把一次性输出、日志或临时 WAV 提交进仓库。