"""阶段 2 分析 Prompt。

模板集中在此处维护，版本号写入章节分析元数据。Prompt 只发送到本机
Ollama，不应包含 API Key 或用户环境信息。
"""

from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "stage2.v1"
CHARACTER_PROMPT_VERSION = f"{PROMPT_VERSION}.characters"
SEGMENT_PROMPT_VERSION = f"{PROMPT_VERSION}.segments"
SUMMARY_PROMPT_VERSION = f"{PROMPT_VERSION}.summary"
REPAIR_PROMPT_VERSION = f"{PROMPT_VERSION}.repair"

_JSON_RULES = """你必须只输出一个合法 JSON 对象，不要输出 Markdown、代码围栏、解释或前后缀文字。
不得编造原文没有的事实。无法判断时使用 unknown，不要根据姓名刻板印象猜测性别或年龄。
所有引文必须来自本次输入文本。"""

_CHARACTER_SCHEMA = """{
  "characters": [
    {
      "name": "标准姓名",
      "aliases": [
        {"name": "别名或称谓", "confidence": 0.0, "basis": "explicit|inferred|unknown"}
      ],
      "gender": "male|female|other|unknown",
      "gender_basis": "explicit|inferred|unknown",
      "age_range": "child|teenager|young_adult|adult|middle_aged|elderly|unknown",
      "age_basis": "explicit|inferred|unknown",
      "character_type": "human|animal|non_human|unknown",
      "identity_description": "身份、职业或叙事作用，最多 80 字",
      "personality": ["性格标签"],
      "personality_desc": "性格简述",
      "appearance_desc": "外貌简述",
      "voice_description": "适合 TTS 的声音描述，例如年龄感、音高、音色、语速",
      "evidence": [{"quote": "原文证据", "reason": "为什么这样判断"}],
      "confidence": 0.0,
      "is_provisional": false
    }
  ],
  "relationships": [
    {
      "source_name": "角色标准姓名",
      "target_name": "角色标准姓名",
      "relationship_type": "亲属|朋友|敌对|师徒|同事|其他具体关系",
      "description": "关系说明",
      "evidence": [{"quote": "原文证据", "reason": "依据"}],
      "confidence": 0.0,
      "is_provisional": false
    }
  ]
}"""

_SEGMENT_SCHEMA = """{
  "segments": [
    {
      "paragraph_index": 0,
      "source_text": "必须逐字取自该段落的一段连续文本",
      "segment_type": "narration|dialogue|inner_monologue|unknown",
      "speaker_type": "character|narrator|unknown",
      "speaker_name": "已登记角色的标准姓名；旁白或无法判断时为 null",
      "speaker_confidence": 0.0,
      "emotion": "neutral|happy|excited|angry|sad|fearful|surprised|doubtful|sarcastic|tender|embarrassed|determined|tired",
      "emotion_confidence": 0.0,
      "intensity": 1,
      "delivery": "normal|whispering|shouting|crying|laughing|breathless|slow|hesitant",
      "tts_instruction": "供 TTS 使用的自然语言表演提示，不改变原文",
      "evidence": [{"quote": "原文依据", "reason": "判断理由"}]
    }
  ]
}"""

_SUMMARY_SCHEMA = """{
  "summary": "本章客观摘要，150-300 字",
  "key_events": ["关键事件"],
  "characters": ["本章出现的角色标准姓名"]
}"""


def _system(role: str) -> dict[str, str]:
    return {"role": "system", "content": role}


def _user(content: str) -> dict[str, str]:
    return {"role": "user", "content": content}


def _registry(known_characters: list[dict[str, Any]]) -> str:
    return json.dumps(known_characters, ensure_ascii=False, indent=2)


def character_messages(
    text: str,
    known_characters: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context or {}
    system = (
        "你是中文小说的角色编辑。只提取有明确叙事作用的主要人物和重要配角，"
        "不要为每个路人、群体称谓或普通名词创建角色。合并同一人物的全名、简称、"
        "称谓与绰号；证据不足时把性别、年龄、类型设为 unknown。"
        f"\n{_JSON_RULES}\n输出结构：\n{_CHARACTER_SCHEMA}"
    )
    user = (
        f"书籍：{context.get('book_title', '未知')}\n"
        f"当前章节：{context.get('chapter_title', '')}（序号 {context.get('chapter_sequence', '?')}）\n"
        f"上一章摘要（仅供指代参考，不可当作原文证据）：{context.get('previous_summary') or '无'}\n\n"
        f"已确认角色注册表（标准姓名与别名，必须优先复用）：\n{_registry(known_characters)}\n\n"
        "请分析下面这一章的原文。只记录本章有依据的角色信息；若本章出现注册表中"
        "同一人物的新称谓，把它作为 aliases 返回。关系两端必须使用角色标准姓名。\n\n"
        f"<chapter>\n{text}\n</chapter>"
    )
    return [_system(system), _user(user)]


def segment_messages(
    text: str,
    known_characters: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context or {}
    system = (
        "你是中文小说的配音分段编辑。把章节切分为适合语音合成的片段，区分旁白、"
        "直接对白和内心独白，并判断说话人、情绪与表演方式。"
        "\n规则：\n"
        "1. 每个段落可以切成多个片段（多人对话按发言轮次拆开），但同一段落内所有片段的 "
        "source_text 按顺序拼接后必须等于该段落原文（忽略首尾空白）。\n"
        "2. source_text 必须逐字引用，不得改写、增删标点或合并跨段落文本。\n"
        "3. 普通叙述与内心独白使用 narration / inner_monologue，speaker_type=narrator，"
        "speaker_name=null；内心独白默认使用旁白声音。\n"
        "4. 直接对白只在能从原文可靠判断时填写 speaker_name，且必须来自注册表；"
        "无法判断则 speaker_type=unknown、speaker_name=null、speaker_confidence 低于 0.5。\n"
        "5. 感叹号不等于愤怒；没有依据时 emotion=neutral、emotion_confidence 较低。\n"
        "6. intensity 只能是 1、2、3。\n"
        f"\n{_JSON_RULES}\n输出结构：\n{_SEGMENT_SCHEMA}"
    )
    paragraphs = context.get("paragraphs") or []
    user = (
        f"书籍：{context.get('book_title', '未知')}\n"
        f"当前章节：{context.get('chapter_title', '')}（序号 {context.get('chapter_sequence', '?')}）\n"
        f"上一章摘要：{context.get('previous_summary') or '无'}\n\n"
        f"角色注册表：\n{_registry(known_characters)}\n\n"
        "分段时只使用下面的段落编号与原文：\n"
        f"{json.dumps(paragraphs, ensure_ascii=False, indent=2)}"
    )
    return [_system(system), _user(user)]


def summary_messages(
    text: str,
    known_characters: list[dict[str, Any]],
) -> list[dict[str, str]]:
    system = (
        "你是中文小说编辑。请客观总结章节，不评价文笔，不补充原文没有的信息。"
        "人物变化、地点、关键事件和未解决的指代要写清楚，供下一章分析参考。"
        f"\n{_JSON_RULES}\n输出结构：\n{_SUMMARY_SCHEMA}"
    )
    user = (
        f"角色注册表：\n{_registry(known_characters)}\n\n"
        f"<chapter>\n{text}\n</chapter>"
    )
    return [_system(system), _user(user)]


def repair_messages(
    invalid_output: str,
    error: str,
    schema: str,
    task: str,
) -> list[dict[str, str]]:
    system = (
        f"你是 JSON 修复器。任务类型：{task}。根据校验错误修正下面的输出。"
        "只输出修复后的合法 JSON 对象；不要省略原有内容，不要添加解释。"
        f"\n输出结构：\n{schema}"
    )
    user = (
        f"校验错误：{error[:1500]}\n\n"
        f"待修复输出（可能被截断）：\n{invalid_output[:6000]}"
    )
    return [_system(system), _user(user)]


SCHEMAS = {
    "characters": _CHARACTER_SCHEMA,
    "segments": _SEGMENT_SCHEMA,
    "summary": _SUMMARY_SCHEMA,
}