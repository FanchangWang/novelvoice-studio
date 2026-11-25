from __future__ import annotations

from novelvoice_studio.models import AudioAsset, Character, Segment
from novelvoice_studio.services.paths import new_id
from novelvoice_studio.services.segment_ops import LOCKABLE_FIELDS


def _import_book(client, upload, sample_text: str, title: str = "片段测试") -> str:
    response = upload("/api/v1/books/import", sample_text, title=title)
    assert response.status_code == 201
    return response.json()["book"]["id"]


def _chapters(client, book_id: str) -> list[dict]:
    return client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]


def _add_character(session, book_id: str, name: str) -> Character:
    character = Character(
        id=new_id("char"),
        book_id=book_id,
        canonical_name=name,
        aliases_json=[],
        alias_candidates_json=[],
        gender="unknown",
        gender_basis="unknown",
        age_range="unknown",
        age_basis="unknown",
        character_type="unknown",
        personality_json=[],
        evidence_json=[],
        confidence=1.0,
        is_provisional=False,
        user_locked=False,
        merge_log_json=[],
    )
    session.add(character)
    return character


def _add_segment(
    session,
    book_id: str,
    chapter_id: str,
    index: int,
    *,
    speaker_type: str = "narrator",
    speaker_id: str | None = None,
    confidence: float = 1.0,
    review: str = "auto",
    emotion_confidence: float = 0.9,
    audio_status: str = "none",
) -> Segment:
    segment = Segment(
        id=new_id("seg"),
        book_id=book_id,
        chapter_id=chapter_id,
        segment_index=index,
        source_text=f"第{index}段原文",
        speech_text=f"第{index}段原文",
        segment_type="dialogue" if speaker_type != "narrator" else "narration",
        speaker_type=speaker_type,
        speaker_id=speaker_id,
        speaker_name_snapshot=None,
        speaker_confidence=confidence,
        emotion="neutral",
        emotion_confidence=emotion_confidence,
        intensity=1,
        delivery="normal",
        review_status=review,
        audio_status=audio_status,
        user_locked_fields_json=[],
        text_version=1,
        source_start_offset=index * 100,
        source_end_offset=index * 100 + 90,
        evidence_json=[],
    )
    session.add(segment)
    return segment


def _items(client, chapter_id: str, **params) -> list[dict]:
    response = client.get(f"/api/v1/chapters/{chapter_id}/segments", params=params)
    assert response.status_code == 200
    return response.json()["items"]


def test_segment_filters_and_flags(client, upload, sample_text: str, db_session) -> None:
    book_id = _import_book(client, upload, sample_text)
    chapter_id = _chapters(client, book_id)[0]["id"]
    character = _add_character(db_session, book_id, "林见秋")
    low = _add_segment(
        db_session,
        book_id,
        chapter_id,
        0,
        speaker_type="character",
        speaker_id=character.id,
        confidence=0.3,
        review="needs_review",
        emotion_confidence=0.2,
    )
    high = _add_segment(
        db_session,
        book_id,
        chapter_id,
        1,
        speaker_type="character",
        speaker_id=character.id,
        confidence=0.95,
        review="auto",
        emotion_confidence=0.9,
    )
    unknown = _add_segment(
        db_session,
        book_id,
        chapter_id,
        2,
        speaker_type="unknown",
        confidence=0.0,
        review="needs_review",
        emotion_confidence=0.1,
    )
    _add_segment(db_session, book_id, chapter_id, 3, speaker_type="narrator")
    db_session.commit()

    items = _items(client, chapter_id)
    assert [item["id"] for item in items] == [low.id, high.id, unknown.id, items[3]["id"]]
    assert items[0]["low_speaker_confidence"] is True
    assert items[0]["low_emotion_confidence"] is True
    assert items[1]["low_speaker_confidence"] is False
    assert items[1]["low_emotion_confidence"] is False

    low_items = _items(client, chapter_id, low_confidence="true")
    assert {item["id"] for item in low_items} == {low.id, unknown.id}
    assert _items(client, chapter_id, speaker_type="character") == [items[0], items[1]]
    assert {item["id"] for item in _items(client, chapter_id, review_status="needs_review")} == {
        low.id,
        unknown.id,
    }
    assert _items(client, chapter_id, segment_type="narration") == [items[3]]


def test_segment_update_locks_and_audio_stale(
    client, upload, sample_text: str, db_session
) -> None:
    book_id = _import_book(client, upload, sample_text)
    chapter_id = _chapters(client, book_id)[0]["id"]
    character = _add_character(db_session, book_id, "林见秋")
    segment = _add_segment(
        db_session,
        book_id,
        chapter_id,
        0,
        speaker_type="character",
        speaker_id=character.id,
        confidence=0.9,
        audio_status="ready",
    )
    db_session.add(
        AudioAsset(
            id=new_id("audio"),
            segment_id=segment.id,
            version=1,
            file_path="audio/fake.wav",
            sample_rate=24000,
            duration_ms=1000,
        )
    )
    db_session.commit()

    response = client.patch(
        f"/api/v1/segments/{segment.id}",
        json={"speech_text": "  人工改写  ", "emotion": "sad"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["speech_text"] == "人工改写"
    assert payload["text_version"] == 2
    assert payload["audio_status"] == "stale"
    assert set(payload["user_locked_fields"]) == {"speech_text", "emotion"}

    approved = client.patch(
        f"/api/v1/segments/{segment.id}", json={"review_status": "approved"}
    )
    assert approved.status_code == 200
    assert approved.json()["review_status"] == "approved"
    assert set(approved.json()["user_locked_fields"]) == set(LOCKABLE_FIELDS)

    unlocked = client.patch(
        f"/api/v1/segments/{segment.id}",
        json={"unlock_fields": ["emotion"], "speed": 1.25},
    )
    assert unlocked.status_code == 200
    locks = set(unlocked.json()["user_locked_fields"])
    assert "emotion" not in locks
    assert "speed" in locks
    assert locks == set(LOCKABLE_FIELDS) - {"emotion"}
    assert unlocked.json()["speed"] == 1.25

    bad_unlock = client.patch(
        f"/api/v1/segments/{segment.id}", json={"unlock_fields": ["not_a_field"]}
    )
    assert bad_unlock.status_code == 422
    assert bad_unlock.json()["error"]["code"] == "validation_error"

    rebind = client.patch(
        f"/api/v1/segments/{segment.id}",
        json={"speaker_type": "narrator", "speaker_id": character.id},
    )
    assert rebind.status_code == 422

    empty_text = client.patch(
        f"/api/v1/segments/{segment.id}", json={"speech_text": ""}
    )
    assert empty_text.status_code == 422

    missing = client.patch(f"/api/v1/segments/{new_id('seg')}", json={"emotion": "sad"})
    assert missing.status_code == 404
    assert client.get("/api/v1/segments/not-valid").status_code == 422


def test_segment_batch_update_same_chapter(client, upload, sample_text: str, db_session) -> None:
    book_id = _import_book(client, upload, sample_text)
    chapters = _chapters(client, book_id)
    first = _add_segment(db_session, book_id, chapters[0]["id"], 0)
    second = _add_segment(db_session, book_id, chapters[0]["id"], 1)
    other = _add_segment(db_session, book_id, chapters[1]["id"], 0)
    db_session.commit()

    response = client.post(
        "/api/v1/segments/batch-update",
        json={
            "segment_ids": [first.id, second.id, first.id],
            "update": {"emotion": "angry", "intensity": 3},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["updated_count"] == 2
    assert set(body["affected_segment_ids"]) == {first.id, second.id}

    first_items = _items(client, chapters[0]["id"])
    assert all(item["emotion"] == "angry" and item["intensity"] == 3 for item in first_items)

    cross = client.post(
        "/api/v1/segments/batch-update",
        json={"segment_ids": [first.id, other.id], "update": {"emotion": "sad"}},
    )
    assert cross.status_code == 422
    assert cross.json()["error"]["code"] == "validation_error"

    missing = client.post(
        "/api/v1/segments/batch-update",
        json={"segment_ids": [first.id, new_id("seg")], "update": {"emotion": "sad"}},
    )
    assert missing.status_code == 404


def test_segment_speaker_binding_validation(client, upload, sample_text: str, db_session) -> None:
    book_id = _import_book(client, upload, sample_text)
    other_book = _import_book(client, upload, sample_text, title="另一本片段书")
    chapter_id = _chapters(client, book_id)[0]["id"]
    local = _add_character(db_session, book_id, "本地角色")
    foreign = _add_character(db_session, other_book, "外书角色")
    segment = _add_segment(db_session, book_id, chapter_id, 0, speaker_type="unknown")
    db_session.commit()

    cross = client.patch(
        f"/api/v1/segments/{segment.id}", json={"speaker_id": foreign.id}
    )
    assert cross.status_code == 422
    assert "不属于当前书籍" in cross.json()["error"]["message"]

    bound = client.patch(
        f"/api/v1/segments/{segment.id}", json={"speaker_id": local.id}
    )
    assert bound.status_code == 200
    assert bound.json()["speaker_type"] == "character"
    assert bound.json()["speaker_id"] == local.id
    assert bound.json()["speaker_confidence"] == 1.0
    assert {"speaker_type", "speaker_id"} <= set(bound.json()["user_locked_fields"])

    cleared = client.patch(
        f"/api/v1/segments/{segment.id}", json={"speaker_type": "unknown"}
    )
    assert cleared.status_code == 200
    assert cleared.json()["speaker_type"] == "unknown"
    assert cleared.json()["speaker_id"] is None