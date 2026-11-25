# 文档索引

本项目文档按“稳定说明 / 需求 / 设计 / 验收 / 历史报告”分层。历史报告保留原文件名与原始结论，不因目录整理改写验收状态。

## 稳定说明

- [项目 README](../README.md)：安装、启动、配置、测试与数据目录。
- [架构说明](ARCHITECTURE_NOTES.md)：模块边界、数据流、安全边界与阶段口径。
- [引擎服务目录](../workers/README.md)：`workers/` 的服务清单、独立 venv、回环协议与启停命令。
- [历史实验归档](../workers/experiments/README.md)：阶段 0/3 实验脚本、试听页生成器与旧路径映射；不参与生产运行。
- [模型清单与使用](MODELS.md)：LLM、语音模型的来源、下载链接、缓存位置与启动方式。

## 需求

- [需求规格说明书 v1.0](requirements/NovelVoice_Studio_需求规格说明书_v1.0.md)：产品目标、功能范围、接口与验收标准。

## 设计

- [v0.5.0 AI 生产链重构](design/V0_5_0_AI_PRODUCTION_CHAIN_2026-11-26.md)：暂停原阶段 4，改为全书设定、有声剧本、全局配音员与异常审核；含轻改编、音效 cue 与验收标准。
- [v0.5.0 数据模型与迁移](design/V0_5_0_DATA_MODEL_MIGRATION_2026-11-26.md)：新增圣经/场景/剧本/配音员/异常表，旧角色、片段、音色与音频资产的幂等迁移方案。

## 验收

- [阶段 0–3 轻度验收方式与验收单](acceptance/STAGE0_3_LIGHT_ACCEPTANCE_2026-11-26.md)：阶段 4 开工前的真实链路检查、验收表格与前置基线。

## 阶段报告

- [阶段 0 POC 报告](reports/POC_REPORT.md)：硬件环境、模型来源、结构化输出与三音色试听。
- [P2 全书角色圣经实施报告](reports/P2_BOOK_BIBLE_REPORT_2026-11-26.md)：角色、别名、关系、人物弧、剧情时间线、异常收件箱与选定章节重跑。
- [阶段 2 验收报告](reports/STAGE2_REPORT.md)：本机 Ollama 分析、角色/片段审核闭环。
- [阶段 3 验收报告](reports/STAGE3_REPORT.md)：音色设计、逐段生成、版本回退与引擎服务协议。
- [阶段 3 音质计划](reports/STAGE3_AUDIO_QUALITY_PLAN.md)：实验矩阵与退出标准。
- [阶段 3 音质报告](reports/STAGE3_AUDIO_QUALITY_REPORT.md)：音质实验、评分与选型结论。
- [阶段 3 复听结果](reports/STAGE3_LISTENING_RESULT.md)：首轮复听 gate 不通过。
- [阶段 3B 修复报告](reports/STAGE3B_AUDIO_REPAIR_REPORT.md)：参考音去齿、VC 链路与聚焦修复。
- [阶段 3B 复听结果](reports/STAGE3B_LISTENING_RESULT.md)：复听 gate 通过。
- [阶段 3C 去齿报告](reports/STAGE3C_SIBILANCE_REPORT.md)：句中毛刺与齿音处理定稿。
- [阶段 3C 复听结果](reports/STAGE3C_LISTENING_RESULT.md)：最终偏好与遗留问题。
- [引擎服务正式化与仓库清理记录](reports/ENGINE_SERVICE_AND_REPO_CLEANUP_2026-11-26.md)：统一启停、实验归档、Mizuoto 结论与回归验证。

## 阅读边界

阶段 0 的听感 gate 是有条件通过；阶段 3 首轮 gate 不通过，3B 复听通过，3C 接受“参考音先去齿、输出不处理”的链路。功能可用不代表音质已验收，模型许可证核对也仍未完成。详见各报告与 [项目 README](../README.md)。

当前版本仍为 0.4.0；0.5.0 主链重构已完成 P1 数据层（迁移 `9f3c2a7b5d10`）
与 P2 全书角色圣经后端（Mock 与真实本机 Ollama 均通过），
AI 生产链其余环节与 UI 尚在实施中，原阶段 4 暂停。设计与迁移口径见
[v0.5.0 AI 生产链重构](design/V0_5_0_AI_PRODUCTION_CHAIN_2026-11-26.md)，
P2 结果与限制见
[P2 实施报告](reports/P2_BOOK_BIBLE_REPORT_2026-11-26.md)。
