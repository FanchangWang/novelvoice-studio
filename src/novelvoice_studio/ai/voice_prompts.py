"""声音设计 / 表演指令模板。

参考音只描述音色，不带强烈情绪；表演指令在生成每个片段时叠加情绪、
强度和演绎方式。所有指令都用中文，避免模型把英文标签读进音频。
"""

from __future__ import annotations

from typing import Any

_EMOTION_PHRASES = {
    "neutral": "情绪平静克制",
    "happy": "语气轻快明朗",
    "excited": "情绪兴奋上扬",
    "angry": "压抑着怒意，语气沉硬",
    "sad": "压抑悲伤，声音发紧，像强忍眼泪",
    "fearful": "紧张害怕，气息略虚",
    "surprised": "带着惊讶",
    "doubtful": "语气疑惑",
    "sarcastic": "带一点克制的讽刺",
    "tender": "语气温柔",
    "embarrassed": "有些窘迫",
    "determined": "语气坚定",
    "tired": "声音疲惫",
}

_DELIVERY_PHRASES = {
    "normal": "",
    "whispering": "压低声音贴近耳语",
    "shouting": "提高音量喊出来",
    "crying": "带哭腔",
    "laughing": "话里带笑",
    "breathless": "气息急促，像刚跑过",
    "slow": "放慢语速",
    "hesitant": "迟疑停顿",
}

_INTENSITY_PHRASES = {
    1: "程度轻微",
    2: "情绪明显",
    3: "情绪强烈",
}

_BASE_QUALITY = "咬字清楚，气声少，齿音轻，语感自然，句尾干净"


def describe_character_voice(
    *,
    gender: str = "unknown",
    age_range: str = "unknown",
    voice_description: str | None = None,
    personality_desc: str | None = None,
) -> str:
    """把角色属性压成一句音色描述，作为 VoiceDesign 的 instruct 基底。"""
    cleaned = (voice_description or "").strip()
    if cleaned:
        return cleaned

    age = {
        "child": "儿童",
        "teenager": "少年",
        "young_adult": "年轻",
        "adult": "成年",
        "middle_aged": "中年",
        "elderly": "老年",
    }.get(age_range, "成年")
    if age_range == "child":
        base = "男童" if gender == "male" else "女童" if gender == "female" else "儿童"
    elif age_range == "teenager":
        base = "少年" if gender == "male" else "少女" if gender == "female" else "少年"
    elif gender == "male":
        base = f"{age}男性"
    elif gender == "female":
        base = f"{age}女性"
    else:
        base = "中性嗓音"
    description = f"{base}，{_BASE_QUALITY}"
    personality = (personality_desc or "").strip()
    if personality:
        description += f"；性格{personality[:60]}，但参考音本身保持平静"
    return description


def build_reference_instruction(description: str) -> str:
    base = (description or "").strip().rstrip("。；;，,")
    if not base:
        base = f"成年男性，{_BASE_QUALITY}"
    return (
        f"{base}。这是音色参考朗读：语速自然，情绪平静克制，"
        "不带明显情绪起伏，不要叹气，不要拖长尾音。"
    )


def build_performance_instruction(
    base_description: str,
    *,
    emotion: str | None = None,
    intensity: float = 1.0,
    delivery: str | None = None,
    extra: str | None = None,
) -> str:
    base = (base_description or "").strip().rstrip("。；;，,")
    if not base:
        base = f"成年男性，{_BASE_QUALITY}"
    emotion_key = (emotion or "neutral").strip().lower()
    emotion_phrase = _EMOTION_PHRASES.get(emotion_key, "情绪平静克制")
    level = max(1, min(3, int(round(intensity or 1))))
    delivery_key = (delivery or "normal").strip().lower()
    delivery_phrase = _DELIVERY_PHRASES.get(delivery_key, "")

    parts = [f"{base}。此刻{emotion_phrase}"]
    if level >= 2:
        parts.append(_INTENSITY_PHRASES[level])
    if delivery_phrase:
        parts.append(delivery_phrase)
    parts.append("语速自然，保持同一角色音色")
    instruction = "，".join(parts) + "；" + _BASE_QUALITY + "，不要拖长尾音。"
    cleaned_extra = (extra or "").strip()
    if cleaned_extra:
        instruction += f"额外表演要求：{cleaned_extra[:300]}。"
    return instruction


def profile_base_description(profile: dict[str, Any]) -> str:
    """兼容 VoiceProfile 字典：优先 description，其次 instruct_text。"""
    for key in ("description", "instruct_text", "reference_text"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"成年男性，{_BASE_QUALITY}"