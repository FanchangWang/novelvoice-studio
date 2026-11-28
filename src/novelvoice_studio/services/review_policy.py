"""内容异常策略：诊断自动消化，系统故障与需要用户判断的内容问题分流。"""

from __future__ import annotations

from dataclasses import dataclass


# 这些记录保留在底层，便于排查模型输出，但不再作为待处理内容异常展示或阻断制作。
AUTO_ACCEPTED_CODES: frozenset[str] = frozenset(
    {
        "low_confidence_character",
        "timeline_fallback",
        "scene_llm_fallback",
        "scene_low_confidence",
        "scene_pov_unknown",
        "script_llm_fallback",
        "script_source_fill",
        "script_low_confidence",
        "segment_low_speaker_confidence",
        "segment_low_emotion_confidence",
        "shared_voice_conflict",
        "chapter_source_changed",
    }
)

SYSTEM_CODE_PREFIXES: tuple[str, ...] = (
    "cast_job_failed",
    "scene_job_failed",
    "script_job_failed",
    "unit_audio_failed",
    "interrupted",
    "llm_",
    "tts_",
)


@dataclass(frozen=True)
class ReviewPolicy:
    actionable: bool
    category: str
    impact: str
    recommended_action: str
    action_type: str


_CHAPTER_IMPACT = "该章节暂不进入后续分析、剧本与配音，其他章节可继续制作。"
_CHAPTER_ACTION = "请判断本章是否包含正文内容；确认后可纳入分析，或保留为番外/作者说明。"


def is_auto_accepted(code: str) -> bool:
    return code in AUTO_ACCEPTED_CODES or code.startswith(
        ("bible_note:", "scene_note:", "script_note:")
    )


def policy_for(code: str, *, category: str | None = None) -> ReviewPolicy:
    """返回用户可理解的处置策略；未知 code 保守保留，避免静默丢失真正问题。"""
    if category == "system" or code.startswith(SYSTEM_CODE_PREFIXES):
        return ReviewPolicy(
            False,
            "system",
            "这是一次执行故障，不影响小说内容本身。",
            "请在后台任务中查看错误并重试；任务恢复后会自动跳过已完成结果。",
            "task",
        )
    if is_auto_accepted(code):
        return ReviewPolicy(
            False,
            "content",
            "AI 已采用可用结果；此记录仅用于排查，不需要人工确认。",
            "无需处理。如后续修改对应章节，系统会重新分析。",
            "none",
        )
    if code in {"no_characters", "scene_no_characters"}:
        return ReviewPolicy(
            True,
            "content",
            _CHAPTER_IMPACT,
            _CHAPTER_ACTION,
            "chapter_classification",
        )
    if code in {"script_analysis_failed", "scene_analysis_failed"}:
        return ReviewPolicy(
            True,
            "content",
            "该章节的分析未形成可靠结果，但不会阻断其他章节。",
            "请先查看原文；可在章节页重新分析，或暂时排除该章节。",
            "chapter_retry",
        )

    policies: dict[str, tuple[str, str, str]] = {
        "alias_conflict": (
            "同一称谓可能对应不同角色，会让后续台词说话人和声线分配出现歧义。",
            "打开原文证据，确认该称谓属于哪位角色，再修正角色别名。",
            "character",
        ),
        "suspected_same_person": (
            "两个角色记录可能是同一人的不同称谓，继续分开会重复分配声线。",
            "对照原文证据，确认是同一人后合并角色，否则保留两个角色。",
            "character",
        ),
        "scene_unknown_character": (
            "场景中的未知角色无法稳定继承已有身份和声线，可能影响本章角色分配。",
            "查看场景原文，补充或确认角色身份后重新生成本章剧本。",
            "script",
        ),
        "scene_age_stage_conflict": (
            "同一角色在不同场景的年龄阶段不一致，可能导致不适龄声线被复用。",
            "查看两处原文，确认年龄阶段或修正角色信息后重新制作。",
            "script",
        ),
        "script_unresolved_speaker": (
            "这些台词无法可靠判断说话人，直接配音可能张冠李戴。",
            "打开剧本定位问题行，选择正确说话人；确认后继续生成音频。",
            "script_unit",
        ),
    }
    impact, action, action_type = policies.get(
        code,
        (
            "该内容问题可能影响剧本说话人、角色归属或后续配音。",
            "打开对应原文或剧本，核对问题并选择推荐处理方式。",
            "chapter",
        ),
    )
    return ReviewPolicy(True, "content", impact, action, action_type)
