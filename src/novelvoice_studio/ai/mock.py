"""确定性 Mock Provider：不加载模型、不访问网络，保证阶段 1 流程可测试。"""

from __future__ import annotations

import hashlib
import io
import math
import re

import numpy as np
import soundfile as sf

from novelvoice_studio.ai.base import (
    AudioResult,
    BibleArcDraft,
    BibleChapterResult,
    BibleCharacterDraft,
    BibleMergeCharacterDraft,
    BibleMergeResult,
    BibleTimelineChapterDraft,
    BibleTimelineDraft,
    BibleTimelineResult,
    ChapterSceneResult,
    ChapterScriptResult,
    ChapterSummaryResult,
    CharacterAnalysisResult,
    CharacterDraft,
    ImportStructureResult,
    LLMProvider,
    ProviderHealth,
    SceneCharacterStateDraft,
    SceneDraft,
    ScriptUnitDraft,
    SegmentAnalysisResult,
    SegmentDraft,
    TTSProvider,
    VoiceCandidate,
    VoiceDesignProvider,
)

_NAME_HINT = re.compile(r"([\u4e00-\u9fa5]{2,4})(?:说|道|问|答|喊|叫|笑|叹|想)")
_NAME_TAIL = (
    r"(?=[，。！？；：、“”「」『』\s]|说|道|问|答|喊|叫|笑|叹|想|把|看|走|站|坐|"
    r"摸|推|拿|摘|转|低|皱|点|摇|沉|愣|举|回|望|心|没|忽|终)"
)
_BIBLE_NAME_HINT = re.compile(
    r"(?:^|(?<=[，。！？；：“”「」『』\s]))"
    r"([赵钱孙李周吴郑王冯陈蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹喻柏窦章"
    r"苏潘葛范彭郎鲁韦马苗方俞任袁柳鲍史唐薛雷贺倪汤罗毕郝安常傅齐康伍余顾孟"
    r"黄穆萧尹姚邵汪毛狄米贝明戴谈宋庞熊纪舒屈项董梁杜阮蓝闵席季贾路江童颜郭"
    r"梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万柯卢莫房解应宗丁宣邓郁单杭洪包诸左石"
    r"崔吉龚程邢裴陆荣翁荀羊惠甄曲封芮储靳段富焦巴牧山谷侯全班仰秋仲伊宫宁仇"
    r"甘厉祖武符刘景詹龙叶幸黎白怀蒲从索赖卓屠蒙池乔苍闻党翟谭劳姬申扶冉雍桑"
    r"桂牛寿通边燕浦尚农温庄晏柴瞿阎慕连茹艾鱼容向古易慎戈廖终居衡步耿满弘匡"
    r"国文寇广禄欧沃利蔚越隆师巩聂融冷辛简饶曾沙丰巢关相查荆红游竺权盖益桓]"
    r"|阿|小|老)"
    r"([\u4e00-\u9fa5]{1,2})"
    + _NAME_TAIL
)
_ALIAS_HINT = re.compile(
    r"(?:叫|称作|人称|绰号是|绰号叫)(?:他|她|它)?"
    r"([\u4e00-\u9fa5]{1,3})(?=[，。！？；、“”「」『』\s]|$)"
)
_MOCK_NAME_SUFFIXES = set("说道问答喊叫笑叹想把看走站坐摸推拿摘转低皱点摇沉愣举回望心没忽终")
_MOCK_NAME_STOPWORDS = {
    "两人",
    "众人",
    "有人",
    "自己",
    "对方",
    "声音",
    "门外",
    "屋里",
    "街坊",
    "熟人",
    "门灯",
    "照片",
    "旧城",
    "木盒",
    "钥匙",
    "三年前",
    "十年前",
    "多年前",
    "谁",
    "至今",
    "想起",
    "忽然",
    "终于",
    "没有",
    "林间",
    "白衣",
    "白天",
    "石头",
    "高山",
    "流水",
    "马上",
    "方向",
}
_AGE_MARKER = re.compile(
    r"([一二三四五六七八九十百\d]{1,4})岁|"
    r"(幼年|小时候|童年|少年|青年|中年|老年|老人)"
)
_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_STAGE_LABELS = {
    "child": "幼年",
    "teenager": "少年",
    "young_adult": "青年",
    "adult": "成年",
    "middle_aged": "中年",
    "elderly": "老年",
    "unknown": "未分阶段",
}
_QUOTE_MARKERS = ("「", "“", "『", '"')


def _chinese_number(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if "百" in raw:
        left, _, right = raw.partition("百")
        hundreds = _CHINESE_DIGITS.get(left, 1) if left else 1
        rest = 0
        if right:
            tens, _, units = right.partition("十")
            rest = (_CHINESE_DIGITS.get(tens, 1) if tens else 1) * 10 if "十" in right else _CHINESE_DIGITS.get(right, 0)
            if "十" in right and units:
                rest += _CHINESE_DIGITS.get(units, 0)
        return hundreds * 100 + rest
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        units = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + units
    return _CHINESE_DIGITS.get(raw)


def _age_stage_for_number(age: int) -> str:
    if age <= 12:
        return "child"
    if age <= 17:
        return "teenager"
    if age <= 35:
        return "young_adult"
    if age <= 55:
        return "adult"
    if age <= 70:
        return "middle_aged"
    return "elderly"


def _mock_age_stage(text: str, name: str) -> str:
    """Mock 用：把本章年龄标记关联到最近出现的角色名。"""
    positions = [match.start() for match in re.finditer(re.escape(name), text)]
    if not positions:
        return "unknown"
    for match in _AGE_MARKER.finditer(text):
        earlier = [position for position in positions if position <= match.start()]
        if not earlier:
            continue
        previous = max(earlier)
        # 只接受角色名之后 80 字内的年龄/阶段标记，避免跨段误关联。
        if match.start() - previous > 80:
            continue
        if match.group(1):
            number = _chinese_number(match.group(1))
            if number is not None:
                return _age_stage_for_number(number)
        label = match.group(2) or ""
        if label in {"幼年", "小时候", "童年"}:
            return "child"
        if label == "少年":
            return "teenager"
        if label == "青年":
            return "young_adult"
        if label == "中年":
            return "middle_aged"
        if label in {"老年", "老人"}:
            return "elderly"
    return "unknown"


def _mock_name_candidate(raw: str) -> str:
    candidate = raw.strip()
    while candidate and candidate[-1] in _MOCK_NAME_SUFFIXES:
        candidate = candidate[:-1]
    return candidate


def _mock_timeline(text: str) -> BibleTimelineDraft:
    label = None
    mode = "present"
    for marker in ("十年前", "多年前", "很多年前", "当年", "回忆", "想起", "梦中", "梦里"):
        if marker in text:
            label = marker
            mode = "dream" if marker in {"梦中", "梦里"} else "flashback"
            break
    return BibleTimelineDraft(
        story_time_label=label,
        time_mode=mode,
        note="模拟模式时间线提示，不代表真实分析结论。" if label else None,
        confidence=0.55 if label else 0.4,
    )


def _tone_wav(text: str, *, frequency: float, duration_ms: int, sample_rate: int = 16000) -> AudioResult:
    duration_ms = max(300, min(duration_ms, 8000))
    frames = int(sample_rate * duration_ms / 1000)
    time = np.arange(frames, dtype=np.float32) / sample_rate
    envelope = np.ones_like(time)
    fade = min(frames // 4, int(sample_rate * 0.02))
    if fade > 0:
        envelope[:fade] = np.linspace(0, 1, fade, dtype=np.float32)
        envelope[-fade:] = np.linspace(1, 0, fade, dtype=np.float32)
    wave = 0.12 * np.sin(2 * math.pi * frequency * time) * envelope
    buffer = io.BytesIO()
    sf.write(buffer, wave.astype(np.float32), sample_rate, format="WAV", subtype="PCM_16")
    return AudioResult(
        audio_bytes=buffer.getvalue(),
        sample_rate=sample_rate,
        channels=1,
        duration_ms=duration_ms,
        format="wav",
        provider="mock",
        model="mock-tone",
        is_mock=True,
        metadata={"text_length": len(text), "frequency": frequency},
    )


class MockLLMProvider(LLMProvider):
    name = "mock_llm"
    model = "mock-llm"
    is_mock = True

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="Mock LLM 始终可用（不调用模型）", meta={"mock": True})

    def list_models(self) -> list[str]:
        return [self.model]

    def inspect_import(
        self,
        front_matter: str,
        candidates: list[dict],
        rule_headings: list[dict],
        *,
        include_metadata: bool = True,
    ) -> ImportStructureResult:
        return ImportStructureResult(meta={"provider": self.name, "ai_used": False})

    def analyze_characters(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> CharacterAnalysisResult:
        names: list[str] = []
        for item in known_characters:
            name = item.get("canonical_name") or item.get("name")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
        # 模拟模式用极简启发式补一点角色名，方便演示审核流程；不要当作真实分析。
        for match in _NAME_HINT.findall(text):
            if match not in names:
                names.append(match)
        characters = [
            CharacterDraft(
                name=name,
                aliases=[],
                gender="unknown",
                age_range="unknown",
                character_type="unknown",
                identity_description="模拟模式仅用于流程验证，不代表真实分析结果。",
                personality=["待确认"],
                evidence=[text[:60]] if text else [],
                confidence=0.2,
                is_provisional=True,
            )
            for name in names[:12]
        ]
        return CharacterAnalysisResult(
            characters=characters,
            meta={"mock": True, "prompt_version": "mock"},
        )

    def analyze_segments(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> SegmentAnalysisResult:
        context = context or {}
        paragraphs = context.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            paragraphs = [
                {"index": index, "text": line.strip()}
                for index, line in enumerate(text.splitlines())
                if line.strip()
            ]
        segments: list[SegmentDraft] = []
        for item in paragraphs[:80]:
            index = int(item.get("index", 0))
            paragraph = str(item.get("text", "")).strip()
            if not paragraph:
                continue
            is_dialogue = paragraph[0] in _QUOTE_MARKERS
            segments.append(
                SegmentDraft(
                    paragraph_index=index,
                    source_text=paragraph,
                    segment_type="dialogue" if is_dialogue else "narration",
                    speaker_type="unknown" if is_dialogue else "narrator",
                    speaker_name=None,
                    speaker_confidence=0.1 if is_dialogue else 0.9,
                    emotion="neutral",
                    emotion_confidence=0.1 if is_dialogue else 0.9,
                    intensity=1,
                    delivery="normal",
                    tts_instruction=None,
                    evidence=[
                        "模拟模式按段落切分；对白说话人需人工确认。"
                    ],
                )
            )
        return SegmentAnalysisResult(
            segments=segments,
            meta={"mock": True, "prompt_version": "mock"},
        )

    def summarize_chapter(
        self, text: str, known_characters: list[dict]
    ) -> ChapterSummaryResult:
        summary = text.strip().replace("\n", " ")[:120] or "（空章节）"
        names = [
            str(item.get("canonical_name") or item.get("name"))
            for item in known_characters[:5]
            if item.get("canonical_name") or item.get("name")
        ]
        return ChapterSummaryResult(
            summary=summary,
            characters=names,
            meta={"mock": True, "prompt_version": "mock"},
        )

    # ---------------------------------------------------------- v0.5.0 P2

    def extract_bible_characters(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> BibleChapterResult:
        known: dict[str, list[str]] = {}
        for item in known_characters:
            name = str(item.get("canonical_name") or item.get("name") or "").strip()
            if not name:
                continue
            aliases = [
                str(alias.get("name") if isinstance(alias, dict) else alias).strip()
                for alias in (item.get("aliases") or [])
                if (alias.get("name") if isinstance(alias, dict) else alias)
            ]
            known[name] = [alias for alias in aliases if alias]
        names: list[str] = []
        for name, aliases in known.items():
            if name in text or any(alias in text for alias in aliases):
                names.append(name)
        counts: dict[str, int] = {}
        for match in _BIBLE_NAME_HINT.finditer(text):
            candidate = _mock_name_candidate(match.group(1) + match.group(2))
            if (
                len(candidate) < 2
                or candidate in _MOCK_NAME_STOPWORDS
                or candidate in names
            ):
                continue
            counts[candidate] = max(counts.get(candidate, 0), text.count(candidate))
        for candidate, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            # Mock 无法可靠区分只出现一次的人名与普通短语，至少出现两次才建档。
            if count >= 2:
                names.append(candidate)
        if not names:
            return BibleChapterResult(
                meta={"mock": True, "prompt_version": "mock", "characters": 0}
            )

        occurrences: list[tuple[int, str]] = []
        for name in names:
            occurrences.extend((match.start(), name) for match in re.finditer(re.escape(name), text))
        occurrences.sort()
        aliases_by_name: dict[str, list[str]] = {name: [] for name in names}
        for match in _ALIAS_HINT.finditer(text):
            alias = match.group(1).strip()
            if not alias:
                continue
            earlier = [
                item
                for item in occurrences
                if item[0] <= match.start() and match.start() - item[0] <= 120
            ]
            if not earlier:
                continue
            owner = max(
                earlier,
                key=lambda item: (
                    len(set(alias) & set(item[1])) > 0,
                    item[0],
                ),
            )[1]
            if alias != owner and alias not in aliases_by_name[owner]:
                aliases_by_name[owner].append(alias)
        for name, aliases in known.items():
            if name in aliases_by_name:
                aliases_by_name[name] = list(
                    dict.fromkeys([*aliases, *aliases_by_name[name]])
                )
        alias_owner = {
            alias: name
            for name, aliases in aliases_by_name.items()
            for alias in aliases
        }
        names = [
            name
            for name in names
            if name not in alias_owner or alias_owner.get(name) == name
        ]

        timeline = _mock_timeline(text)
        characters: list[BibleCharacterDraft] = []
        for name in names:
            aliases = [*known.get(name, []), *aliases_by_name.get(name, [])]
            alias_drafts = [
                {"name": alias, "confidence": 0.45, "basis": "inferred"}
                for alias in dict.fromkeys(aliases)
                if alias and alias != name
            ]
            stage = _mock_age_stage(text, name)
            arc_note = None
            if stage != "unknown":
                arc_note = f"本章处于{_STAGE_LABELS.get(stage, stage)}阶段（模拟模式启发式）。"
            characters.append(
                BibleCharacterDraft(
                    name=name,
                    aliases=alias_drafts,
                    gender="unknown",
                    age_range=stage,
                    character_type="unknown",
                    role_tier="unknown",
                    identity_description="模拟模式仅用于流程验证，不代表真实分析结果。",
                    personality=["待确认"],
                    arc_note=arc_note,
                    evidence=[text[:80]] if text else [],
                    confidence=0.35,
                    is_provisional=True,
                )
            )
        return BibleChapterResult(
            characters=characters,
            timeline=timeline,
            meta={"mock": True, "prompt_version": "mock", "characters": len(characters)},
        )

    def merge_bible_candidates(
        self, candidates: list[dict], context: dict | None = None
    ) -> BibleMergeResult:
        context = context or {}
        total_chapters = max(1, int(context.get("total_chapters") or 1))
        characters: list[BibleMergeCharacterDraft] = []
        for item in candidates:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            observations = [
                chapter
                for chapter in (item.get("chapters") or [])
                if isinstance(chapter, dict)
            ]
            sequences = sorted(
                {
                    int(chapter.get("sequence") or 0)
                    for chapter in observations
                    if chapter.get("sequence")
                }
            )
            count = len(sequences)
            if count >= max(2, math.ceil(total_chapters * 0.6)):
                tier = "protagonist"
            elif count >= max(2, math.ceil(total_chapters * 0.3)):
                tier = "major"
            elif count >= 2:
                tier = "minor"
            else:
                tier = "extra"
            stage_buckets: dict[str, list[dict]] = {}
            for chapter in observations:
                stage_buckets.setdefault(str(chapter.get("age_stage") or "unknown"), []).append(
                    chapter
                )
            arcs: list[BibleArcDraft] = []
            for stage, rows in stage_buckets.items():
                rows.sort(key=lambda row: int(row.get("sequence") or 0))
                notes = [
                    str(row.get("arc_note") or "").strip()
                    for row in rows
                    if str(row.get("arc_note") or "").strip()
                ]
                labels = [
                    str(row.get("story_time_label") or "").strip()
                    for row in rows
                    if str(row.get("story_time_label") or "").strip()
                ]
                arcs.append(
                    BibleArcDraft(
                        label=_STAGE_LABELS.get(stage, "未分阶段"),
                        age_stage=stage if stage in _STAGE_LABELS else "unknown",
                        chapter_start=int(rows[0].get("sequence") or 0) or None,
                        chapter_end=int(rows[-1].get("sequence") or 0) or None,
                        story_time_start=labels[0] if labels else None,
                        story_time_end=labels[-1] if labels else None,
                        summary=" ".join(dict.fromkeys(notes))[:200] or None,
                        confidence=0.35,
                    )
                )
            if not arcs:
                arcs.append(
                    BibleArcDraft(
                        label="未分阶段",
                        age_stage="unknown",
                        chapter_start=sequences[0] if sequences else None,
                        chapter_end=sequences[-1] if sequences else None,
                        summary=None,
                        confidence=0.2,
                    )
                )
            characters.append(
                BibleMergeCharacterDraft(
                    name=name,
                    merge_names=[],
                    role_tier=tier,
                    overview=str(item.get("identity") or "").strip() or None,
                    arcs=arcs,
                    confidence=0.4,
                )
            )
        return BibleMergeResult(
            characters=characters,
            summary=(
                f"模拟模式：共 {total_chapters} 章，归并出 {len(characters)} 个候选角色。"
                "该结果只验证流程，不代表真实全书分析。"
            ),
            notes=[],
            meta={"mock": True, "prompt_version": "mock"},
        )

    # ---------------------------------------------------------- v0.5.0 P3

    def analyze_chapter_scenes(
        self,
        paragraphs: list[dict],
        known_characters: list[dict],
        context: dict | None = None,
    ) -> ChapterSceneResult:
        context = context or {}
        units = [
            {
                "index": int(item.get("index", position)),
                "text": str(item.get("text") or "").strip(),
            }
            for position, item in enumerate(paragraphs)
            if str(item.get("text") or "").strip()
        ]
        if not units:
            return ChapterSceneResult(
                meta={"mock": True, "prompt_version": "mock", "scenes": 0}
            )

        known: list[tuple[str, list[str], str | None]] = []
        for item in known_characters:
            name = str(item.get("canonical_name") or item.get("name") or "").strip()
            if not name:
                continue
            aliases = [
                str(alias.get("name") if isinstance(alias, dict) else alias).strip()
                for alias in (item.get("aliases") or [])
                if (alias.get("name") if isinstance(alias, dict) else alias)
            ]
            known.append((name, [alias for alias in aliases if alias], item.get("voice_description")))

        chapter_time = context.get("chapter_time") or {}
        chapter_mode = str(chapter_time.get("time_mode") or "unknown")
        chunk_size = max(1, math.ceil(len(units) / 3))
        scenes: list[SceneDraft] = []
        for offset in range(0, len(units), chunk_size):
            group = units[offset : offset + chunk_size]
            text = "\n".join(item["text"] for item in group)
            timeline = _mock_timeline(text)
            mode = timeline.time_mode
            label = timeline.story_time_label
            if mode in {"present", "unknown"} and chapter_mode in {
                "flashback",
                "flashforward",
                "dream",
            }:
                mode = chapter_mode
                label = label or chapter_time.get("story_time_label")
            states: list[SceneCharacterStateDraft] = []
            for name, aliases, voice_description in known:
                if name not in text and not any(alias in text for alias in aliases):
                    continue
                speaker = bool(
                    re.search(
                        re.escape(name) + r".{0,6}(?:说|道|问|答|喊|叫|开口)",
                        text,
                    )
                )
                states.append(
                    SceneCharacterStateDraft(
                        name=name,
                        presence="speaker" if speaker else "present",
                        age_stage=_mock_age_stage(text, name),
                        voice_note=voice_description
                        or "模拟模式：仅验证分场与状态流程。",
                        state_tags=["模拟"],
                        confidence=0.45,
                        evidence=[text[:80]],
                    )
                )
            title = " ".join(group[0]["text"].split())[:24] or None
            scenes.append(
                SceneDraft(
                    paragraph_start=group[0]["index"],
                    paragraph_end=group[-1]["index"],
                    title=title,
                    summary=" ".join(text.split())[:160],
                    story_time_label=label,
                    time_mode=mode,
                    characters=states,
                    confidence=0.45,
                    evidence=[text[:100]],
                )
            )
        return ChapterSceneResult(
            scenes=scenes,
            notes=["模拟模式分场仅用于流程验证，不代表真实分场结果。"],
            meta={"mock": True, "prompt_version": "mock", "scenes": len(scenes)},
        )

    # ---------------------------------------------------------- v0.5.0 P4

    def adapt_scene_script(
        self,
        scene_text: str,
        known_characters: list[dict],
        context: dict | None = None,
    ) -> ChapterScriptResult:
        context = context or {}
        text = scene_text or ""
        if not text.strip():
            return ChapterScriptResult(
                meta={"mock": True, "prompt_version": "mock", "units": 0}
            )
        names = [
            str(item.get("canonical_name") or item.get("name") or "").strip()
            for item in known_characters
            if str(item.get("canonical_name") or item.get("name") or "").strip()
        ]
        units: list[ScriptUnitDraft] = []
        opening = re.compile(r"^\s*[“「『](?P<sound>[^”」』]{1,6})[”」』]的一声[，,]?")
        quoted = re.compile(r"[“「『]([^”」』]+)[”」』]")
        sentence = re.compile(r"[^\n。！？!?；;]+[。！？!?；;]?")
        for match in sentence.finditer(text):
            raw = match.group(0)
            if not raw.strip():
                continue
            start = len(raw) - len(raw.lstrip())
            end = len(raw.rstrip())
            raw = raw[start:end]
            leading = opening.match(raw)
            if leading and re.fullmatch(r"[啪砰咚哗轰吱呀当叮咣]+", leading.group("sound")):
                sound = leading.group("sound")
                cue_text = leading.group(0).strip()
                units.append(
                    ScriptUnitDraft(
                        unit_type="sfx",
                        source_text=cue_text,
                        text="",
                        display_text=f"音效：{sound}",
                        speaker_type="none",
                        sfx={"label": sound, "description": "模拟模式音效 cue"},
                        confidence=0.45,
                        evidence=[cue_text],
                    )
                )
                remainder = raw[leading.end() :].strip()
                if remainder:
                    unit_type = "inner_monologue" if any(
                        marker in remainder for marker in ("心想", "心里", "暗想", "默念")
                    ) else "narration"
                    units.append(
                        ScriptUnitDraft(
                            unit_type=unit_type,
                            source_text=remainder,
                            text=remainder,
                            display_text=remainder,
                            speaker_type="narrator",
                            confidence=0.4,
                            evidence=[remainder[:80]],
                        )
                    )
                continue
            quotes = list(quoted.finditer(raw))
            if quotes:
                speaker_name = None
                first_quote = quotes[0].start()
                before = raw[:first_quote]
                hits = [name for name in names if name in before or name in raw]
                if hits:
                    speaker_name = max(hits, key=len)
                spoken = " ".join(
                    item.group(1).strip() for item in quotes if item.group(1).strip()
                )
                if spoken:
                    units.append(
                        ScriptUnitDraft(
                            unit_type="dialogue",
                            source_text=raw,
                            text=spoken,
                            display_text=raw,
                            speaker_type="character" if speaker_name else "unknown",
                            speaker_name=speaker_name,
                            confidence=0.4 if speaker_name else 0.2,
                            evidence=[raw[:80]],
                        )
                    )
                    continue
            unit_type = "inner_monologue" if any(
                marker in raw for marker in ("心想", "心里", "暗想", "默念")
            ) else "narration"
            units.append(
                ScriptUnitDraft(
                    unit_type=unit_type,
                    source_text=raw,
                    text=raw,
                    display_text=raw,
                    speaker_type="narrator",
                    confidence=0.4,
                    evidence=[raw[:80]],
                )
            )
        return ChapterScriptResult(
            units=units,
            summary="模拟模式：按句子切分并标记对白/旁白/音效 cue，仅用于流程验证。",
            notes=["模拟模式剧本仅用于流程验证，不代表真实改编质量。"],
            meta={"mock": True, "prompt_version": "mock", "units": len(units)},
        )

    def synthesize_bible_timeline(
        self, chapters: list[dict], context: dict | None = None
    ) -> BibleTimelineResult:
        rows: list[dict] = [item for item in chapters if isinstance(item, dict)]
        nonlinear = any(
            str(item.get("time_mode") or "unknown") not in {"present", "unknown"}
            for item in rows
        )
        ordered = sorted(
            rows,
            key=lambda item: (
                0
                if str(item.get("time_mode") or "unknown") not in {"present", "unknown"}
                else 1,
                int(item.get("sequence") or 0),
            ),
        )
        order_by_sequence = {
            int(item.get("sequence") or 0): index + 1 for index, item in enumerate(ordered)
        }
        return BibleTimelineResult(
            narrative_mode="nonlinear" if nonlinear else "linear",
            chapters=[
                BibleTimelineChapterDraft(
                    sequence=int(item.get("sequence") or 0),
                    story_time_label=item.get("story_time_label"),
                    story_order=order_by_sequence.get(int(item.get("sequence") or 0)),
                    time_mode=str(item.get("time_mode") or "unknown"),
                )
                for item in rows
            ],
            notes=["模拟模式按“非顺叙章节前置”生成剧情顺序，仅供流程验证。"]
            if nonlinear
            else [],
            meta={"mock": True, "prompt_version": "mock"},
        )


class MockVoiceDesignProvider(VoiceDesignProvider):
    name = "mock_voice_design"
    model = "mock-voice-design"
    is_mock = True

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="Mock VoiceDesign 可用（不调用模型）", meta={"mock": True})

    def design_voice(
        self,
        description: str,
        reference_text: str,
        language: str = "zh-CN",
        *,
        seed: int | None = None,
    ) -> VoiceCandidate:
        if seed is None:
            digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
            seed = int(digest[:8], 16) % 10000
        audio = _tone_wav(reference_text, frequency=180 + seed % 200, duration_ms=1200)
        return VoiceCandidate(
            candidate_id=f"mock_voice_{seed:04d}",
            name=f"模拟音色 {seed:04d}",
            description=description,
            instruct_text=description,
            reference_text=reference_text,
            seed=seed,
            audio=audio,
            metadata={"language": language, "mock": True},
        )

    def preview_voice(self, candidate: VoiceCandidate, text: str) -> AudioResult:
        return _tone_wav(text, frequency=180 + candidate.seed % 200, duration_ms=len(text) * 80)


class MockTTSProvider(TTSProvider):
    name = "mock_tts"
    model = "mock-tts"
    is_mock = True

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="Mock TTS 可用（生成提示音，不是语音）", meta={"mock": True})

    def load_voice_profile(self, profile: dict) -> None:
        return None

    def synthesize(
        self,
        text: str,
        voice_profile: dict,
        *,
        emotion: str | None = None,
        intensity: float = 1.0,
        delivery: str | None = None,
        instruction: str | None = None,
        speed: float = 1.0,
    ) -> AudioResult:
        seed = int(voice_profile.get("seed", 0) or 0)
        duration = int(max(400, len(text) * 70 / max(0.5, speed)))
        result = _tone_wav(text, frequency=160 + seed % 240, duration_ms=duration)
        return AudioResult(
            audio_bytes=result.audio_bytes,
            sample_rate=result.sample_rate,
            channels=result.channels,
            duration_ms=result.duration_ms,
            format=result.format,
            provider=self.name,
            model=self.model,
            is_mock=True,
            metadata={
                "emotion": emotion,
                "intensity": intensity,
                "delivery": delivery,
                "instruction": instruction,
                "speed": speed,
                "mock": True,
            },
        )