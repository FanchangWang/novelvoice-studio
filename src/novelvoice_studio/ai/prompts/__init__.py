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

# v0.5.0 P2 全书角色圣经。分遍处理，禁止把整本书放进一次调用。
BIBLE_PROMPT_VERSION = "v050.p2.bible.v3"
BIBLE_CHARACTER_PROMPT_VERSION = f"{BIBLE_PROMPT_VERSION}.characters"
BIBLE_MERGE_PROMPT_VERSION = f"{BIBLE_PROMPT_VERSION}.merge"
BIBLE_TIMELINE_PROMPT_VERSION = f"{BIBLE_PROMPT_VERSION}.timeline"

# v0.5.0 P3 逐章分场与角色状态快照。窗口按片段编号完整覆盖。
SCENE_PROMPT_VERSION = "v050.p3.scene.v1"

# v0.5.0 P4 有声剧本。默认轻改编，source_text 必须按顺序覆盖场景原文。
SCRIPT_PROMPT_VERSION = "v050.p4.script.v1"

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

_BIBLE_CHARACTER_SCHEMA = """{
  "characters": [
    {
      "name": "标准姓名（优先复用已知角色名）",
      "aliases": [
        {"name": "全名、简称、称谓或绰号", "confidence": 0.0, "basis": "explicit|inferred|unknown"}
      ],
      "gender": "male|female|other|unknown",
      "gender_basis": "explicit|inferred|unknown",
      "age_range": "child|teenager|young_adult|adult|middle_aged|elderly|unknown",
      "age_basis": "explicit|inferred|unknown",
      "character_type": "human|animal|non_human|unknown",
      "role_tier": "protagonist|major|minor|extra|unknown",
      "role_hint": "本章中的叙事作用，最多 40 字",
      "identity_description": "身份、职业或叙事作用，最多 80 字",
      "personality": ["性格标签"],
      "personality_desc": "性格简述",
      "appearance_desc": "外貌简述",
      "voice_description": "适合 TTS 的声音描述，例如年龄感、音高、音色、语速",
      "arc_note": "本章所处的人生阶段、身心状态或明显变化，最多 80 字",
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
  ],
  "timeline": {
    "story_time_label": "本章故事发生时间，例如“十年前”“雨夜次日”；无法判断为 null",
    "time_mode": "present|flashback|flashforward|dream|unknown",
    "note": "倒叙、回忆、插叙或伏笔的说明",
    "confidence": 0.0
  }
}"""

_BIBLE_MERGE_SCHEMA = """{
  "characters": [
    {
      "name": "全书标准姓名",
      "merge_names": ["需要并入本角色的其他候选名或别名，可为空"],
      "role_tier": "protagonist|major|minor|extra|unknown",
      "overview": "该角色全书概述，最多 160 字",
      "arcs": [
        {
          "label": "阶段名称，例如“幼年”“黑化后”",
          "age_stage": "child|teenager|young_adult|adult|middle_aged|elderly|unknown",
          "chapter_start": 1,
          "chapter_end": 3,
          "story_time_start": "剧情时间起点，可为 null",
          "story_time_end": "剧情时间终点，可为 null",
          "summary": "该阶段变化，最多 100 字",
          "voice_hint": "该阶段声音倾向，可为 null",
          "confidence": 0.0
        }
      ],
      "confidence": 0.0
    }
  ],
  "notes": ["需要人工确认的疑点"]
}"""

_BIBLE_TIMELINE_SCHEMA = """{
  "narrative_mode": "linear|nonlinear|unknown",
  "chapters": [
    {
      "sequence": 1,
      "story_time_label": "该章故事发生时间，可为 null",
      "story_order": 1,
      "time_mode": "present|flashback|flashforward|dream|unknown"
    }
  ],
  "notes": ["时间线疑点"]
}"""

_SCENE_SCHEMA = """{
  "scenes": [
    {
      "paragraph_start": 0,
      "paragraph_end": 2,
      "title": "短场景标题",
      "summary": "场景客观摘要，不评价文笔",
      "location": "地点，无法判断为 null",
      "story_time_label": "场景故事内时间，可为 null",
      "time_mode": "present|flashback|flashforward|dream|unknown",
      "pov_name": "视角角色标准姓名，无法判断为 null",
      "mood": "气氛，最多 12 字",
      "characters": [
        {
          "name": "角色标准姓名，必须来自注册表",
          "presence": "speaker|present|mentioned",
          "age_stage": "child|teenager|young_adult|adult|middle_aged|elderly|unknown",
          "physical_state": "受伤、疲惫、醉酒等；无依据为 null",
          "emotional_state": "紧张、害怕、压抑等；无依据为 null",
          "relationship_state": "本场景中对其他角色的态度/关系状态，可为 null",
          "knowledge_state": "本场景知道/不知道/误以为什么，可为 null",
          "disguise_state": "易容、假名、卧底等，可为 null",
          "voice_note": "供 TTS 的声音提示，例如沙哑、虚弱、压低；可为 null",
          "state_tags": ["简短状态标签"],
          "confidence": 0.0,
          "evidence": [{"quote": "原文证据", "reason": "判断理由"}]
        }
      ],
      "confidence": 0.0,
      "evidence": [{"quote": "原文证据", "reason": "场景划分依据"}]
    }
  ],
  "notes": ["需要人工确认的分场或状态疑点"]
}"""


_SCRIPT_SCHEMA = """{
  "units": [
    {
      "unit_type": "narration|dialogue|inner_monologue|sfx|ambience|pause",
      "source_text": "本单元覆盖的连续原文，必须逐字取自输入场景，按顺序拼接可覆盖全文",
      "text": "实际送入 TTS 的文本；cue/pause 为空字符串",
      "display_text": "界面展示文本；对白可只展示台词，音效可写“惊堂木重拍（啪）”",
      "speaker_type": "narrator|character|unknown|none",
      "speaker_name": "对白说话人的角色标准姓名；旁白/未知时为 null",
      "emotion": "neutral|happy|excited|angry|sad|fearful|surprised|doubtful|sarcastic|tender|embarrassed|determined|tired",
      "intensity": 1,
      "delivery": "normal|whispering|shouting|crying|laughing|breathless|slow|hesitant",
      "tts_instruction": "声音表演提示，不改变台词内容",
      "speed": 1.0,
      "pause_before_ms": 0,
      "pause_after_ms": 250,
      "sfx": {"label": "音效短标签", "description": "音效描述", "timing": "before|during|after", "intensity": 1},
      "confidence": 0.0,
      "evidence": [{"quote": "原文依据", "reason": "判断理由"}]
    }
  ],
  "summary": "本场景有声剧本处理摘要，最多 120 字",
  "notes": ["需要人工确认的说话人、原文对齐或表演疑点"]
}"""

_SCRIPT_SYSTEM = (
    "你是中文小说的有声剧本编辑。把输入场景整理成适合多角色配音的单元序列。"
    "默认执行轻改编：保留原文叙述顺序、事实、人物关系和台词，不新增情节，不改写措辞。"
    "\n规则：\n"
    "1. source_text 必须逐字取自输入场景；units 按原文顺序排列，相邻单元的 source_text "
    "必须连续衔接，全部 units 合起来（除空白外）完整覆盖输入场景。不得跳段、重叠或遗漏。\n"
    "2. 一段原文可以拆成多个单元。若省略“他说/她道/问道”等纯阅读提示，"
    "仍要把这些字放进相邻单元的 source_text 覆盖范围，只让 text 不朗读它们。\n"
    "3. 引号内的直接台词用 dialogue；明确标出的内心想法用 inner_monologue；"
    "其余用 narration。普通叙述与内心独白默认 speaker_type=narrator。\n"
    "4. dialogue 的 speaker_name 只能是角色注册表中的标准姓名；"
    "证据不足时用 unknown，并在 notes 说明，不得猜测。\n"
    "5. 拟声词、拍击声等可以拆成 sfx cue；雨声、风声等持续环境声用 ambience；"
    "明确停顿用 pause。cue/pause 不进入 TTS，text 为空，信息写 display_text 与 sfx。\n"
    "6. 长句按呼吸和语义切分，但不得改变句意；emotion/delivery 没有依据时使用 neutral/normal，"
    "置信度保守。\n"
    "7. 不得输出原文之外的新增旁白、解释或总结。\n"
    f"\n{_JSON_RULES}\n输出结构：\n{_SCRIPT_SCHEMA}"
)


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


def bible_character_messages(
    text: str,
    known_characters: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context or {}
    system = (
        "你是中文小说的全书角色编辑。当前只处理一章（或其中一段窗口），"
        "但目标是建立全书角色圣经，因此要记录角色在本章所处的人生阶段、身心状态"
        "与明显变化，供后续全局归并使用。"
        "\n规则：\n"
        "1. 只提取有明确叙事作用的主要人物和重要配角，不要为路人、群体称谓或普通名词创建角色。\n"
        "2. 合并本章出现的全名、简称、称谓与绰号；证据不足时性别、年龄、类型设为 unknown。\n"
        "3. 不得根据姓名刻板印象推断性别或年龄；不得把上一章摘要当作本章原文证据。\n"
        "4. age_range 表示角色在本章所处的人生阶段；arc_note 记录本章状态/变化，"
        "例如幼年、受伤、紧张、黑化后。不要用全书最终年龄覆盖本章状态。\n"
        "5. role_tier 表示全书层级判断；本章无法判断时使用 unknown。\n"
        "6. 关系两端必须使用角色标准姓名；只记录本章有证据的关系。\n"
        "7. timeline 记录本章故事发生时间与叙事模式（顺叙/倒叙/回忆/梦境）。"
        "无法判断时用 unknown，不要按章节号臆测。"
        f"\n{_JSON_RULES}\n输出结构：\n{_BIBLE_CHARACTER_SCHEMA}"
    )
    window = context.get("window") or {}
    user = (
        f"书籍：{context.get('book_title', '未知')}\n"
        f"当前章节：{context.get('chapter_title', '')}"
        f"（序号 {context.get('chapter_sequence', '?')}）\n"
        f"窗口：{window.get('index', 1)}/{window.get('total', 1)}\n"
        f"本章已生成摘要（仅供指代参考，不可当作原文证据）："
        f"{context.get('chapter_summary') or '无'}\n"
        f"上一章摘要（仅供指代参考，不可当作原文证据）："
        f"{context.get('previous_summary') or '无'}\n\n"
        f"已知角色注册表（标准姓名与别名，必须优先复用）：\n"
        f"{_registry(known_characters)}\n\n"
        "请分析下面这段原文，输出本章/本窗口的人物、关系与时间线观察。\n\n"
        f"<chapter>\n{text}\n</chapter>"
    )
    return [_system(system), _user(user)]


def bible_merge_messages(
    candidates: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context or {}
    system = (
        "你是中文小说的全书角色圣经归并编辑。输入是按章节抽取、已经过确定性聚类的"
        "候选角色，不是整本原文。你的任务是：只在有明确依据时合并同一人物；"
        "判断全书角色层级；根据角色在各章的年龄阶段/状态变化整理人物弧。"
        "\n规则：\n"
        "1. 不要凭空创造候选表中没有的角色；merge_names 只能来自候选表或别名。\n"
        "2. 证据不足时保留为不同角色，并把疑点写入 notes，不要强行合并。\n"
        "3. 人物弧按剧情时间排序；chapter_start/chapter_end 使用章节序号。"
        "倒叙、回忆中的状态不能被当成当前章节状态。\n"
        "4. 同一角色可以有多个阶段（幼年、青年、老年、黑化后等）；没有阶段变化时只给一个阶段。\n"
        "5. role_tier 表示全书层级：protagonist 主角、major 重要配角、minor 次要角色、"
        "extra 路人级但需要声音的角色、unknown 无法判断。\n"
        "6. notes 只记录候选表内的归并或阶段疑点；不得补充候选表以外的"
        "人物关系、亲属称谓、具体事件或时间线结论。\n"
        f"\n{_JSON_RULES}\n输出结构：\n{_BIBLE_MERGE_SCHEMA}"
    )
    user = (
        f"书籍：{context.get('book_title', '未知')}\n"
        f"总章节数：{context.get('total_chapters', '?')}\n"
        f"本批候选角色（可能只是全书的一部分）：\n"
        f"{json.dumps(candidates, ensure_ascii=False, indent=2)}"
    )
    return [_system(system), _user(user)]


def bible_timeline_messages(
    chapters: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context or {}
    system = (
        "你是中文小说的剧情时间线编辑。输入是章节摘要、章节顺序和已有的时间提示，"
        "不是整本原文。请判断全书叙事模式，并为每章给出相对剧情时间顺序。"
        "\n规则：\n"
        "1. 不得把章节顺序直接当作剧情顺序；倒叙、回忆、插叙要按故事发生时间排序。\n"
        "2. story_order 使用 1..N 的整数，1 表示剧情时间最早；无法判断时给 null。\n"
        "3. 不编造摘要中没有的时间信息；不确定时 time_mode=unknown、story_time_label=null。\n"
        "4. notes 只写需要作者确认的时间线疑点。\n"
        f"\n{_JSON_RULES}\n输出结构：\n{_BIBLE_TIMELINE_SCHEMA}"
    )
    user = (
        f"书籍：{context.get('book_title', '未知')}\n"
        f"章节时间线材料：\n{json.dumps(chapters, ensure_ascii=False, indent=2)}"
    )
    return [_system(system), _user(user)]


def scene_messages(
    paragraphs: list[dict[str, Any]],
    known_characters: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context or {}
    chapter_time = context.get("chapter_time") or {}
    system = (
        "你是中文小说的分场与角色状态编辑。当前只处理一章（或其中一段窗口），"
        "需要把原文片段切分为场景，并为每个出场角色记录本场景的状态快照。"
        "\n规则：\n"
        "1. 每个输入片段必须且只能属于一个场景；scenes 必须按片段顺序排列，"
        "paragraph_start/paragraph_end 是输入片段的 index（包含首尾），"
        "不得跳号、重叠或遗漏。\n"
        "2. 按地点、故事时间、视角、事件或情绪转折切分场景，不要仅按长度平均切分。\n"
        "3. 角色姓名只能使用注册表中的标准姓名，不得创造新角色；"
        "只被提及而未出场的角色可用 presence=mentioned；对话发言者用 speaker。\n"
        "4. 状态必须按场景的剧情时间解释。time_mode 不是 present（倒叙、回忆、梦境、预叙）时，"
        "要使用角色在那一时点的年龄阶段与身心状态，不得把当前章节状态带进去。\n"
        "5. age_stage、身体、心理、关系、知情、伪装和声音提示只在原文有依据时填写；"
        "没有依据用 unknown 或 null。关系状态写本场景中的态度/距离，不重复全书关系设定。\n"
        "6. 视角角色优先从注册表选择；无法判断时 pov_name=null。\n"
        "7. 置信度要保守；分场边界或状态不确定时写 notes，供异常收件箱处理。\n"
        f"\n{_JSON_RULES}\n输出结构：\n{_SCENE_SCHEMA}"
    )
    user = (
        f"书籍：{context.get('book_title', '未知')}\n"
        f"当前章节：{context.get('chapter_title', '')}"
        f"（序号 {context.get('chapter_sequence', '?')}）\n"
        f"本章摘要（仅供指代参考，不可当作原文证据）："
        f"{context.get('chapter_summary') or '无'}\n"
        f"上一章摘要：{context.get('previous_summary') or '无'}\n"
        f"下一章摘要：{context.get('next_summary') or '无'}\n"
        f"本章剧情时间：故事时间={chapter_time.get('story_time_label') or '未知'}；"
        f"time_mode={chapter_time.get('time_mode') or 'unknown'}；"
        f"story_order={chapter_time.get('story_order') or '未知'}\n\n"
        f"角色注册表（标准姓名、别名、人物弧）：\n"
        f"{_registry(known_characters)}\n\n"
        "请分析下面的原文片段。片段编号可能因超长段落被切分而不等于原段落编号，"
        "但仍必须完整覆盖：\n"
        f"{json.dumps(paragraphs, ensure_ascii=False, indent=2)}"
    )
    return [_system(system), _user(user)]


def script_messages(
    scene_text: str,
    known_characters: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    context = context or {}
    scene = context.get("scene") or {}
    chunk = context.get("chunk") or {}
    speaker_states = context.get("speaker_states") or []
    user = (
        f"书籍：{context.get('book_title', '未知')}\n"
        f"章节：{context.get('chapter_title', '')}\n"
        f"场景：{scene.get('title') or '未命名'}；地点={scene.get('location') or '未知'}；"
        f"时间={scene.get('story_time_label') or '未知'}；"
        f"time_mode={scene.get('time_mode') or 'unknown'}\n"
        f"场景摘要（仅供指代，不可当作原文）：{scene.get('summary') or '无'}\n"
        f"改编档位：{context.get('adaptation_level') or 'light'}\n"
        f"场景分块：{chunk.get('index', 1)}/{chunk.get('total', 1)}\n\n"
        f"角色注册表：\n{_registry(known_characters)}\n\n"
        f"本场景角色状态（用于说话人、语气和声音提示）：\n"
        f"{json.dumps(speaker_states, ensure_ascii=False, indent=2)}\n\n"
        "请把下面的场景原文整理为有声剧本单元。再次强调："
        "units 的 source_text 必须按顺序连续覆盖全文，只允许忽略空白。\n\n"
        f"<scene>\n{scene_text}\n</scene>"
    )
    return [_system(_SCRIPT_SYSTEM), _user(user)]


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
    "bible_characters": _BIBLE_CHARACTER_SCHEMA,
    "bible_merge": _BIBLE_MERGE_SCHEMA,
    "bible_timeline": _BIBLE_TIMELINE_SCHEMA,
    "scenes": _SCENE_SCHEMA,
    "script": _SCRIPT_SCHEMA,
}