"""首批 36 条通用声线目录；只定义参数，不自动下载或调用模型。"""

from __future__ import annotations

from typing import Any

_AGES = (
    ("child", "儿童", (("清亮", "天真"), ("柔和", "好奇"), ("活泼", "明快"))),
    ("teenager", "少年", (("清朗", "朝气"), ("低柔", "腼腆"), ("轻快", "机灵"))),
    ("young_adult", "青年", (("温润", "自然"), ("清亮", "克制"), ("微沙", "松弛"))),
    ("adult", "成年", (("沉稳", "诚恳"), ("醇厚", "温和"), ("清晰", "理性"))),
    ("middle_aged", "中年", (("低沉", "稳重"), ("温厚", "阅历感"), ("微沙", "克制"))),
    ("elderly", "老年", (("苍劲", "缓慢"), ("温和", "沙哑"), ("清瘦", "慈祥"))),
)


def general_voice_presets() -> list[dict[str, Any]]:
    """返回稳定的 6 年龄段 × 2 性别 × 3 音色目录。"""
    result: list[dict[str, Any]] = []
    for age, age_label, variants in _AGES:
        for gender, gender_label in (("male", "男"), ("female", "女")):
            for index, (timbre, personality) in enumerate(variants, start=1):
                preset_id = f"general_{age}_{gender}_{index}"
                result.append(
                    {
                        "preset_id": preset_id,
                        "display_name": f"通用 · {age_label}{gender_label} · {timbre}",
                        "description": (
                            f"{age_label}{gender_label}声，{timbre}，{personality}，"
                            "普通话自然叙述，吐字清楚，情绪不过度表演"
                        ),
                        "gender_presentation": gender,
                        "age_impression": age,
                        "language": "zh-CN",
                        "timbre_tags": [timbre, age_label, gender_label, "通用"],
                        "personality_tags": [personality, "自然"],
                        "seed": sum(ord(char) for char in preset_id),
                    }
                )
    return result


def get_general_voice_preset(preset_id: str) -> dict[str, Any]:
    for preset in general_voice_presets():
        if preset["preset_id"] == preset_id:
            return preset
    raise KeyError(preset_id)
