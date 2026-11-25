"""阶段 3：音色库、单段生成与音频版本（Mock Provider）。"""

from __future__ import annotations

from pathlib import Path

from novelvoice_studio.models import AudioAsset, Character, Segment
from novelvoice_studio.services.paths import new_id


def _import_book(client, upload, sample_text: str, title: str = "声音测试") -> str:
    response = upload("/api/v1/books/import", sample_text, title=title)
    assert response.status_code == 201
    return response.json()["book"]["id"]


def _chapter_id(client, book_id: str) -> str:
    return client.get(f"/api/v1/books/{book_id}/chapters").json()["items"][0]["id"]


def _add_character(session, book_id: str, name: str, **overrides) -> Character:
    character = Character(
        id=new_id("char"),
        book_id=book_id,
        canonical_name=name,
        aliases_json=[],
        alias_candidates_json=[],
        gender=overrides.get("gender", "male"),
        gender_basis="explicit",
        age_range=overrides.get("age_range", "adult"),
        age_basis="inferred",
        character_type="human",
        personality_json=[],
        personality_desc=overrides.get("personality_desc"),
        voice_description=overrides.get("voice_description"),
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
    speech_text: str = "这是用于生成语音的测试片段。",
    emotion: str = "neutral",
) -> Segment:
    segment = Segment(
        id=new_id("seg"),
        book_id=book_id,
        chapter_id=chapter_id,
        segment_index=index,
        source_text=speech_text,
        speech_text=speech_text,
        segment_type="dialogue" if speaker_type == "character" else "narration",
        speaker_type=speaker_type,
        speaker_id=speaker_id,
        speaker_name_snapshot=None,
        speaker_confidence=1.0,
        emotion=emotion,
        emotion_confidence=1.0,
        intensity=1,
        delivery="normal",
        review_status="auto",
        audio_status="none",
        user_locked_fields_json=[],
        text_version=1,
        source_start_offset=index * 100,
        source_end_offset=index * 100 + 90,
        evidence_json=[],
    )
    session.add(segment)
    return segment


def _library(client, book_id: str) -> dict:
    response = client.get(f"/api/v1/books/{book_id}/voices")
    assert response.status_code == 200
    return response.json()


def test_voice_design_select_preview_and_delete(
    client, upload, sample_text: str, db_session, settings
) -> None:
    book_id = _import_book(client, upload, sample_text)
    first = _add_character(db_session, book_id, "林见秋")
    second = _add_character(db_session, book_id, "顾临舟", gender="female", age_range="young_adult")
    db_session.commit()

    library = _library(client, book_id)
    assert library["narrator"]["owner_id"] == book_id
    assert library["narrator"]["has_selected"] is False
    assert {item["owner_id"] for item in library["characters"]} == {first.id, second.id}
    assert set(library["characters_without_voice"]) == {"林见秋", "顾临舟"}

    design = client.post(
        f"/api/v1/books/{book_id}/voices/design",
        json={"owner_type": "narrator", "owner_id": book_id, "candidates": 2},
    )
    assert design.status_code == 200
    payload = design.json()
    assert payload["requested"] == 2
    assert len(payload["items"]) == 2
    assert payload["errors"] == []
    assert all(item["status"] == "draft" and not item["is_selected"] for item in payload["items"])
    assert all(item["is_mock"] is True for item in payload["items"])
    candidate_id = payload["items"][0]["id"]
    candidate_path = (
        settings.data_dir
        / "books"
        / book_id
        / "voices"
        / f"narrator_{book_id}"
        / "candidates"
        / f"{candidate_id}.wav"
    )
    assert candidate_path.is_file()

    audio = client.get(f"/api/v1/voices/{candidate_id}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content[:4] == b"RIFF"

    selected = client.post(f"/api/v1/voices/{candidate_id}/select")
    assert selected.status_code == 200
    selected_body = selected.json()
    assert selected_body["is_selected"] is True
    assert selected_body["status"] == "ready"
    assert selected_body["owner_name"] == "旁白"
    assert selected_body["generation_params"]["reference_processed"] is False

    library = _library(client, book_id)
    assert library["narrator"]["has_selected"] is True
    assert library["narrator"]["selected_profile_id"] == candidate_id
    assert library["selected_owners"] == 1

    preview = client.post(
        f"/api/v1/voices/{candidate_id}/preview",
        json={"text": "我等你很久了。", "emotion": "sad", "intensity": 2},
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("audio/wav")
    assert preview.headers["X-NVS-Is-Mock"] == "true"
    assert preview.content[:4] == b"RIFF"

    blocked = client.delete(f"/api/v1/voices/{candidate_id}")
    assert blocked.status_code == 409
    assert "已绑定" in blocked.json()["error"]["message"]

    draft_id = payload["items"][1]["id"]
    removed = client.delete(f"/api/v1/voices/{draft_id}")
    assert removed.status_code == 204
    assert client.get(f"/api/v1/voices/{draft_id}/audio").status_code == 404

    character_design = client.post(
        f"/api/v1/books/{book_id}/voices/design",
        json={
            "owner_type": "character",
            "owner_id": first.id,
            "description": "青年男性，声音清亮，咬字干净",
            "candidates": 1,
        },
    )
    assert character_design.status_code == 200
    character_item = character_design.json()["items"][0]
    assert character_item["owner_name"] == "林见秋"
    assert character_item["description"] == "青年男性，声音清亮，咬字干净"
    assert client.post(f"/api/v1/voices/{character_item['id']}/select").status_code == 200

    library = _library(client, book_id)
    assert library["selected_owners"] == 2
    assert library["characters_without_voice"] == ["顾临舟"]

    foreign_book = client.post(
        f"/api/v1/books/{book_id}/voices/design",
        json={"owner_type": "character", "owner_id": new_id("char")},
    )
    assert foreign_book.status_code == 422


def test_segment_audio_generation_versions_and_missing_voice(
    client, upload, sample_text: str, db_session, settings
) -> None:
    book_id = _import_book(client, upload, sample_text)
    chapter_id = _chapter_id(client, book_id)
    character = _add_character(db_session, book_id, "林见秋")
    narrator_segment = _add_segment(db_session, book_id, chapter_id, 0)
    character_segment = _add_segment(
        db_session,
        book_id,
        chapter_id,
        1,
        speaker_type="character",
        speaker_id=character.id,
        speech_text="你答应过今天一定回来。",
        emotion="sad",
    )
    db_session.commit()

    missing = client.post(f"/api/v1/segments/{character_segment.id}/generate")
    assert missing.status_code == 409
    error = missing.json()["error"]
    assert error["details"]["owner_name"] == "林见秋"
    assert "固定音色" in error["message"]

    narrator = client.post(
        f"/api/v1/books/{book_id}/voices/design",
        json={"owner_type": "narrator", "owner_id": book_id, "candidates": 1},
    )
    assert narrator.status_code == 200
    narrator_voice = narrator.json()["items"][0]["id"]
    assert client.post(f"/api/v1/voices/{narrator_voice}/select").status_code == 200

    first = client.post(f"/api/v1/segments/{narrator_segment.id}/generate")
    assert first.status_code == 200
    body = first.json()
    assert body["audio_status"] == "ready"
    assert body["voice_profile_id"] == narrator_voice
    asset_v1 = body["asset"]
    assert asset_v1["version"] == 1
    assert asset_v1["is_current"] is True
    assert asset_v1["duration_ms"] > 0
    assert asset_v1["tts_provider"] == "mock_tts"
    assert asset_v1["audio_url"].endswith("/audio")
    file_v1 = settings.data_dir / "books" / book_id / "audio" / chapter_id / narrator_segment.id / "v001.wav"
    assert file_v1.is_file()
    assert file_v1.read_bytes()[:4] == b"RIFF"

    detail = client.get(f"/api/v1/segments/{narrator_segment.id}").json()
    assert detail["current_audio_asset_id"] == asset_v1["id"]
    assert detail["voice_profile_id"] == narrator_voice

    direct = client.get(f"/api/v1/segments/{narrator_segment.id}/audio")
    assert direct.status_code == 200
    assert direct.content[:4] == b"RIFF"

    second = client.post(f"/api/v1/segments/{narrator_segment.id}/generate")
    assert second.status_code == 200
    asset_v2 = second.json()["asset"]
    assert asset_v2["version"] == 2
    assert asset_v2["id"] != asset_v1["id"]

    versions = client.get(f"/api/v1/segments/{narrator_segment.id}/audio-versions")
    assert versions.status_code == 200
    versions_body = versions.json()
    assert versions_body["total"] == 2
    assert versions_body["current_asset_id"] == asset_v2["id"]
    assert [item["version"] for item in versions_body["items"]] == [2, 1]

    restored = client.post(f"/api/v1/audio-assets/{asset_v1['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["asset"]["is_current"] is True
    assert restored.json()["asset"]["version"] == 1

    asset_audio = client.get(f"/api/v1/audio-assets/{asset_v1['id']}/audio")
    assert asset_audio.status_code == 200
    assert asset_audio.content[:4] == b"RIFF"

    selected_voice = client.delete(f"/api/v1/voices/{narrator_voice}")
    assert selected_voice.status_code == 409
    assert "已绑定" in selected_voice.json()["error"]["message"]

    replacement = client.post(
        f"/api/v1/books/{book_id}/voices/design",
        json={"owner_type": "narrator", "owner_id": book_id, "candidates": 1},
    )
    assert replacement.status_code == 200
    replacement_id = replacement.json()["items"][0]["id"]
    assert client.post(f"/api/v1/voices/{replacement_id}/select").status_code == 200
    used_voice = client.delete(f"/api/v1/voices/{narrator_voice}")
    assert used_voice.status_code == 409
    assert "片段" in used_voice.json()["error"]["message"]

    character_voice = client.post(
        f"/api/v1/books/{book_id}/voices/design",
        json={"owner_type": "character", "owner_id": character.id, "candidates": 1},
    )
    assert character_voice.status_code == 200
    character_voice_id = character_voice.json()["items"][0]["id"]
    assert (
        client.post(f"/api/v1/voices/{character_voice_id}/select").status_code == 200
    )
    character_audio = client.post(f"/api/v1/segments/{character_segment.id}/generate")
    assert character_audio.status_code == 200
    assert character_audio.json()["voice_profile_id"] == character_voice_id
    assert character_audio.json()["asset"]["version"] == 1

    assert client.post("/api/v1/segments/not-valid/generate").status_code == 422
    assert client.get(f"/api/v1/audio-assets/{new_id('asset')}/audio").status_code == 404


def test_same_character_uses_two_emotions_with_one_voice(
    client, upload, sample_text: str, db_session, settings
) -> None:
    book_id = _import_book(client, upload, sample_text, title="情绪测试")
    chapter_id = _chapter_id(client, book_id)
    character = _add_character(db_session, book_id, "林见秋")
    happy = _add_segment(
        db_session,
        book_id,
        chapter_id,
        0,
        speaker_type="character",
        speaker_id=character.id,
        speech_text="今天天气真好。",
        emotion="happy",
    )
    sad = _add_segment(
        db_session,
        book_id,
        chapter_id,
        1,
        speaker_type="character",
        speaker_id=character.id,
        speech_text="你答应过今天一定回来。",
        emotion="sad",
    )
    db_session.commit()

    design = client.post(
        f"/api/v1/books/{book_id}/voices/design",
        json={
            "owner_type": "character",
            "owner_id": character.id,
            "description": "年轻女性，声音清亮，咬字干净",
            "candidates": 1,
        },
    )
    assert design.status_code == 200
    voice_id = design.json()["items"][0]["id"]
    assert client.post(f"/api/v1/voices/{voice_id}/select").status_code == 200

    happy_audio = client.post(f"/api/v1/segments/{happy.id}/generate")
    sad_audio = client.post(f"/api/v1/segments/{sad.id}/generate")
    assert happy_audio.status_code == 200
    assert sad_audio.status_code == 200
    assert happy_audio.json()["voice_profile_id"] == voice_id
    assert sad_audio.json()["voice_profile_id"] == voice_id
    assert happy_audio.json()["asset"]["id"] != sad_audio.json()["asset"]["id"]

    db_session.expire_all()
    assets = {
        item.segment_id: item
        for item in db_session.query(AudioAsset)
        .filter(AudioAsset.segment_id.in_([happy.id, sad.id]))
        .all()
    }
    assert assets[happy.id].generation_params_json["emotion"] == "happy"
    assert assets[sad.id].generation_params_json["emotion"] == "sad"
    assert (
        settings.data_dir / "books" / book_id / "audio" / chapter_id / happy.id / "v001.wav"
    ).is_file()
    assert (
        settings.data_dir / "books" / book_id / "audio" / chapter_id / sad.id / "v001.wav"
    ).is_file()


def test_speaker_change_clears_voice_binding(client, upload, sample_text: str, db_session) -> None:
    book_id = _import_book(client, upload, sample_text)
    chapter_id = _chapter_id(client, book_id)
    character = _add_character(db_session, book_id, "林见秋")
    segment = _add_segment(
        db_session,
        book_id,
        chapter_id,
        0,
        speaker_type="character",
        speaker_id=character.id,
    )
    segment.voice_profile_id = new_id("voice")
    db_session.commit()

    response = client.patch(
        f"/api/v1/segments/{segment.id}", json={"speaker_type": "narrator"}
    )
    assert response.status_code == 200
    assert response.json()["speaker_type"] == "narrator"
    assert response.json()["voice_profile_id"] is None