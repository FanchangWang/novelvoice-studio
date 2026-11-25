# 阶段 3B 复听结果

日期：2026-11-25
页面保存时间：2026-11-25T17:40:44.020Z
结论：**通过**。阶段 3 应用功能可以解除音质 gate 阻塞，但句中气声/毛刺仍需作为独立质量项继续修。

## 1. 评分

| 维度 | 分数 |
|---|---:|
| 跨情绪一致性 | 9 / 10 |
| sad 表现力 | 8 / 10 |
| 句尾气声/杂音 | 6 / 10 |
| 老年男性语速 | 8 / 10 |
| 中文发音与杂音 | 8 / 10 |
| Qwen+VC 变声痕迹 | 8 / 10 |
| 三音色差异 | 9 / 10 |
| 整体可用性 | 8 / 10 |

gate 结论：`pass`。

## 2. 选择

| 项目 | 选择 |
|---|---|
| sad 首选 | Qwen+VC |
| angry 首选 | Qwen+VC |
| 老年男性首选 | b08 + Qwen+VC |
| 尾音处理 | 都不满意（`none`） |

## 3. 备注原文

> 本轮整体效果还可以，主要的问题还是出现在气声，但不是每句结尾，而是句中就有，比如： “我等你很久了”的“久”字，“你答应过今天一定回来”的“今”字，“我把饭菜热了三遍”的“三”字，还有很多其他的。我通过搜索资料发现这种好像是 TTS 很常见的问题，qi zi ci si shi in 等音经常出现，人耳听起来会跟刮玻璃一样的感觉

## 4. 解读

1. **跨情绪、sad、老年语速都过了可用线**。尤其年轻女性 angry 的身份崩坏被 Qwen+VC 修到可用，sad 不再只靠放慢。
2. **尾音处理没有赢家**。硬切、衰减、原样都被判“有问题”，说明真正的问题不是句尾多长，而是句中某些音节本身带毛刺。
3. 问题集中在 **j / q / z / c / s / sh / in** 一类塞擦音、擦音和鼻韵母附近，例如“久”“今”“三”。这更像声码器/声学模型在高频瞬态上的伪影，而不是简单的尾部静音或气声。
4. 这不影响 gate 通过，但会影响长时间收听的疲劳度。不能把它当作“已经解决”。

## 5. 对后续的影响

- 阶段 3 应用实现可以开始，音色绑定采用：
  - 旁白：`narrator_03`
  - 年轻女性：`young_female_01`
  - 老年男性：`new_old_male_b08`
- sad / angry 默认走 Qwen 情感源 + CosyVoice VC；neutral 还需要补一条统一的 Qwen+VC 中性样本，避免 neutral 和情感句来自两条不同链路。
- 句尾 trim/attenuate 不作为默认后处理；在句中毛刺解决前，硬切只会改变句尾，不能解决“刮玻璃”感。
- 新开阶段 3C：句中毛刺诊断与抑制。优先定位毛刺来自 Qwen 源、VC，还是 CosyVoice 声码器，再决定是换源、换 seed、换声码器，还是做动态高频抑制。

## 6. 机器可读结果

原始评分 JSON 已保存为：

`%USERPROFILE%\Documents\NovelVoiceStudio\cache\poc_output\stage3b\listening_result.json`

```json
{
  "page": "stage3b",
  "version": "3B",
  "saved_at": "2026-11-25T17:40:44.020Z",
  "gate_conclusion": "pass",
  "scores": {
    "consistency": 9,
    "sad": 8,
    "tail": 6,
    "old_pace": 8,
    "pronunciation": 8,
    "vc_artifacts": 8,
    "timbre_diff": 9,
    "overall": 8
  },
  "preferences": {
    "sad_pref": "qwen_vc",
    "angry_pref": "qwen_vc",
    "old_pref": "b08_vc",
    "tail_pref": "none"
  },
  "notes": "本轮整体效果还可以，主要的问题还是出现在气声，但不是每句结尾，而是句中就有，比如： “我等你很久了”的“久”字，“你答应过今天一定回来”的“今”字，“我把饭菜热了三遍”的“三”字，还有很多其他的。我通过搜索资料发现这种好像是 TTS 很常见的问题，qi zi ci si shi in 等音经常出现，人耳听起来会跟刮玻璃一样的感觉",
  "heard": [],
  "page_generated_at": "2026-11-25 05:40 UTC",
  "sample_count": 64,
  "metric_source": "audition_metrics.json（CosyVoice Campplus 说话人嵌入）"
}
```