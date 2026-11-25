"""阶段 2 角色/关系/片段的序列化。"""

from __future__ import annotations

from typing import Any

from novelvoice_studio.models import Character, CharacterRelationship, GenerationJob, Segment
from novelvoice_studio.schemas.analysis import (
    AliasOut,
    AnalysisJobOut,
    CharacterOut,
    RelationshipOut,
    SegmentOut,
)


def _as_alias_list(raw: Any) -> list[AliasOut]:
    items: list[AliasOut] = []
    if not isinstance(raw, list):
        return items
    for item in raw:
        if isinstance(item, str):
            items.append(AliasOut(name=item, confidence=0.0, basis="unknown"))
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        evidence = item.get("evidence")
        items.append(
            AliasOut(
                name=name,
                confidence=float(item.get("confidence") or 0.0),
                basis=str(item.get("basis") or "unknown"),
                chapter_id=item.get("chapter_id"),
                evidence=evidence if isinstance(evidence, list) else [],
                source=str(item.get("source") or "ai"),
                conflict_character_id=item.get("conflict_character_id"),
                reason=item.get("reason"),
            )
        )
    return items


def to_character_out(
    character: Character, merged_source_ids: list[str] | None = None
) -> CharacterOut:
    return CharacterOut(
        id=character.id,
        book_id=character.book_id,
        canonical_name=character.canonical_name,
        aliases=_as_alias_list(character.aliases_json),
        alias_candidates=_as_alias_list(character.alias_candidates_json),
        gender=character.gender,
        gender_basis=character.gender_basis,
        age_range=character.age_range,
        age_basis=character.age_basis,
        character_type=character.character_type,
        identity_description=character.identity_description,
        personality=[str(item) for item in (character.personality_json or [])],
        personality_desc=character.personality_desc,
        appearance_desc=character.appearance_desc,
        voice_description=character.voice_description,
        first_chapter_id=character.first_chapter_id,
        evidence=character.evidence_json or [],
        confidence=character.confidence,
        is_provisional=character.is_provisional,
        user_locked=character.user_locked,
        merged_into_id=character.merged_into_id,
        merge_log=character.merge_log_json or [],
        merged_source_ids=merged_source_ids or [],
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


def to_relationship_out(
    relationship: CharacterRelationship,
    names: dict[str, str] | None = None,
) -> RelationshipOut:
    names = names or {}
    return RelationshipOut(
        id=relationship.id,
        book_id=relationship.book_id,
        source_character_id=relationship.source_character_id,
        target_character_id=relationship.target_character_id,
        source_name=names.get(relationship.source_character_id),
        target_name=names.get(relationship.target_character_id),
        relationship_type=relationship.relationship_type,
        description=relationship.description,
        evidence=relationship.evidence_json or [],
        confidence=relationship.confidence,
        is_provisional=relationship.is_provisional,
        created_at=relationship.created_at,
        updated_at=relationship.updated_at,
    )


def to_segment_out(
    segment: Segment,
    *,
    speaker_threshold: float = 0.6,
    emotion_threshold: float = 0.5,
) -> SegmentOut:
    return SegmentOut(
        id=segment.id,
        book_id=segment.book_id,
        chapter_id=segment.chapter_id,
        segment_index=segment.segment_index,
        source_text=segment.source_text,
        speech_text=segment.speech_text,
        segment_type=segment.segment_type,
        speaker_type=segment.speaker_type,
        speaker_id=segment.speaker_id,
        speaker_name_snapshot=segment.speaker_name_snapshot,
        speaker_confidence=segment.speaker_confidence,
        emotion=segment.emotion,
        emotion_confidence=segment.emotion_confidence,
        intensity=segment.intensity,
        delivery=segment.delivery,
        tts_instruction=segment.tts_instruction,
        speed=segment.speed,
        pause_before_ms=segment.pause_before_ms,
        pause_after_ms=segment.pause_after_ms,
        review_status=segment.review_status,
        audio_status=segment.audio_status,
        voice_profile_id=segment.voice_profile_id,
        current_audio_asset_id=segment.current_audio_asset_id,
        user_locked_fields=[str(item) for item in (segment.user_locked_fields_json or [])],
        text_version=segment.text_version,
        source_start_offset=segment.source_start_offset,
        source_end_offset=segment.source_end_offset,
        evidence=segment.evidence_json or [],
        low_speaker_confidence=(
            segment.speaker_type == "character"
            and segment.speaker_confidence is not None
            and segment.speaker_confidence < speaker_threshold
        ),
        low_emotion_confidence=(
            segment.emotion_confidence is not None
            and segment.emotion_confidence < emotion_threshold
        ),
        created_at=segment.created_at,
        updated_at=segment.updated_at,
    )


def to_job_out(job: GenerationJob) -> AnalysisJobOut:
    return AnalysisJobOut(
        id=job.id,
        book_id=job.book_id,
        job_type=job.job_type,
        status=job.status,
        total_items=job.total_items,
        completed_items=job.completed_items,
        failed_items=job.failed_items,
        progress=job.progress,
        params=job.params_json or {},
        result=job.result_json or {},
        error_code=job.error_code,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )