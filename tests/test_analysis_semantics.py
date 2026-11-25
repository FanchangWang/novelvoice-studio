from __future__ import annotations

from typing import Any

from novelvoice_studio.ai.base import (
    AliasDraft,
    ChapterSummaryResult,
    CharacterAnalysisResult,
    CharacterDraft,
    EvidenceDraft,
    LLMProvider,
    ProviderHealth,
    RelationshipDraft,
    SegmentAnalysisResult,
    SegmentDraft,
)
from novelvoice_studio.ai.ollama import ProviderResponseError
from novelvoice_studio.models import Chapter, Character
from novelvoice_studio.services.paths import new_id

STORY = """第一章 测试

林见秋推门。她看见沈砚。

“小秋，你来了。”沈砚说。

沈砚心里想：她终于回来了。

“谁在外面？”林见秋问。

最后一段没有AI覆盖。
"""


def _import_story(client, upload, title: str = "脚本测试") -> str:
    response = upload("/api/v1/books/import", STORY, title=title)
    assert response.status_code == 201
    return response.json()["book"]["id"]


def _paragraph(context: dict, prefix: str) -> dict:
    for item in context["paragraphs"]:
        if str(item["text"]).startswith(prefix):
            return item
    raise AssertionError(f"找不到段落 {prefix}")


class ScriptedLLM(LLMProvider):
    name = "scripted"
    model = "scripted-v1"
    is_mock = False
    mutate = False

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="scripted")

    def list_models(self) -> list[str]:
        return [self.model]

    def analyze_characters(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> CharacterAnalysisResult:
        identity = "AI 生成身份" if not self.mutate else "AI 想覆盖身份"
        gender = "female"
        voice = "清冷女声" if not self.mutate else "AI 想覆盖音色"
        return CharacterAnalysisResult(
            characters=[
                CharacterDraft(
                    name="林见秋",
                    aliases=[
                        AliasDraft(
                            name="小秋",
                            confidence=0.85,
                            basis="explicit",
                            evidence=[EvidenceDraft(quote="“小秋，你来了。”")],
                        ),
                        AliasDraft(name="秋姐", confidence=0.3, basis="inferred"),
                    ],
                    gender=gender,
                    gender_basis="explicit",
                    age_range="young_adult",
                    age_basis="inferred",
                    character_type="human",
                    identity_description=identity,
                    personality=["冷静"],
                    voice_description=voice,
                    evidence=[EvidenceDraft(quote="林见秋推门。")],
                    confidence=0.9,
                    is_provisional=False,
                ),
                CharacterDraft(
                    name="沈砚",
                    aliases=[
                        AliasDraft(name="老沈", confidence=0.8, basis="explicit")
                    ],
                    gender="male",
                    gender_basis="explicit",
                    age_range="adult",
                    character_type="human",
                    voice_description="低沉男声",
                    confidence=0.8,
                    is_provisional=False,
                ),
            ],
            relationships=[
                RelationshipDraft(
                    source_name="林见秋",
                    target_name="沈砚",
                    relationship_type="朋友",
                    description="多年旧识",
                    evidence=[EvidenceDraft(quote="她看见沈砚。")],
                    confidence=0.8,
                    is_provisional=False,
                )
            ],
            meta={"scripted": True},
        )

    def analyze_segments(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> SegmentAnalysisResult:
        assert context is not None
        first = _paragraph(context, "林见秋推门")
        dialogue = _paragraph(context, "“小秋")
        inner = _paragraph(context, "沈砚心里想")
        low = _paragraph(context, "“谁在外面")

        cut = first["text"].index("。") + 1
        quote_end = dialogue["text"].index("”") + 1
        low_end = low["text"].index("”") + 1
        drafts = [
            SegmentDraft(
                paragraph_index=first["index"],
                source_text=first["text"][:cut],
                segment_type="narration",
                speaker_type="narrator",
                speaker_confidence=1.0,
                emotion_confidence=0.9,
            ),
            SegmentDraft(
                paragraph_index=first["index"],
                source_text=first["text"][cut:],
                segment_type="narration",
                speaker_type="narrator",
                speaker_confidence=1.0,
                emotion_confidence=0.9,
            ),
            SegmentDraft(
                paragraph_index=first["index"],
                source_text="不存在的句子",
                segment_type="narration",
                speaker_type="narrator",
                speaker_confidence=1.0,
            ),
            SegmentDraft(
                paragraph_index=dialogue["index"],
                source_text=dialogue["text"][:quote_end],
                segment_type="dialogue",
                speaker_type="character",
                speaker_name="林见秋",
                speaker_confidence=0.92,
                emotion="tender",
                emotion_confidence=0.8,
                intensity=2,
                delivery="normal",
                tts_instruction="温柔地回应",
                evidence=[EvidenceDraft(quote="“小秋，你来了。”", reason="称呼小秋")],
            ),
            SegmentDraft(
                paragraph_index=dialogue["index"],
                source_text=dialogue["text"][quote_end:],
                segment_type="narration",
                speaker_type="narrator",
                speaker_confidence=1.0,
            ),
            SegmentDraft(
                paragraph_index=inner["index"],
                source_text=inner["text"],
                segment_type="inner_monologue",
                speaker_type="character",
                speaker_name="沈砚",
                speaker_confidence=0.9,
                emotion="tender",
                emotion_confidence=0.6,
            ),
            SegmentDraft(
                paragraph_index=low["index"],
                source_text=low["text"][:low_end],
                segment_type="dialogue",
                speaker_type="character",
                speaker_name="林见秋",
                speaker_confidence=0.4,
                emotion_confidence=0.3,
            ),
            SegmentDraft(
                paragraph_index=low["index"],
                source_text=low["text"][low_end:],
                segment_type="narration",
                speaker_type="narrator",
                speaker_confidence=1.0,
            ),
            SegmentDraft(
                paragraph_index=99,
                source_text="幽灵文本",
                segment_type="narration",
                speaker_type="narrator",
            ),
        ]
        return SegmentAnalysisResult(segments=drafts, meta={"scripted": True})

    def summarize_chapter(
        self, text: str, known_characters: list[dict]
    ) -> ChapterSummaryResult:
        return ChapterSummaryResult(
            summary="林见秋与沈砚在门口交谈。",
            key_events=["林见秋开门", "沈砚来访"],
            characters=["林见秋", "沈砚"],
            meta={"scripted": True},
        )


class LowEmotionLLM(ScriptedLLM):
    def analyze_segments(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> SegmentAnalysisResult:
        result = super().analyze_segments(text, known_characters, context)
        for draft in result.segments:
            if draft.segment_type == "inner_monologue":
                draft.emotion_confidence = 0.2
        return result


class ProfileRefreshLLM(ScriptedLLM):
    def __init__(
        self,
        *,
        identity: str,
        profile_confidence: float,
        reverse_relationship: bool = False,
    ) -> None:
        self.identity = identity
        self.profile_confidence = profile_confidence
        self.reverse_relationship = reverse_relationship

    def analyze_characters(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> CharacterAnalysisResult:
        result = super().analyze_characters(text, known_characters, context)
        for draft in result.characters:
            if draft.name == "林见秋":
                draft.identity_description = self.identity
                draft.confidence = self.profile_confidence
        if self.reverse_relationship:
            result.relationships.append(
                RelationshipDraft(
                    source_name="沈砚",
                    target_name="林见秋",
                    relationship_type="朋友",
                    description="模型反向重复的同一条关系",
                    evidence=[EvidenceDraft(quote="她看见沈砚。")],
                    confidence=0.8,
                    is_provisional=False,
                )
            )
        return result


class FailingLLM(ScriptedLLM):
    def analyze_characters(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> CharacterAnalysisResult:
        raise ProviderResponseError("脚本化失败")


class AmbiguousLLM(LLMProvider):
    name = "ambiguous"
    model = "ambiguous-v1"
    is_mock = False

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="ambiguous")

    def list_models(self) -> list[str]:
        return [self.model]

    def analyze_characters(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> CharacterAnalysisResult:
        return CharacterAnalysisResult(characters=[], relationships=[])

    def analyze_segments(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> SegmentAnalysisResult:
        assert context is not None
        drafts: list[SegmentDraft] = []
        for paragraph in context["paragraphs"]:
            content = str(paragraph["text"])
            if content.startswith("“小秋"):
                drafts.append(
                    SegmentDraft(
                        paragraph_index=paragraph["index"],
                        source_text=content,
                        segment_type="dialogue",
                        speaker_type="character",
                        speaker_name="小秋",
                        speaker_confidence=0.95,
                    )
                )
            else:
                drafts.append(
                    SegmentDraft(
                        paragraph_index=paragraph["index"],
                        source_text=content,
                        segment_type="narration",
                        speaker_type="narrator",
                        speaker_confidence=1.0,
                    )
                )
        return SegmentAnalysisResult(segments=drafts)

    def summarize_chapter(
        self, text: str, known_characters: list[dict]
    ) -> ChapterSummaryResult:
        return ChapterSummaryResult(summary="有歧义说话人的章节。")


class RecordingLLM(LLMProvider):
    name = "recording"
    model = "recording-v1"
    is_mock = False

    def __init__(self) -> None:
        self.segment_contexts: list[dict[str, Any]] = []
        self.character_contexts: list[dict[str, Any]] = []

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="recording")

    def list_models(self) -> list[str]:
        return [self.model]

    def analyze_characters(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> CharacterAnalysisResult:
        self.character_contexts.append(context or {})
        return CharacterAnalysisResult()

    def analyze_segments(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> SegmentAnalysisResult:
        assert context is not None
        self.segment_contexts.append(context)
        return SegmentAnalysisResult(
            segments=[
                SegmentDraft(
                    paragraph_index=item["index"],
                    source_text=item["text"],
                    segment_type="narration",
                    speaker_type="narrator",
                    speaker_confidence=1.0,
                )
                for item in context["paragraphs"]
            ]
        )

    def summarize_chapter(
        self, text: str, known_characters: list[dict]
    ) -> ChapterSummaryResult:
        return ChapterSummaryResult(summary="记录型章节摘要。")


def _segments(client, chapter_id: str) -> list[dict]:
    return client.get(f"/api/v1/chapters/{chapter_id}/segments").json()["items"]


def _characters(client, book_id: str) -> dict[str, dict]:
    items = client.get(f"/api/v1/books/{book_id}/characters").json()["items"]
    return {item["canonical_name"]: item for item in items}


def _analyze(client, chapter_id: str):
    return client.post(f"/api/v1/chapters/{chapter_id}/analysis")


def test_scripted_analysis_alias_review_alignment_and_lock(
    client, upload, db_session
) -> None:
    book_id = _import_story(client, upload)
    chapter = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"][0]
    client.app.state.registry.llm = ScriptedLLM()

    response = _analyze(client, chapter["id"])
    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter"]["analysis_status"] == "needs_review"
    assert payload["segments_written"] == 9
    assert payload["meta"]["fallback_segments"] == 2
    assert payload["meta"]["is_mock"] is False
    warnings = payload["meta"]["warnings"]
    assert any("无法与原文逐字对齐" in item for item in warnings)
    assert any("无效段落编号" in item for item in warnings)
    assert any("按旁白处理" in item for item in warnings)

    detail = client.get(f"/api/v1/chapters/{chapter['id']}").json()
    items = _segments(client, chapter["id"])
    assert [item["segment_index"] for item in items] == list(range(9))
    for item in items:
        start = item["source_start_offset"]
        end = item["source_end_offset"]
        assert detail["text"][
            start - chapter["source_start_offset"] : end - chapter["source_start_offset"]
        ] == item["source_text"]
    for previous, current in zip(items, items[1:]):
        assert previous["source_end_offset"] <= current["source_start_offset"]
    assert all(
        item["source_text"] not in {"不存在的句子", "幽灵文本"} for item in items
    )

    characters = _characters(client, book_id)
    assert set(characters) == {"林见秋", "沈砚"}
    lin = characters["林见秋"]
    assert [item["name"] for item in lin["aliases"]] == ["小秋"]
    assert [item["name"] for item in lin["alias_candidates"]] == ["秋姐"]
    assert lin["alias_candidates"][0]["reason"].startswith("置信度不足")
    assert lin["gender"] == "female"
    assert lin["age_range"] == "young_adult"
    assert lin["is_provisional"] is False
    assert [item["name"] for item in characters["沈砚"]["aliases"]] == ["老沈"]

    relationships = client.get(f"/api/v1/books/{book_id}/relationships").json()["items"]
    assert len(relationships) == 1
    assert relationships[0]["source_name"] == "林见秋"
    assert relationships[0]["target_name"] == "沈砚"
    assert relationships[0]["relationship_type"] == "朋友"

    resolved = next(
        item for item in items if item["source_text"].startswith("“小秋")
    )
    assert resolved["segment_type"] == "dialogue"
    assert resolved["speaker_type"] == "character"
    assert resolved["speaker_id"] == lin["id"]
    assert resolved["review_status"] == "auto"
    assert resolved["emotion"] == "tender"

    low = next(item for item in items if item["source_text"].startswith("“谁在外面"))
    assert low["speaker_type"] == "character"
    assert low["speaker_id"] == lin["id"]
    assert low["speaker_confidence"] == 0.4
    assert low["review_status"] == "needs_review"
    assert low["low_speaker_confidence"] is True
    assert any("置信度不足" in (entry.get("reason") or "") for entry in low["evidence"])

    inner = next(item for item in items if item["segment_type"] == "inner_monologue")
    assert inner["speaker_type"] == "narrator"
    assert inner["speaker_id"] is None
    assert inner["review_status"] == "auto"

    fallback = next(item for item in items if item["source_text"] == "最后一段没有AI覆盖。")
    assert fallback["segment_type"] == "narration"
    assert fallback["speaker_type"] == "narrator"
    assert fallback["review_status"] == "needs_review"
    assert any("未覆盖" in (entry.get("reason") or "") for entry in fallback["evidence"])

    edited = client.patch(
        f"/api/v1/characters/{lin['id']}",
        json={"gender": "male", "voice_description": "人工音色"},
    )
    assert edited.status_code == 200
    assert edited.json()["user_locked"] is True

    mutated = ScriptedLLM()
    mutated.mutate = True
    client.app.state.registry.llm = mutated
    rerun = _analyze(client, chapter["id"])
    assert rerun.status_code == 200
    after = _characters(client, book_id)["林见秋"]
    assert after["gender"] == "male"
    assert after["voice_description"] == "人工音色"
    assert after["user_locked"] is True
    assert [item["name"] for item in after["aliases"]] == ["小秋"]
    assert [item["name"] for item in after["alias_candidates"]] == ["秋姐"]
    assert after["identity_description"] == "AI 生成身份"


def test_unlocked_profile_refreshes_only_with_high_confidence(
    client, upload
) -> None:
    book_id = _import_story(client, upload, title="角色资料刷新")
    chapter = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"][0]

    client.app.state.registry.llm = ProfileRefreshLLM(
        identity="初始低置信身份", profile_confidence=0.5
    )
    assert _analyze(client, chapter["id"]).status_code == 200
    assert _characters(client, book_id)["林见秋"]["identity_description"] == "初始低置信身份"

    client.app.state.registry.llm = ProfileRefreshLLM(
        identity="高置信修正身份",
        profile_confidence=0.9,
        reverse_relationship=True,
    )
    assert _analyze(client, chapter["id"]).status_code == 200
    after = _characters(client, book_id)["林见秋"]
    assert after["identity_description"] == "高置信修正身份"

    relationships = client.get(f"/api/v1/books/{book_id}/relationships").json()["items"]
    assert len(relationships) == 1
    assert relationships[0]["source_name"] == "林见秋"
    assert relationships[0]["target_name"] == "沈砚"
    assert relationships[0]["description"] == "多年旧识"

    client.app.state.registry.llm = ProfileRefreshLLM(
        identity="低置信错误身份", profile_confidence=0.4
    )
    assert _analyze(client, chapter["id"]).status_code == 200
    assert _characters(client, book_id)["林见秋"]["identity_description"] == "高置信修正身份"


def test_ambiguous_alias_forces_review(client, upload, db_session) -> None:
    book_id = _import_story(client, upload, title="歧义角色")
    chapter = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"][0]
    alias = {
        "name": "小秋",
        "confidence": 1.0,
        "basis": "explicit",
        "chapter_id": None,
        "evidence": [],
        "source": "manual",
    }
    for name in ("林见秋", "林见春"):
        db_session.add(
            Character(
                id=new_id("char"),
                book_id=book_id,
                canonical_name=name,
                aliases_json=[alias],
                alias_candidates_json=[],
                gender="female",
                gender_basis="explicit",
                age_range="unknown",
                age_basis="unknown",
                character_type="human",
                personality_json=[],
                evidence_json=[],
                confidence=1.0,
                is_provisional=False,
                user_locked=True,
                merge_log_json=[],
            )
        )
    db_session.commit()

    client.app.state.registry.llm = AmbiguousLLM()
    response = _analyze(client, chapter["id"])
    assert response.status_code == 200

    items = _segments(client, chapter["id"])
    ambiguous = next(item for item in items if "小秋，你来了" in item["source_text"])
    assert ambiguous["segment_type"] == "dialogue"
    assert ambiguous["speaker_type"] == "unknown"
    assert ambiguous["speaker_id"] is None
    assert ambiguous["speaker_confidence"] <= 0.3
    assert ambiguous["review_status"] == "needs_review"
    assert any(
        "无法可靠判断" in (entry.get("reason") or "") for entry in ambiguous["evidence"]
    )


def test_analysis_failure_preserves_previous_results(client, upload) -> None:
    book_id = _import_story(client, upload, title="失败保留")
    chapter = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"][0]
    client.app.state.registry.llm = ScriptedLLM()
    assert _analyze(client, chapter["id"]).status_code == 200
    before_detail = client.get(f"/api/v1/chapters/{chapter['id']}").json()
    before_segments = _segments(client, chapter["id"])
    before_ids = {item["id"] for item in before_segments}

    client.app.state.registry.llm = FailingLLM()
    failed = _analyze(client, chapter["id"])
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "llm_invalid_response"

    after_detail = client.get(f"/api/v1/chapters/{chapter['id']}").json()
    assert after_detail["analysis_status"] == "failed"
    assert after_detail["summary"] == before_detail["summary"]
    assert after_detail["analysis_meta"]["error"]["code"] == "llm_invalid_response"
    after_segments = _segments(client, chapter["id"])
    assert {item["id"] for item in after_segments} == before_ids
    assert [item["speech_text"] for item in after_segments] == [
        item["speech_text"] for item in before_segments
    ]


def test_previous_summary_is_passed_to_next_chapter(
    client, upload, sample_text: str, db_session
) -> None:
    response = upload("/api/v1/books/import", sample_text, title="上下文测试")
    book_id = response.json()["book"]["id"]
    chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]

    row = db_session.get(Chapter, chapters[0]["id"])
    assert row is not None
    row.summary = "前一章摘要"
    row.analysis_status = "completed"
    db_session.commit()

    llm = RecordingLLM()
    client.app.state.registry.llm = llm
    analyzed = _analyze(client, chapters[1]["id"])
    assert analyzed.status_code == 200
    assert llm.character_contexts[-1]["previous_summary"] == "前一章摘要"
    assert llm.segment_contexts[-1]["previous_summary"] == "前一章摘要"
    assert llm.character_contexts[-1]["chapter_sequence"] == 2
    assert llm.segment_contexts[-1]["chapter_title"] == chapters[1]["title"]


def test_low_emotion_confidence_enters_review(client, upload) -> None:
    book_id = _import_story(client, upload, title="情绪置信度测试")
    chapter = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"][0]
    client.app.state.registry.llm = LowEmotionLLM()

    response = _analyze(client, chapter["id"])
    assert response.status_code == 200

    items = _segments(client, chapter["id"])
    inner = next(item for item in items if item["segment_type"] == "inner_monologue")
    assert inner["review_status"] == "needs_review"
    assert inner["low_emotion_confidence"] is True
    assert any(
        "情绪置信度不足" in (entry.get("reason") or "")
        for entry in inner["evidence"]
    )